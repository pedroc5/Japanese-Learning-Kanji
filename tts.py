#!/usr/bin/env python3
"""ElevenLabs text-to-speech for the practice pages — 例文とクイズの音声を作る。

The pages want a play button on every example sentence and every exercise, so
one build asks for ~120 short clips. Three things follow from that, and they
are most of what this module is:

* **Everything is cached by content.** The cache key is voice + model + format
  + the exact text, so a rerun of the same day (the `--append` extra class, a
  retried launchd run, Friday's review page reusing a sentence) costs nothing
  and takes no time. Credits are only spent on text never spoken before.
* **The cache lives in the skill folder**, not beside the page. ~/Documents is
  TCC-protected and a launchd process cannot read back files there — the same
  reason kanji_history.json and .content/ sit here (see build_page.py). The
  mp3s written next to the page are write-only as far as this code is
  concerned: we never stat or re-read them, we just write what the page needs.
* **Requests go out in parallel**, a few at a time, because 120 sequential
  round trips would take longer than the whole rest of the build.

Only the standard library, like the rest of the build (run_daily.sh runs on
system python3, no conda, no requests).

    python3 tts.py --check              # key + voice reachable?
    python3 tts.py --say "こんにちは" --out hello.mp3
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The key. A file rather than an env var because the daily build runs under
# launchd, which inherits nothing from Pedro's shell — an exported variable in
# .zshrc would work interactively and then silently produce a page with no
# audio every morning. Gitignored.
KEY_FILE = HERE / ".elevenlabs_key"
KEY_ENV = "ELEVENLABS_API_KEY"

CACHE_DIR = HERE / ".tts_cache"

API = "https://api.elevenlabs.io"

# Pedro's two Japanese voices, by id. Kept here so the daily build never spends
# a round trip (or a failure mode) on looking a name up: /v2/voices only finds
# a voice already added to the account, and a page with no audio because a
# voice search 404'd at 9:00 is a worse outcome than a hard-coded id.
# Any other name still goes through resolve_voice() and the API.
KNOWN_VOICES = {
    "riku": "KJje3wzepLiSIQjVGCB6",       # 男性
    "sakura": "gHBfNp2PWSyFgpPlzCOd",     # 女性
    "kozy": "GxxMAMfQkDlnqjpzjLHH",       # 男性
    "shizuka": "WQz3clzUdMqvBf0jswZQ",    # 女性
}
# All four by default, split sentence by sentence (assign_voices). A page read
# entirely by one narrator is monotonous to work through, and hearing the same
# vocabulary in four voices — two male, two female — is closer to how it turns
# up in the wild.
DEFAULT_VOICE = "Riku,Sakura,Kozy,Shizuka"
DEFAULT_MODEL = "eleven_v3"
# Say which language the text is in. A 20字 sentence of nothing but kanji is
# genuinely ambiguous between Japanese and Chinese, and left to infer it the
# model sometimes picks wrong — which on this page reads as "it said the word
# incorrectly". v3 honours this and normalizes text for the language; v2
# accepts the field but ignores it (per the API docs), which is one reason the
# same sentence can come back wrong on v2 and right on v3.
DEFAULT_LANGUAGE = "ja"
# 64kbps mono mp3: clearly good enough for a spoken sentence, and a quarter the
# size of the 128k default — with ~120 clips a day that is the difference
# between a 2MB and an 8MB folder per page.
DEFAULT_FORMAT = "mp3_44100_64"
DEFAULT_WORKERS = 3               # 同時リクエスト数（プランの上限に合わせる）

TIMEOUT = 120
RETRIES = 3


# Failures that will repeat identically on all ~120 sentences: a bad key, an
# exhausted quota, a voice the account can't use. Worth recognising, because
# retrying each sentence in turn just produces 120 copies of one error.
FATAL_SIGNS = ("authentication_error", "invalid_api_key", "HTTP 401", "HTTP 403",
               "HTTP 402", "payment_required", "paid_plan_required",
               "quota_exceeded", "missing_permissions")


def is_fatal(error: object) -> bool:
    """Whether an error means "stop the whole batch", not "this one sentence"."""
    return any(sign in str(error) for sign in FATAL_SIGNS)


class TTSError(RuntimeError):
    """Anything that means "no audio this run" — missing key, unknown voice,
    the API refusing. Callers catch it and build the page without buttons
    rather than failing the whole day's build."""


# --------------------------------------------------------------------------- #
# API key and voice
# --------------------------------------------------------------------------- #


def load_key(key_file: Path = KEY_FILE) -> str:
    """The API key, from the key file or, failing that, the environment.

    Returns "" when there is none — that is not an error here, it is how the
    build decides to skip audio.
    """
    import os
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        if key:
            return key
    return os.environ.get(KEY_ENV, "").strip()


def _request(path: str, key: str, *, body: dict | None = None,
             query: dict | None = None, accept: str = "application/json") -> bytes:
    """One call to the ElevenLabs API, retried on the failures worth retrying.

    429 (rate limit) and 5xx get an exponential backoff; 401/404/422 are the
    caller's fault and come straight back as TTSError with the API's own
    message, which is usually specific enough to act on.
    """
    url = f"{API}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"xi-api-key": key, "Accept": accept}
    if data is not None:
        headers["Content-Type"] = "application/json"

    last = ""
    for attempt in range(RETRIES):
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:400]
            last = f"HTTP {error.code}: {detail}"
            if error.code not in (429, 500, 502, 503, 504):
                raise TTSError(last) from None
        except (urllib.error.URLError, TimeoutError) as error:
            last = f"接続できません: {error}"
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise TTSError(last)


def resolve_voice(name: str, key: str, cache_dir: Path = CACHE_DIR) -> str:
    """The voice_id for a voice name, looked up once and remembered.

    A name is what Pedro asked for ("Riku"); the API wants an id. KNOWN_VOICES
    answers for his own two without any call at all; anything else is looked up
    once and remembered in the cache folder.

    A voice from the community library has to be added to the account before
    it can be spoken with, so when the name is only found there we say so and
    hand over the id, which --voice-id will accept directly.
    """
    if len(name) == 20 and name.isalnum():   # already an id
        return name
    if name.strip().lower() in KNOWN_VOICES:
        return KNOWN_VOICES[name.strip().lower()]

    remembered = cache_dir / f"voice_{name}.txt"
    if remembered.exists():
        return remembered.read_text(encoding="utf-8").strip()

    payload = json.loads(_request("/v2/voices", key,
                                  query={"search": name, "page_size": 100}))
    for voice in payload.get("voices", []):
        if voice.get("name", "").strip().lower() == name.strip().lower():
            cache_dir.mkdir(parents=True, exist_ok=True)
            remembered.write_text(voice["voice_id"], encoding="utf-8")
            return voice["voice_id"]

    available = "・".join(v.get("name", "?") for v in payload.get("voices", [])[:10])
    shared = ""
    try:
        library = json.loads(_request("/v2/voices", key,
                                      query={"search": name, "voice_type": "community",
                                             "language": "ja", "page_size": 10}))
        hit = next((v for v in library.get("voices", [])
                    if v.get("name", "").strip().lower() == name.strip().lower()), None)
        if hit:
            shared = (f"（ボイスライブラリには見つかりました: voice_id={hit['voice_id']}。"
                      "ElevenLabsのアプリで自分のVoicesに追加するか、"
                      "--voice-id でこのIDを直接指定してください）")
    except TTSError:
        pass
    raise TTSError(f"ボイス「{name}」がアカウントに見つかりません{shared}"
                   + (f" / 見つかったもの: {available}" if available and not shared else ""))


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #


def cache_key(text: str, voice_id: str, model: str, fmt: str,
              language: str = DEFAULT_LANGUAGE) -> str:
    """The name a clip is filed under. Everything that changes the audio is in
    it, so a new voice, model or language doesn't quietly serve the old
    recording."""
    stamp = "\n".join([voice_id, model, fmt, language, text])
    return hashlib.sha1(stamp.encode("utf-8")).hexdigest()


def synthesize(text: str, voice_id: str, key: str, *, model: str = DEFAULT_MODEL,
               fmt: str = DEFAULT_FORMAT, cache_dir: Path = CACHE_DIR,
               language: str = DEFAULT_LANGUAGE) -> bytes:
    """One sentence as mp3 bytes, from the cache when we've said it before.

    Deliberately sends no voice_settings. Omitting the field makes the API use
    the settings stored on the voice itself — the ones its author tuned and the
    ones elevenlabs.io plays it with. An earlier version passed a generic
    {stability: .5, similarity_boost: .75} here, which overrode Riku's and
    Sakura's own settings (and silently dropped style, speed and
    use_speaker_boost); that is most of why the same sentence sounded better on
    the website than on this page.
    """
    name = cache_key(text, voice_id, model, fmt, language)
    cached = cache_dir / f"{name}.mp3"
    if cached.exists():
        return cached.read_bytes()

    body = {"text": text, "model_id": model}
    if language:
        body["language_code"] = language
    audio = _request(f"/v1/text-to-speech/{voice_id}", key,
                     body=body, query={"output_format": fmt}, accept="audio/mpeg")
    if not audio:
        raise TTSError(f"空の音声が返りました: {text[:20]}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(audio)
    return audio


def assign_voices(texts: list[str], voices: list[tuple[str, str]]
                  ) -> dict[str, tuple[str, str]]:
    """Which voice reads which sentence: text -> (name, voice_id).

    Pedro wants the two voices alternating unpredictably through a page rather
    than one narrator throughout, so the choice is made per sentence — but from
    a hash of the sentence, not random(). The spread is the same 50/50 either
    way; the difference is that a hash gives the *same* answer next time, so
    rebuilding a day (a retried launchd run, an --append extra class) still
    hits the cache instead of re-synthesizing all 120 clips in freshly shuffled
    voices and billing for them a second time.
    """
    if not voices:
        return {}
    picked = {}
    for text in texts:
        digest = hashlib.sha1(text.encode("utf-8")).digest()
        picked[text] = voices[digest[0] % len(voices)]
    return picked


def synthesize_all(pairs: dict[str, str], key: str, *,
                   model: str = DEFAULT_MODEL, fmt: str = DEFAULT_FORMAT,
                   cache_dir: Path = CACHE_DIR, workers: int = DEFAULT_WORKERS,
                   language: str = DEFAULT_LANGUAGE,
                   ) -> tuple[dict[str, bytes], list[str]]:
    """Every sentence at once, a few requests in flight at a time.

    `pairs` maps each sentence to the voice_id that should read it (see
    assign_voices). Returns (text -> mp3 bytes, texts that failed). A failure
    is not fatal: that sentence simply loses its play button, and the next
    build will try it again because nothing was cached for it.
    """
    unique = [t for t in pairs if t.strip()]
    clips: dict[str, bytes] = {}

    # Anything already cached is free and instant — take it here so the pool
    # only ever holds real network work.
    pending = []
    for text in unique:
        cached = cache_dir / f"{cache_key(text, pairs[text], model, fmt, language)}.mp3"
        if cached.exists():
            clips[text] = cached.read_bytes()
        else:
            pending.append(text)

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(synthesize, text, pairs[text], key, model=model,
                                   fmt=fmt, cache_dir=cache_dir, language=language): text
                       for text in pending}
            for future in concurrent.futures.as_completed(futures):
                text = futures[future]
                try:
                    clips[text] = future.result()
                except TTSError as error:
                    if is_fatal(error):
                        # The key or the quota, not this sentence. Say it once
                        # and drop the rest rather than repeating it 120 times.
                        print(f"  音声を中止します — {error}", file=sys.stderr)
                        for other in futures:
                            other.cancel()
                        break
                    print(f"  音声を作れませんでした: {text[:24]}… — {error}", file=sys.stderr)

    # Whatever the reason — refused, cancelled, or a request still in flight
    # when the batch was abandoned — anything without a clip is a failure. The
    # caller only needs to know which sentences have no audio.
    failed = [text for text in unique if text not in clips]
    return clips, failed


# --------------------------------------------------------------------------- #
# Writing the clips out beside the page
# --------------------------------------------------------------------------- #


def audio_dir_for(page: Path) -> Path:
    """漢字練習_2026-08-29.html -> 漢字練習_2026-08-29_audio/ beside it."""
    return page.with_name(f"{page.stem}_audio")


def write_clips(clips: dict[str, bytes], out_dir: Path,
                pairs: dict[str, tuple[str, str]], model: str, fmt: str,
                language: str = DEFAULT_LANGUAGE) -> dict[str, dict[str, str]]:
    """Drop the mp3s next to the page and return text -> {"src", "voice"}.

    The voice name rides along so the page can say who is speaking: with two
    voices alternating, a play button that doesn't name one leaves Pedro
    unable to tell which he preferred.

    Files are named by their cache key so the same sentence is one file, and
    the URL is percent-encoded because the week folder has Japanese in its
    name and the page is opened over file://.

    Written unconditionally, never checked for existence first: under launchd
    ~/Documents can be written but not read back (TCC), so "is it already
    there?" is a question this code is not allowed to ask. The bytes are free
    — they come from the cache in the skill folder.
    """
    if not clips:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = {}
    for text, audio in clips.items():
        voice_name, voice_id = pairs[text]
        name = f"{cache_key(text, voice_id, model, fmt, language)[:12]}.mp3"
        (out_dir / name).write_bytes(audio)
        urls[text] = {"src": f"{urllib.parse.quote(out_dir.name)}/{name}",
                      "voice": voice_name}
    return urls


def speak_page(texts: list[str], page: Path, *, voice: str = DEFAULT_VOICE,
               voice_id: str = "", model: str = DEFAULT_MODEL, fmt: str = DEFAULT_FORMAT,
               cache_dir: Path = CACHE_DIR, key_file: Path = KEY_FILE,
               workers: int = DEFAULT_WORKERS, language: str = DEFAULT_LANGUAGE
               ) -> tuple[dict[str, dict[str, str]], str]:
    """Everything a page needs, in one call: text -> {"src", "voice"}, plus a
    one-line note for the build's console output.

    `voice` may name more than one voice, comma-separated ("Riku,Sakura"); the
    sentences are then split between them (see assign_voices). `voice_id`
    overrides the lot with a single id.

    Raises nothing. No key, no network, a bad voice name — the page is built
    without audio and the note says why, because a silent page is a far better
    outcome than no page at all at 9:00.
    """
    texts = [t for t in dict.fromkeys(texts) if t.strip()]
    if not texts:
        return {}, ""

    key = load_key(key_file)
    if not key:
        return {}, f"音声なし（APIキーがありません: {key_file} か ${KEY_ENV}）"
    try:
        if voice_id:
            voices = [(voice_id, voice_id)]
        else:
            voices = [(name.strip(), resolve_voice(name.strip(), key, cache_dir))
                      for name in voice.split(",") if name.strip()]
    except TTSError as error:
        return {}, f"音声なし（{error}）"
    if not voices:
        return {}, "音声なし（ボイスが指定されていません）"

    pairs = assign_voices(texts, voices)
    clips, failed = synthesize_all({t: v[1] for t, v in pairs.items()}, key,
                                   model=model, fmt=fmt, cache_dir=cache_dir,
                                   workers=workers, language=language)
    try:
        urls = write_clips(clips, audio_dir_for(page), pairs, model, fmt, language)
    except OSError as error:
        # ~/Documents is TCC-protected and the daily build runs under launchd.
        # Writing there has always worked, but if it ever stops, that must cost
        # the page its play buttons — not the page itself. The clips are already
        # in the cache, so the next successful run writes them out for free.
        return {}, f"音声なし（書き出せません: {error}）"

    tally = {}
    for clip in urls.values():
        tally[clip["voice"]] = tally.get(clip["voice"], 0) + 1
    spread = "・".join(f"{name} {n}文" for name, n in sorted(tally.items()))
    note = f"音声 {len(urls)}/{len(texts)}文（{spread or voice}・{model}）"
    if failed:
        note += f"／失敗 {len(failed)}文"
    return urls, note


# --------------------------------------------------------------------------- #
# CLI — for checking the setup by hand, not used by the build
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="ElevenLabsの音声を確認する")
    parser.add_argument("--check", action="store_true", help="キーとボイスが使えるか確かめる")
    parser.add_argument("--say", help="この文を読み上げてmp3にする")
    parser.add_argument("--out", type=Path, default=Path("tts_test.mp3"))
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--voice-id", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--format", dest="fmt", default=DEFAULT_FORMAT)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE,
                        help=f"読み上げる言語（既定：{DEFAULT_LANGUAGE}）")
    parser.add_argument("--cache", type=Path, default=CACHE_DIR,
                        help=f"音声のキャッシュ（既定：{CACHE_DIR.name}）")
    args = parser.parse_args()

    key = load_key()
    if not key:
        print(f"APIキーがありません。{KEY_FILE} に書くか ${KEY_ENV} を設定してください。",
              file=sys.stderr)
        return 1
    try:
        names = [n.strip() for n in args.voice.split(",") if n.strip()]
        if args.voice_id:
            voice_id = args.voice_id
            print(f"ボイス（ID指定）→ {voice_id}")
        else:
            voice_id = resolve_voice(names[0], key, args.cache)
            print(f"ボイス {names[0]} → {voice_id}")
        if args.check and not args.say:
            args.say = "音声のテストです。今日の漢字を練習しましょう。"
        if args.say:
            audio = synthesize(args.say, voice_id, key, model=args.model, fmt=args.fmt,
                               cache_dir=args.cache, language=args.language)
            args.out.write_bytes(audio)
            print(f"完了：{args.out}（{len(audio) // 1024}KB・{args.model}）")
    except TTSError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
