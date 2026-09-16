#!/usr/bin/env python3
"""VOICEVOX text-to-speech for the practice pages — 例文とクイズの音声を作る。

tts.py（ElevenLabs）の置き換え。speak_page() の呼び出し方と戻り値はそちらと同じ
なので、build_page.py は --tts-engine で入れ替えるだけでよい。ElevenLabs版は
消していない：VOICEVOXのエンジンが動かない朝は自動でそちらに落ちるし、
--tts-engine elevenlabs と書けばいつでも戻せる。

VOICEVOXにした理由（2026-08-31）:

- **読みが先に分かる。** /audio_query が合成前に読みをカナで返す
  （祖父は類人猿… -> ソ'フワ/ルイジ'ンエンノ/…）。ElevenLabsでは音を作ってから
  聞き取り直して確かめるしかなく、しかもScribeが文脈から復元してしまうので
  「机」が潰れていても「机」と返ってきた。ここでは読みは推測ではなく事実で、
  ページのふりがなと突き合わせれば済む。
- **直せる。** 読みが違えばユーザー辞書に登録する（--teach）。以後その語は
  どの文でも正しく読まれる。ElevenLabsでは引き直すか声を替えるしかなかった。
- **同じ入力なら同じ音が出る。** 引き直しという概念が要らない。
- **出だしが切れない。** prePhonemeLength が既定で100msの無音を置く。
  ElevenLabsで「机」が「くえ」に聞こえた問題は、ここでは起きようがない。
- **無料で、外に出ない。** 1日120文をローカルで作るので課金もネットも要らない。

エンジンはVOICEVOX.appに同梱されている。アプリを開いていれば動いているし、
いなければ ensure_engine() が同梱のバイナリを起こす。

利用規約により、音声を配るときは**VOICEVOXを使ったと分かるクレジット表記**が要る
（https://voicevox.hiroshiba.jp/term/）。ページのフッターに話者名込みで出している。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

ENGINE = "http://127.0.0.1:50021"
# VOICEVOX.app に同梱されているエンジン本体。アプリを開かなくてもこれだけで動く
# ので、launchdからの9時のビルドはアプリのウィンドウを出さずに済む。
ENGINE_BINARY = Path("/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run")

# 話者。男女2人ずつ、いずれも素直な読み上げ調のものを選んである（ElevenLabsで
# 4声を回していたのと同じ理由：一つの声で1ページ通すと単調で、同じ語を別の声で
# 聞くほうが実際の日本語に近い）。名前はページに出るので、学習者が「この声が
# 聞きやすい」と言えるようにしてある。
SPEAKERS = {
    "四国めたん": 2,
    "春日部つむぎ": 8,
    "玄野武宏": 11,
    "青山龍星": 13,
}
DEFAULT_VOICE = "四国めたん,春日部つむぎ,玄野武宏,青山龍星"
DEFAULT_WORKERS = 4          # ローカルなので上限はCPU。課金の心配はない
DEFAULT_FORMAT = "m4a"       # wav をAACに詰め直す。下の to_m4a() を見よ
CACHE_DIR = HERE / ".vv_cache"

TIMEOUT = 120
RETRIES = 3
START_WAIT = 40              # エンジンを起こしてから応答するまで待つ秒数


class VoiceVoxError(RuntimeError):
    """Anything that means "no audio from VOICEVOX this run". Callers catch it
    and either fall back to ElevenLabs or build the page without audio."""


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def _request(path: str, *, query: dict | None = None, body: object = None,
             accept: str = "application/json", timeout: int = TIMEOUT,
             method: str = "") -> bytes:
    url = f"{ENGINE}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": accept}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last = ""
    for attempt in range(RETRIES):
        verb = method or ("POST" if (data is not None or query is not None)
                          and path != "/version" else "GET")
        request = urllib.request.Request(url, data=data, headers=headers, method=verb)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:400]
            raise VoiceVoxError(f"HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last = f"エンジンに繋がりません: {error}"
            if attempt < RETRIES - 1:
                time.sleep(1 + attempt)
    raise VoiceVoxError(last)


def version() -> str:
    """The running engine's version, or "" when nothing is listening."""
    try:
        return json.loads(_request("/version", timeout=5))
    except (VoiceVoxError, ValueError):
        return ""


def ensure_engine(binary: Path = ENGINE_BINARY, wait: int = START_WAIT) -> str:
    """The engine's version, starting it first if nothing is listening.

    VOICEVOX.app being open is the usual reason it is already up. At 9:00 under
    launchd it will not be, so the bundled binary is started headless. Raises
    when there is no engine to start — the caller then falls back to ElevenLabs
    rather than losing the day's audio.
    """
    running = version()
    if running:
        return running
    if not binary.exists():
        raise VoiceVoxError(f"エンジンが見つかりません（{binary}）。"
                            "VOICEVOXをインストールするか、アプリを開いてください")
    try:
        subprocess.Popen([str(binary)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as error:
        raise VoiceVoxError(f"エンジンを起動できません: {error}") from None
    for _ in range(wait):
        time.sleep(1)
        running = version()
        if running:
            return running
    raise VoiceVoxError(f"エンジンを起動しましたが{wait}秒で応答しませんでした")


def resolve_speaker(name: str) -> int:
    """A speaker name to its style id. Digits pass straight through."""
    name = name.strip()
    if name.isdigit():
        return int(name)
    if name in SPEAKERS:
        return SPEAKERS[name]
    for known, style in SPEAKERS.items():          # 部分一致も許す（ずんだ -> ずんだもん）
        if name and name in known:
            return style
    raise VoiceVoxError(f"知らない話者です: {name}（{'・'.join(SPEAKERS)}）")


# --------------------------------------------------------------------------- #
# Readings
# --------------------------------------------------------------------------- #

_HIRA_TO_KATA = {chr(c): chr(c + 0x60) for c in range(0x3041, 0x3097)}
# 長音をどう書くかは流儀が分かれる。エンジンは「ゆうめい」を ユウメエ と書き、
# ページのふりがなは ゆうめい と書く。同じ音なので、比べる前に片方に寄せる。
_LONG = {"エ": "エ", "ケ": "エ", "セ": "エ", "テ": "エ", "ネ": "エ", "ヘ": "エ",
         "メ": "エ", "レ": "エ", "ゲ": "エ", "ゼ": "エ", "デ": "エ", "ベ": "エ",
         "ペ": "エ"}
_LONG_O = {"オ": "オ", "コ": "オ", "ソ": "オ", "ト": "オ", "ノ": "オ", "ホ": "オ",
           "モ": "オ", "ヨ": "オ", "ロ": "オ", "ゴ": "オ", "ゾ": "オ", "ド": "オ",
           "ボ": "オ", "ポ": "オ", "ョ": "オ"}
_VOWEL = {"ア": "ア", "イ": "イ", "ウ": "ウ", "エ": "エ", "オ": "オ"}


def katakana(text: str) -> str:
    """ひらがな -> カタカナ。他の文字はそのまま。"""
    return "".join(_HIRA_TO_KATA.get(c, c) for c in str(text))


def _strip(text: str) -> str:
    """Accent marks, phrase breaks and punctuation gone; ぢ/づ folded onto じ/ず
    (the same sound, and the two sides do not always spell them alike)."""
    text = katakana(text)
    text = re.sub(r"['/_、。，．,.!！?？「」『』（）()…・\s]", "", text)
    return text.replace("ヂ", "ジ").replace("ヅ", "ズ").replace("ヲ", "オ")


def canon_engine(kana: str) -> str:
    """The engine's reading, ready to compare.

    Only the marks come off. The engine already writes long vowels the way they
    sound — ユウメエ, ビョオイン, サンカッケエ — so there is nothing to fold, and
    folding anyway is a bug: it ran across word boundaries and ate the very
    syllable being looked for（ので|いそいで -> ノデエソイデ, and 急/イソ then
    looked missing）.
    """
    out: list[str] = []
    for char in _strip(kana):
        if char == "ー" and out:
            out.append(_VOWEL.get(out[-1], ""))
            continue
        out.append(char)
    return "".join(out)


def page_variants(reading: str) -> set[str]:
    """Both ways the page's reading might be spelled, as a set to look for.

    The page writes ゆうめい; the engine usually says ユウメエ but writes ドケイ
    when the reading came from the user dictionary, where it was typed with イ.
    Rather than folding the engine's side — which ran across word boundaries and
    ate the syllable being looked for（ので|いそいで -> ノデエソイデ）— both
    spellings of the page's own reading are accepted. It is one word here, with
    no boundary to fold across.

    折り込むかどうかは**直前の素のかな**で決める。折り込んだ側を見ていると連鎖
    する：せいいき は セ→エ の次の イ が「直前はエ＝_LONGだ」と見て セエエキ に
    なり、エンジンの言う セエイキ と食い違って、正しいページを誤りと報告していた
    （2026-09-08に発覚。こううん -> コオオン も同じ）。
    """
    plain: list[str] = []
    folded: list[str] = []
    for char in _strip(reading):
        if char == "ー" and plain:
            plain.append(_VOWEL.get(plain[-1], ""))
            folded.append(_VOWEL.get(folded[-1], "") if folded else "")
            continue
        prev = plain[-1] if plain else ""      # 直前の「素の」かな。折り込んだ側を
        plain.append(char)                     # 見ると連鎖して食い過ぎる（下記）
        if char == "イ" and prev in _LONG:
            folded.append("エ")                     # けい -> ケエ
        elif char == "ウ" and prev in _LONG_O:
            folded.append("オ")                     # こう -> コオ
        else:
            folded.append(char)
    return {"".join(plain), "".join(folded)}


def reading_misses(checks: list[tuple[str, str]], heard: str) -> list[str]:
    """Which of a sentence's words the engine is about to read wrong.

    `checks` are the page's own (書き方, ふりがな) pairs — the readings step 4
    verified on jisho — and `heard` is what /audio_query says the engine will
    say. Unlike the ElevenLabs version this is not a guess about audio: it is
    the engine's reading, before a single sample is generated, so a mismatch is
    a fact and can be fixed with --teach rather than re-rolled and hoped over.

    Only the readings are compared, never the spelling: particles change (は ->
    ワ) and the engine writes its own kana, but a content word's reading is a
    content word's reading.
    """
    heard = canon_engine(heard)
    return [base for base, reading in checks
            if reading and not any(v in heard for v in page_variants(reading))]


# --------------------------------------------------------------------------- #
# ふりがなの点検 — ページ全体を、音になる前に
# --------------------------------------------------------------------------- #

_KANJI = re.compile(r"[一-鿌々]")


def furigana_targets(content: dict) -> list[tuple[str, str, bool]]:
    """(どこの, ふりがなの付いた断片, 声になるか) — 内容JSONの全部から。

    ビルドの中の読みの確認は `speak_page()` の途中で起きるので、**再生ボタンの付く
    文しか見ていない**（例文とクイズの問題文＝`page_sentences()`）。単語表・意味・
    書き順・クイズの解説のふりがなは、何とも突き合わされないままページに載る。
    ここはその全部を並べる。
    """
    out: list[tuple[str, str, bool]] = []
    for entry in content.get("kanji", []):
        char = entry.get("char", "")
        out.append((f"{char} 意味", entry.get("meaning", ""), False))
        out.append((f"{char} 書き順", entry.get("order_note", ""), False))
        for word in entry.get("words", []):
            surface, reading = word.get("w", ""), word.get("r", "")
            if surface and reading:
                # 単語表の見出しは `w`/`r` の二つ組で、ページ上は <ruby> になる。
                # 検査に掛けるためにここで同じ形に組み直す。
                out.append((f"{char} 単語 {surface}",
                            f"<ruby>{surface}<rt>{reading}</rt></ruby>", False))
            out.append((f"{char} 単語 {surface} の意味", word.get("m", ""), False))
        for i, example in enumerate(entry.get("examples", []), 1):
            out.append((f"{char} 例文{i}", example, True))
    for i, question in enumerate(content.get("quiz", []), 1):
        out.append((f"クイズ{i}", question.get("q", ""), True))
        out.append((f"クイズ{i} 解説", question.get("note", ""), False))
    return [(label, text, spoken) for label, text, spoken in out if str(text).strip()]


def checkable(pairs: list[tuple[str, str]], spoken: bool) -> list[tuple[str, str]]:
    """どの (書き方, ふりがな) 組をエンジンに問うてよいか。

    声になる文はそのまま全部。**声にならない断片は熟語（漢字2字以上）だけ**に絞る。
    短い断片の中の1字は、エンジンに訊いても答えが返ってこないからだ：

    - 「<ruby>終<rt>お</rt></ruby>わりまでの」の 終 を単独で読ませれば シュウ。
    - クイズの解説の「<ruby>実<rt>じつ</rt></ruby>」は**読みの名前**を言っている引用で、
      エンジンは語として ミ と読む。
    - 送り仮名付きの訓読みも同じ（付けた→ヅケタ、時が→ジガ）。

    2026-09-01の実測：絞らないと342箇所中9箇所が食い違い、**9箇所とも本物の誤りでは
    なかった**。全部この形。絞ると0になる。一方、絞っても残るのは 翼幅・三角形 のような
    熟語の読み — 連濁・促音・熟字訓で、まさに字を引いても決まらず、ページが間違えうる側。
    """
    if spoken:
        return pairs
    return [(base, reading) for base, reading in pairs
            if len(_KANJI.findall(base)) >= 2]


def furigana_misses(content: dict, speaker: int, *, workers: int = DEFAULT_WORKERS
                    ) -> list[tuple[str, bool, str, str, str, list[str]]]:
    """ページ全体のふりがなをエンジンの読みと突き合わせ、食い違いだけ返す。

    (どこの, 声になるか, 読ませた文, ページの読み, エンジンの読み, 食い違った語)。
    合成は一切しない — `/audio_query` は音を作る前に読みを返すので、120文でも数秒。
    """
    import build_page                      # 循環import避け（build_pageはvoicevoxを読む）

    jobs = []
    for label, text, spoken in furigana_targets(content):
        pairs = checkable(build_page.reading_checks(text), spoken)
        if pairs:
            jobs.append((label, text, spoken, pairs))

    def one(job):
        label, text, spoken, pairs = job
        said = build_page.speech_text(text)
        heard = query_for(said, speaker)["kana"]
        return (label, spoken, said, build_page.speech_kana(text),
                canon_engine(heard), reading_misses(pairs, heard))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return [row for row in pool.map(one, jobs) if row[-1]]


# --------------------------------------------------------------------------- #
# The user dictionary — how a wrong reading gets fixed for good
# --------------------------------------------------------------------------- #


def teach(surface: str, pronunciation: str, accent: int = 0,
          priority: int = 10, word_type: str = "PROPER_NOUN") -> str:
    """Register a reading with the engine, and return the new word's uuid.

    2026-08-31 の例：猿人 を サルジン と読んだ（正しくは エンジン）。一度教えれば
    以後どの文でも直っている。辞書はVOICEVOX側に残るので、このスキルの外でも効く。
    """
    return json.loads(_request("/user_dict_word", query={
        "surface": surface, "pronunciation": katakana(pronunciation),
        "accent_type": accent, "word_type": word_type, "priority": priority}))


def forget(word_uuid: str) -> None:
    """登録を取り消す。DELETEなので method を明示する。"""
    _request(f"/user_dict_word/{word_uuid}", method="DELETE")


def taught() -> dict:
    """Everything currently in the engine's user dictionary."""
    return json.loads(_request("/user_dict"))


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #


def dict_stamp() -> str:
    """A fingerprint of the user dictionary.

    It belongs in the cache key: teaching 猿人 -> エンジン has to reach the
    sentences already synthesized, and without this they would be served from
    the cache still saying サルジン. Any teaching invalidates every clip, which
    would be unthinkable at ElevenLabs prices and costs 74 seconds here.
    """
    try:
        words = taught()
    except VoiceVoxError:
        return "?"
    stamp = "\n".join(sorted(f"{w['surface']}:{w['pronunciation']}" for w in words.values()))
    return hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:12]


def cache_key(text: str, speaker: int, engine: str, fmt: str,
              dictionary: str = "") -> str:
    """The name a clip is filed under. The engine version and the user
    dictionary are in it because either can change how the sentence is read."""
    stamp = "\n".join([str(speaker), engine, fmt, dictionary, text])
    return hashlib.sha1(stamp.encode("utf-8")).hexdigest()


def query_for(text: str, speaker: int) -> dict:
    """The synthesis plan for one sentence, including its reading (`kana`)."""
    return json.loads(_request("/audio_query", query={"text": text, "speaker": speaker}))


def to_m4a(wav: bytes) -> tuple[bytes, str]:
    """WAV re-packed as AAC, or the WAV unchanged when that is not possible.

    The engine returns 24kHz WAV, which is ~130KB for a four-second sentence —
    120 of those is a 16MB folder per day against 2MB before. afconvert (which
    ships with macOS) gets that back to ~29KB. If it is missing or fails, the
    WAV is used as-is: a big folder is a far better outcome than no audio.
    """
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as work:
            src, dst = Path(work) / "a.wav", Path(work) / "a.m4a"
            src.write_bytes(wav)
            subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "64000",
                            str(src), str(dst)], check=True, capture_output=True,
                           timeout=60)
            return dst.read_bytes(), "m4a"
    except (OSError, subprocess.SubprocessError):
        return wav, "wav"


def synthesize(text: str, speaker: int, *, engine: str, cache_dir: Path = CACHE_DIR,
               fmt: str = DEFAULT_FORMAT, dictionary: str = "",
               checks: list[tuple[str, str]] | None = None
               ) -> tuple[bytes, str, str, list[str]]:
    """One sentence as audio bytes, plus (extension, reading, misread words).

    The reading is settled before any audio is made, so a sentence the engine
    would read wrong is reported with its clip rather than instead of it —
    the learner still gets a play button, and the build says which word to teach.
    """
    name = cache_key(text, speaker, engine, fmt, dictionary)
    for suffix in ("m4a", "wav"):
        cached = cache_dir / f"{name}.{suffix}"
        if cached.exists():
            heard = ""
            record = cache_dir / f"{name}.heard.json"
            if record.exists():
                try:
                    heard = json.loads(record.read_text(encoding="utf-8")).get("kana", "")
                except (OSError, ValueError):
                    heard = ""
            return cached.read_bytes(), suffix, heard, reading_misses(checks or [], heard)

    query = query_for(text, speaker)
    heard = query.get("kana", "")
    missed = reading_misses(checks or [], heard)
    wav = _request("/synthesis", query={"speaker": speaker}, body=query,
                   accept="audio/wav")
    if not wav:
        raise VoiceVoxError(f"空の音声が返りました: {text[:20]}")
    audio, suffix = to_m4a(wav) if fmt == "m4a" else (wav, "wav")
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{name}.{suffix}").write_bytes(audio)
    (cache_dir / f"{name}.heard.json").write_text(
        json.dumps({"text": text, "kana": heard, "missed": missed}, ensure_ascii=False),
        encoding="utf-8")
    return audio, suffix, heard, missed


def assign_voices(texts: list[str], voices: list[tuple[str, int]],
                  overrides: dict[str, str] | None = None
                  ) -> dict[str, tuple[str, int]]:
    """Which speaker reads which sentence: text -> (name, style id).

    Chosen from a hash of the sentence rather than at random, so a rebuild lands
    on the same voices and reuses the cache. `overrides` pins a sentence to a
    speaker by name — kept from the ElevenLabs version because a human ear is
    still the only judge of whether a voice suits a sentence.
    """
    if not voices:
        return {}
    by_name = {name: (name, style) for name, style in voices}
    overrides = overrides or {}
    picked = {}
    for text in texts:
        chosen = by_name.get(overrides.get(text, ""))
        if chosen:
            picked[text] = chosen
            continue
        digest = hashlib.sha1(text.encode("utf-8")).digest()
        picked[text] = voices[digest[0] % len(voices)]
    return picked


def audio_dir_for(page: Path) -> Path:
    return page.with_name(f"{page.stem}_audio")


def speak_page(texts: list[str], page: Path, *, voice: str = DEFAULT_VOICE,
               cache_dir: Path = CACHE_DIR, workers: int = DEFAULT_WORKERS,
               fmt: str = DEFAULT_FORMAT,
               checks: dict[str, list[tuple[str, str]]] | None = None,
               overrides: dict[str, str] | None = None
               ) -> tuple[dict[str, dict[str, str]], str]:
    """Everything a page needs: text -> {"src", "voice"}, plus a note for the
    build's console output. Same contract as tts.speak_page.

    Raises nothing. If the engine cannot be reached the note says so and the
    caller decides what to do (build_page falls back to ElevenLabs).
    """
    texts = [t for t in dict.fromkeys(texts) if t.strip()]
    if not texts:
        return {}, ""
    try:
        engine = ensure_engine()
    except VoiceVoxError as error:
        return {}, f"音声なし（VOICEVOX: {error}）"
    try:
        voices = [(name.strip(), resolve_speaker(name)) for name in voice.split(",")
                  if name.strip()]
    except VoiceVoxError as error:
        return {}, f"音声なし（VOICEVOX: {error}）"
    if not voices:
        return {}, "音声なし（話者が指定されていません）"

    pairs = assign_voices(texts, voices, overrides)
    checks = checks or {}
    dictionary = dict_stamp()
    clips: dict[str, tuple[bytes, str]] = {}
    wrong: list[tuple[str, list[str]]] = []
    failed: list[str] = []

    def one(text: str):
        return text, synthesize(text, pairs[text][1], engine=engine, cache_dir=cache_dir,
                                fmt=fmt, dictionary=dictionary, checks=checks.get(text))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in concurrent.futures.as_completed(
                [pool.submit(one, text) for text in texts]):
            try:
                text, (audio, suffix, _, missed) = future.result()
            except VoiceVoxError as error:
                print(f"  音声を作れませんでした — {error}", file=sys.stderr)
                continue
            clips[text] = (audio, suffix)
            if missed:
                wrong.append((text, missed))

    failed = [t for t in texts if t not in clips]
    out_dir = audio_dir_for(page)
    urls: dict[str, dict[str, str]] = {}
    try:
        if clips:
            out_dir.mkdir(parents=True, exist_ok=True)
        for text, (audio, suffix) in clips.items():
            name, speaker = pairs[text]
            filename = f"{cache_key(text, speaker, engine, fmt, dictionary)[:12]}.{suffix}"
            (out_dir / filename).write_bytes(audio)
            urls[text] = {"src": f"{urllib.parse.quote(out_dir.name)}/{filename}",
                          "voice": name}
    except OSError as error:
        return {}, f"音声なし（書き出せません: {error}）"

    tally: dict[str, int] = {}
    for clip in urls.values():
        tally[clip["voice"]] = tally.get(clip["voice"], 0) + 1
    spread = "・".join(f"{name} {n}文" for name, n in sorted(tally.items()))
    note = f"音声 {len(urls)}/{len(texts)}文（{spread or voice}・VOICEVOX {engine}）"
    if failed:
        note += f"／失敗 {len(failed)}文"
    if wrong:
        note += f"／読みが違う {len(wrong)}文"
        for text, words in sorted(wrong):
            print(f"  読みが違います（{'・'.join(words)}）: {text}", file=sys.stderr)
        print("  直すには: python3 voicevox.py --teach <表記> <ヨミ>", file=sys.stderr)
    return urls, note


def credit(urls: dict[str, dict[str, str]]) -> str:
    """The credit line the terms of use require, naming the speakers actually
    heard on this page（https://voicevox.hiroshiba.jp/term/）."""
    if not urls:
        return ""
    names = sorted({clip["voice"] for clip in urls.values()})
    return f"音声：VOICEVOX（{'・'.join(names)}）"


# --------------------------------------------------------------------------- #
# CLI — for checking the setup and teaching readings by hand
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="VOICEVOXの音声と読みを確認する")
    parser.add_argument("--check", action="store_true", help="エンジンと話者を確かめる")
    parser.add_argument("--say", help="この文を読み上げて書き出す")
    parser.add_argument("--reading", help="この文をどう読むか、合成せずに見る")
    parser.add_argument("--out", type=Path, default=Path("voicevox_test.m4a"))
    parser.add_argument("--voice", default="四国めたん")
    parser.add_argument("--format", dest="fmt", default=DEFAULT_FORMAT,
                        choices=["m4a", "wav"])
    parser.add_argument("--cache", type=Path, default=CACHE_DIR)
    parser.add_argument("--teach", nargs=2, metavar=("表記", "ヨミ"),
                        help="読みをユーザー辞書に登録する（例：--teach 猿人 エンジン）")
    parser.add_argument("--accent", type=int, default=0,
                        help="--teach のアクセント位置（既定：0）")
    parser.add_argument("--words", action="store_true", help="登録済みの読みを並べる")
    parser.add_argument("--furigana", type=Path, metavar="content.json",
                        help="ページ中のふりがなを全部エンジンの読みと突き合わせる"
                             "（単語表・意味・書き順・解説まで。合成はしない）")
    parser.add_argument("--kana", type=Path, metavar="content.json",
                        help="ページ中の平仮名のかたまりを全部並べる"
                             "（漢字かカタカナで書くべき語が平仮名のままでないか、目で見る）")
    parser.add_argument("--audit", type=Path, metavar="content.json",
                        help="平仮名書きの語が助詞と読み違えられていないか、"
                             "その日の文を並べて見せる（目で確かめるための道具）")
    args = parser.parse_args()

    try:
        engine = ensure_engine()
    except VoiceVoxError as error:
        print(f"だめ: {error}", file=sys.stderr)
        return 1

    if args.words:
        words = taught()
        if not words:
            print("ユーザー辞書は空です")
        for uuid, word in words.items():
            print(f"  {word['surface']} → {word['pronunciation']}  [{uuid}]")
        return 0

    if args.teach:
        surface, reading = args.teach
        teach(surface, reading, args.accent)
        print(f"登録しました: {surface} → {katakana(reading)}")
        print(f"  確認: {query_for(surface, resolve_speaker(args.voice))['kana']}")
        return 0

    if args.furigana:
        # ビルドの中の確認は再生ボタンの付く文しか見ていない。ここはページの
        # ふりがな全部。**声になる文の食い違いは直すまで終わっていない**
        # （2026-08-31の 三角形・翼幅 は、見つけたまま宿題にして残ってしまった）。
        content = json.loads(args.furigana.read_text(encoding="utf-8"))
        rows = furigana_misses(content, resolve_speaker(args.voice))
        spoken = [r for r in rows if r[1]]
        silent = [r for r in rows if not r[1]]
        for title, group in (("声になる文", spoken), ("声にならないふりがな", silent)):
            if not group:
                continue
            print(f"\n【{title}】")
            for label, _, said, page, engine, missed in sorted(group):
                print(f"  × [{label}] {'・'.join(missed)}")
                print(f"      文      : {said}")
                print(f"      ページ  : {page}")
                print(f"      エンジン: {engine}")
        if not rows:
            print("食い違いなし。")
            return 0
        print(f"\n食い違い {len(rows)}箇所"
              f"（声になる文 {len(spoken)}・声にならない {len(silent)}）")
        print("どちらが正しいかは字ではなく**熟語**を jisho.org で引いて決める。")
        print("  ページの読みが載っていれば → ページが正しい。"
              "--teach でエンジンに教え、音とふりがなを揃える。")
        print("  載っていなければ → ページのふりがなを直す。--teach で塗り潰さない。")
        return 1

    if args.kana:
        # 生き物の名前を平仮名で書いていないか探すための道具（SKILL.md 5節）。
        #
        # **語の表で探すのは当てにならない。** 2026-09-01に2回失敗している：
        # 「かめ」を持っていても うみがめ は連濁して「がめ」なので当たらず、
        # さけ・つる・ひな はそもそも表に無かった。だから絞り込まず、
        # **平仮名のかたまりを全部並べて目で見る**。
        #
        # 読みの確認（--furigana）はここに届かない。ふりがなの無い平仮名は
        # 「かなは読み違えようがない」として素通りするからで、これはその穴を
        # 人の目で塞ぐためのもの。機械には動物名かどうか決められない。
        import build_page
        content = json.loads(args.kana.read_text(encoding="utf-8"))
        seen: dict[str, tuple[int, str]] = {}
        for label, text, _ in furigana_targets(content):
            for run in re.findall(r"[ぁ-ん]{2,}", build_page.speech_text(text)):
                count, where = seen.get(run, (0, label))
                seen[run] = (count + 1, where)
        for run, (count, where) in sorted(seen.items(),
                                          key=lambda kv: (-len(kv[0]), kv[0])):
            print(f"  {run}  ×{count}  [{where}]")
        print(f"\n{len(seen)}種類。生き物の名前が平仮名で混じっていないか目で見る"
              f"（さけ→サケ、つる→ツル、うみがめ→ウミガメ）。")
        print("見つけたら内容JSONを直す。ユーザー辞書では直さない。")
        return 0

    if args.audit:
        # 助詞と読み違えられうるのは は・へ・を で始まる平仮名の語。ふりがなが無いので
        # 読みの確認は素通りする（6.5節「平仮名で書く語に気をつける」）。品詞が分からない
        # 以上、機械には助詞の は と語頭の は を区別できない — だから並べて見せるだけにする。
        import build_page
        content = json.loads(args.audit.read_text(encoding="utf-8"))
        speaker = resolve_speaker(args.voice)
        found = 0
        for sentence in build_page.page_sentences(content):
            said = build_page.speech_text(sentence)
            if not said.strip():
                continue
            runs = [r for r in re.findall(r"[ぁ-ん]{2,}", said) if r[0] in "はへを"]
            if not runs:
                continue
            found += 1
            print(f"  {'・'.join(runs)}")
            print(f"    {said}")
            print(f"    {canon_engine(query_for(said, speaker)['kana'])}")
        print(f"\n{found}文。助詞として読まれてよい は・へ・を か、"
              f"語の一部なのに助詞にされていないかを目で見る。")
        print("語だった場合はカタカナか漢字に書き換える（ユーザー辞書では直さない）。")
        return 0

    if args.reading:
        print(query_for(args.reading, resolve_speaker(args.voice))["kana"])
        return 0

    if args.check:
        print(f"エンジン {engine}")
        for name, style in SPEAKERS.items():
            print(f"  {name} (id {style})")
        return 0

    if args.say:
        audio, suffix, heard, _ = synthesize(args.say, resolve_speaker(args.voice),
                                             engine=engine, cache_dir=args.cache,
                                             fmt=args.fmt, dictionary=dict_stamp())
        out = args.out.with_suffix(f".{suffix}")
        out.write_bytes(audio)
        print(f"{out}（{heard}）")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
