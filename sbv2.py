#!/usr/bin/env python3
"""Style-BERT-VITS2 (JP-Extra) text-to-speech — 例文とクイズの音声を作る。

`voicevox.py`・`tts.py` と同じ speak_page() の形をしているので、build_page.py からは
`--tts-engine sbv2` で入れ替わる。**このファイル自体は標準ライブラリだけ**で、重い側
（torch・transformers・pyopenjtalk）は `.sbv2venv` の中の `sbv2_worker.py` にある。

なぜ足したか（2026-08-31、学習者の希望）:

- 声がVOICEVOXより自然（JVNVコーパスの4声。CC BY-SA 4.0）。
- **読みをふりがなから押し込める。** infer() の given_phone/given_tone に、ページの
  ふりがなから作った音素を渡す。Style-BERT-VITS2の読みはVOICEVOXと同じOpenJTalkなので、
  放っておけば同じ間違いをする（実測：猿人→サルジン、はち→ワチ、机の角→ツクエノカク、
  三羽→サンワ、翼幅→ツバサハバ、目覚まし時計→メザマシトケー）。ふりがなはjishoで
  確認済みなので、そちらを正解として渡す。**VOICEVOXで14語育てたユーザー辞書に相当する
  ものが要らない。**

代わりに失うもの:

- **遅い。** M3で1文2秒、120文で約4分（VOICEVOXは75秒）。
- **重い。** BERT込みでピーク1.5GB。モデルとBERTでディスク約2.6GB。
- **合成前に読みを見せてくれない。** VOICEVOXの /audio_query に当たるものが無いので、
  「読みが違う」の検査は**ふりがなを渡している以上そもそも起きない**が、
  ふりがな自体が間違っていればそのまま読まれる。ふりがなの確認（4.5節）がその分だけ重い。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent

VENV_PYTHON = HERE / ".sbv2venv" / "bin" / "python"
WORKER = HERE / "sbv2_worker.py"
MODEL_ROOT = HERE / ".sbv2_models"
CACHE_DIR = HERE / ".sbv2_cache"
BERT = "ku-nlp/deberta-v2-large-japanese-char-wwm"

# JVNVコーパスの4声（女2・男2）。VOICEVOX・ElevenLabsのときと同じ組み方で、
# 1ページを1つの声で通さない。litagin/style_bert_vits2_jvnv、CC BY-SA 4.0。
SPEAKERS = {
    "JVNV-F1": "jvnv-F1-jp",
    "JVNV-F2": "jvnv-F2-jp",
    "JVNV-M1": "jvnv-M1-jp",
    "JVNV-M2": "jvnv-M2-jp",
}
DEFAULT_VOICE = "JVNV-F1,JVNV-F2,JVNV-M1,JVNV-M2"
DEFAULT_FORMAT = "m4a"
# MPSはこのサイズのモデルでは遅い（実測 6.0s/文 対 CPU 2.0s/文）。既定はCPU。
DEFAULT_DEVICE = "cpu"
TIMEOUT = 3600


class SBV2Error(RuntimeError):
    """Anything that means "no audio from Style-BERT-VITS2 this run"."""


def available() -> str:
    """"" when the venv or the models are not in place, else a version-ish stamp.

    Used both to fail politely and as part of the cache key.
    """
    if not VENV_PYTHON.exists() or not WORKER.exists():
        return ""
    dirs = sorted(d.name for d in MODEL_ROOT.glob("*-jp") if d.is_dir())
    return f"sbv2:{'+'.join(dirs)}" if dirs else ""


def resolve_speaker(name: str) -> Path:
    """A speaker name to its model directory."""
    key = name.strip()
    if key in SPEAKERS:
        return MODEL_ROOT / SPEAKERS[key]
    for known, folder in SPEAKERS.items():
        if key and key.lower() in known.lower():
            return MODEL_ROOT / folder
    raise SBV2Error(f"知らない話者です: {name}（{'・'.join(SPEAKERS)}）")


def cache_key(text: str, speaker: str, stamp: str, fmt: str, reading: str = "") -> str:
    """The name a clip is filed under.

    The reading is in it: the same sentence read off a corrected furigana is a
    different clip, and a furigana fix has to reach the audio.
    """
    return hashlib.sha1("\n".join([speaker, stamp, fmt, reading, text])
                        .encode("utf-8")).hexdigest()


def assign_voices(texts: list[str], voices: list[str],
                  overrides: dict[str, str] | None = None) -> dict[str, str]:
    """text -> speaker name, from a hash so a rebuild reuses the cache."""
    if not voices:
        return {}
    overrides = overrides or {}
    picked = {}
    for text in texts:
        if overrides.get(text) in voices:
            picked[text] = overrides[text]
            continue
        picked[text] = voices[hashlib.sha1(text.encode("utf-8")).digest()[0] % len(voices)]
    return picked


def audio_dir_for(page: Path) -> Path:
    return page.with_name(f"{page.stem}_audio")


def synthesize_all(jobs: list[dict], *, device: str = DEFAULT_DEVICE) -> dict[str, str]:
    """Run the worker once for every sentence that is not already cached.

    One subprocess for the whole page: loading the BERT and a voice costs more
    than the synthesis of a single sentence, so per-sentence calls would double
    the build time for nothing.
    """
    if not jobs:
        return {}
    payload = json.dumps({"items": jobs, "bert": BERT, "device": device},
                         ensure_ascii=False)
    try:
        done = subprocess.run([str(VENV_PYTHON), str(WORKER)], input=payload,
                              capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as error:
        raise SBV2Error(f"合成プロセスを動かせません: {error}") from None
    if done.returncode != 0:
        raise SBV2Error(f"合成プロセスが落ちました: {done.stderr.strip()[-300:]}")
    try:
        results = json.loads(done.stdout.strip().splitlines()[-1])["results"]
    except (ValueError, IndexError, KeyError):
        raise SBV2Error(f"合成プロセスの返事が読めません: {done.stdout[-200:]}") from None
    made = {}
    for row in results:
        if row.get("ok"):
            made[row["text"]] = row["out"]
        else:
            print(f"  音声を作れませんでした: {row['text'][:24]}… — {row.get('error')}",
                  file=sys.stderr)
    return made


def speak_page(texts: list[str], page: Path, *, voice: str = DEFAULT_VOICE,
               cache_dir: Path = CACHE_DIR, fmt: str = DEFAULT_FORMAT,
               readings: dict[str, str] | None = None,
               overrides: dict[str, str] | None = None,
               device: str = DEFAULT_DEVICE
               ) -> tuple[dict[str, dict[str, str]], str]:
    """Same contract as voicevox.speak_page / tts.speak_page.

    `readings` maps a sentence to the kana it should be read as — the page's own
    furigana, via build_page.speech_kana(). A sentence without one is left to
    OpenJTalk, which is where the known misreadings come from, so the caller
    should always pass it.
    """
    import voicevox                                   # to_m4a を借りる（同じ変換）

    texts = [t for t in dict.fromkeys(texts) if t.strip()]
    if not texts:
        return {}, ""
    stamp = available()
    if not stamp:
        return {}, ("音声なし（Style-BERT-VITS2が入っていません。"
                    f"{VENV_PYTHON} と {MODEL_ROOT} を確認してください）")
    names = [n.strip() for n in voice.split(",") if n.strip()]
    try:
        for name in names:
            resolve_speaker(name)
    except SBV2Error as error:
        return {}, f"音声なし（{error}）"
    if not names:
        return {}, "音声なし（話者が指定されていません）"

    readings = readings or {}
    pairs = assign_voices(texts, names, overrides)
    cache_dir.mkdir(parents=True, exist_ok=True)

    clips: dict[str, tuple[bytes, str]] = {}
    jobs = []
    wanted: dict[str, tuple[str, Path]] = {}
    for text in texts:
        speaker = pairs[text]
        key = cache_key(text, speaker, stamp, fmt, readings.get(text, ""))
        for suffix in ("m4a", "wav"):
            cached = cache_dir / f"{key}.{suffix}"
            if cached.exists():
                clips[text] = (cached.read_bytes(), suffix)
                break
        else:
            raw = cache_dir / f"{key}.raw.wav"
            wanted[text] = (key, raw)
            jobs.append({"text": text, "reading": readings.get(text, ""),
                         "model_dir": str(resolve_speaker(speaker)), "out": str(raw)})

    if jobs:
        try:
            made = synthesize_all(jobs, device=device)
        except SBV2Error as error:
            return {}, f"音声なし（{error}）"
        for text, (key, raw) in wanted.items():
            if text not in made or not raw.exists():
                continue
            audio, suffix = (voicevox.to_m4a(raw.read_bytes()) if fmt == "m4a"
                             else (raw.read_bytes(), "wav"))
            (cache_dir / f"{key}.{suffix}").write_bytes(audio)
            raw.unlink(missing_ok=True)
            clips[text] = (audio, suffix)

    out_dir = audio_dir_for(page)
    urls: dict[str, dict[str, str]] = {}
    try:
        if clips:
            out_dir.mkdir(parents=True, exist_ok=True)
        for text, (audio, suffix) in clips.items():
            speaker = pairs[text]
            key = cache_key(text, speaker, stamp, fmt, readings.get(text, ""))
            filename = f"{key[:12]}.{suffix}"
            (out_dir / filename).write_bytes(audio)
            urls[text] = {"src": f"{urllib.parse.quote(out_dir.name)}/{filename}",
                          "voice": speaker}
    except OSError as error:
        return {}, f"音声なし（書き出せません: {error}）"

    tally: dict[str, int] = {}
    for clip in urls.values():
        tally[clip["voice"]] = tally.get(clip["voice"], 0) + 1
    spread = "・".join(f"{name} {n}文" for name, n in sorted(tally.items()))
    forced = sum(1 for t in urls if readings.get(t))
    note = (f"音声 {len(urls)}/{len(texts)}文（{spread or voice}・Style-BERT-VITS2 JP-Extra）"
            f"／ふりがな通りに読ませた {forced}文")
    if len(urls) < len(texts):
        note += f"／失敗 {len(texts) - len(urls)}文"
    return urls, note


def credit(urls: dict[str, dict[str, str]]) -> str:
    """JVNVモデルは CC BY-SA 4.0。出所を書く。"""
    if not urls:
        return ""
    names = sorted({clip["voice"] for clip in urls.values()})
    return ("音声：Style-BERT-VITS2 JP-Extra／JVNV"
            f"（{'・'.join(names)}・CC BY-SA 4.0）")


def main() -> int:
    parser = argparse.ArgumentParser(description="Style-BERT-VITS2の音声を確かめる")
    parser.add_argument("--check", action="store_true", help="venvとモデルの有無を見る")
    parser.add_argument("--say", help="この文を読み上げる")
    parser.add_argument("--reading", default="",
                        help="ふりがな（かな）。渡すとこの読みで読ませる")
    parser.add_argument("--voice", default="JVNV-F1")
    parser.add_argument("--out", type=Path, default=Path("sbv2_test.m4a"))
    parser.add_argument("--device", default=DEFAULT_DEVICE, choices=["cpu", "mps"])
    parser.add_argument("--cache", type=Path, default=CACHE_DIR)
    args = parser.parse_args()

    stamp = available()
    if args.check or not args.say:
        print(f"venv   : {VENV_PYTHON}（{'あり' if VENV_PYTHON.exists() else 'なし'}）")
        print(f"モデル : {MODEL_ROOT}")
        for name, folder in SPEAKERS.items():
            here = (MODEL_ROOT / folder).is_dir()
            print(f"  {name:8} {folder:12} {'あり' if here else 'なし'}")
        print(f"stamp  : {stamp or '（使えません）'}")
        return 0 if stamp else 1

    urls, note = speak_page([args.say], args.out.with_suffix(".html"), voice=args.voice,
                            cache_dir=args.cache,
                            readings={args.say: args.reading} if args.reading else None,
                            device=args.device)
    print(note)
    for text, clip in urls.items():
        print(f"  {clip['voice']}: {clip['src']}")
    return 0 if urls else 1


if __name__ == "__main__":
    raise SystemExit(main())
