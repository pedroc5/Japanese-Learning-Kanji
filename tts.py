#!/usr/bin/env python3
"""ElevenLabs text-to-speech for the practice pages — 例文とクイズの音声を作る。

The pages want a play button on every example sentence and every exercise, so
one build asks for ~120 short clips. Three things follow from that, and they
are most of what this module is:

* **Everything is cached by content.** The cache key is voice + model + format
  + the exact text, so a rerun of the same day (the `--append` extra class, a
  retried launchd run, Friday's review page reusing a sentence) costs nothing
  and takes no time. Credits are only spent on text never spoken before.
* **The cache lives in the skill folder**, not beside the page. A TCC-protected
  output root (anything under ~/Documents, ~/Desktop or ~/Downloads) is
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
import re
import sys
import time
import unicodedata
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The key. A file rather than an env var because the daily build runs under
# launchd, which inherits nothing from the login shell — an exported variable in
# .zshrc would work interactively and then silently produce a page with no
# audio every morning. Gitignored.
KEY_FILE = HERE / ".elevenlabs_key"
KEY_ENV = "ELEVENLABS_API_KEY"

CACHE_DIR = HERE / ".tts_cache"

API = "https://api.elevenlabs.io"

# The Japanese voices used here, by id. Kept here so the daily build never spends
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
# 読み上げたものを聞き取り直して、書いてある読みと合っているか確かめるモデル。
DEFAULT_STT_MODEL = "scribe_v1"
# 1文につき何回まで録り直すか（1回目を含む）。v3は同じ文でも読みが揺れるので、
# 読み違えた回は捨ててもう一度引けばたいてい直る。3回でだめなら諦めて一番よい
# 回を採る — 直らない文のために毎朝クレジットを溶かすほうが困る。
DEFAULT_ATTEMPTS = 3
# 出だしの潰れは、文の前に読点を置いても・波形を測っても解けなかった。2026-08-31の
# 顛末は SKILL.md の「出だしがはっきりしないとき」に書いてある。読み替えは声を替える。

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
             query: dict | None = None, accept: str = "application/json",
             raw_body: bytes | None = None, content_type: str = "") -> bytes:
    """One call to the ElevenLabs API, retried on the failures worth retrying.

    429 (rate limit) and 5xx get an exponential backoff; 401/404/422 are the
    caller's fault and come straight back as TTSError with the API's own
    message, which is usually specific enough to act on.

    `body` is JSON; `raw_body` with `content_type` is for speech-to-text, which
    wants the mp3 as multipart/form-data rather than JSON.
    """
    url = f"{API}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else raw_body
    headers = {"xi-api-key": key, "Accept": accept}
    if body is not None:
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        headers["Content-Type"] = content_type

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

    A name is what the caller gives ("Riku"); the API wants an id. KNOWN_VOICES
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


# --------------------------------------------------------------------------- #
# Checking what the voice actually said
# --------------------------------------------------------------------------- #


# ISO 639-1 for the TTS endpoint, 639-3 for speech-to-text. The same "日本語"
# either way, spelled differently by the two APIs.
STT_LANGUAGE = {"ja": "jpn"}

_PUNCT = re.compile(r"[\s、。，．,.!！?？「」『』（）()…・ー〜]")
_DIGITS = re.compile(r"[0-9]+")
_KANJI_RUN = re.compile(r"[一-鿿々]+")
_NUM = "〇一二三四五六七八九"


def _kanji_number(value: int) -> str:
    """12 -> 十二. Only up to 999, which is as far as a practice sentence
    counts (十個, 二メートル, 七時)."""
    if value < 10:
        return _NUM[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return ("" if tens == 1 else _NUM[tens]) + "十" + (_NUM[ones] if ones else "")
    hundreds, rest = divmod(value, 100)
    return ("" if hundreds == 1 else _NUM[hundreds]) + "百" + (_kanji_number(rest) if rest else "")


def normalize_heard(text: str) -> str:
    """Both sides of the comparison, reduced to what a reading check cares about.

    Punctuation and spacing go (the voice's phrasing is not the transcript's to
    reproduce), and 10年 becomes 十年 — Scribe writes numbers as digits whatever
    the page wrote, and that is a spelling difference, not a misreading.
    """
    text = unicodedata.normalize("NFKC", str(text))
    text = _DIGITS.sub(lambda m: _kanji_number(int(m.group())) if int(m.group()) < 1000
                       else m.group(), text)
    return _PUNCT.sub("", text)


def kanji_runs(text: str) -> list[tuple[str, str]]:
    """Fallback reading checks for a sentence with no furigana to go on: every
    run of kanji in it, with no reading to accept in its place."""
    return [(run, "") for run in _KANJI_RUN.findall(normalize_heard(text))]


def reading_misses(checks: list[tuple[str, str]], heard: str) -> list[str]:
    """Which of a sentence's words the transcript says were read wrong.

    Each check is (書き方, ふりがな) taken from the page's own ruby — the reading
    the learner is being taught, which step 4 verified on jisho. A word passes if the
    transcript contains either one:

    - the kanji itself, meaning Scribe heard the word and wrote it back (祖父);
    - or its kana reading, because Scribe often writes in kana what the page
      wrote in kanji (分かる -> わかる, 付いた -> ついた). Same sound, so nothing
      is wrong with the audio.

    What survives both is a genuine divergence: 祖父 coming back as ザフト, 鶏
    as 鳥, 尾翼 as 微弱. The check is deliberately one-directional — kana in the
    page that Scribe wrote as kanji (そうじ -> 掃除) is not looked at at all,
    because kana is the one thing a voice cannot misread.
    """
    heard = normalize_heard(heard)
    missed = []
    for base, reading in checks:
        base = normalize_heard(base)
        if base and base in heard:
            continue
        if reading and normalize_heard(reading) in heard:
            continue
        missed.append(base)
    return missed


def transcribe(audio: bytes, key: str, *, model: str = DEFAULT_STT_MODEL,
               language: str = DEFAULT_LANGUAGE) -> dict:
    """What the mp3 actually says, via ElevenLabs speech-to-text.

    Returns the whole record, not just the text: the per-word timestamps that
    come with it for free are what the clarity check reads to find where the
    first word starts.
    """
    boundary = uuid.uuid4().hex
    fields = {"model_id": model}
    if language:
        fields["language_code"] = STT_LANGUAGE.get(language, language)
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f'filename="clip.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode("utf-8"))
    parts.append(audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    raw = _request("/v1/speech-to-text", key, raw_body=body,
                   content_type=f"multipart/form-data; boundary={boundary}")
    heard = json.loads(raw)
    return {"text": heard.get("text", ""), "words": heard.get("words") or []}


def heard_path(cache_dir: Path, name: str) -> Path:
    """Where the transcript of a cached clip is kept: <key>.heard.json, beside
    the mp3.

    Its presence is the record that this clip has been listened to. A clip made
    before the check existed has no file and gets checked on the next build; one
    that has been checked is never checked again, however it came out — the
    sentences that stay wrong after three takes are wrong for a reason, and
    paying to rediscover that every morning would be its own bug.
    """
    return cache_dir / f"{name}.heard.json"


def synthesize(text: str, voice_id: str, key: str, *, model: str = DEFAULT_MODEL,
               fmt: str = DEFAULT_FORMAT, cache_dir: Path = CACHE_DIR,
               language: str = DEFAULT_LANGUAGE,
               checks: list[tuple[str, str]] | None = None,
               attempts: int = 1, stt_model: str = DEFAULT_STT_MODEL) -> bytes:
    """One sentence as mp3 bytes, from the cache when we've said it before.

    Deliberately sends no voice_settings. Omitting the field makes the API use
    the settings stored on the voice itself — the ones its author tuned and the
    ones elevenlabs.io plays it with. An earlier version passed a generic
    {stability: .5, similarity_boost: .75} here, which overrode Riku's and
    Sakura's own settings (and silently dropped style, speed and
    use_speaker_boost); that is most of why the same sentence sounded better on
    the website than on this page.

    With `checks` (the page's own furigana, as (書き方, ふりがな) pairs) each take
    is transcribed and compared against them, and a take that misreads a word is
    thrown away and drawn again, up to `attempts` times. v3 does not read the
    same sentence the same way twice — that is exactly why a bad take is worth
    re-rolling, and why one bad take was never evidence that the sentence
    couldn't be read. If no take comes back clean, the closest one is kept: a
    sentence with a play button that mispronounces one word is still better than
    a sentence with no play button.
    """
    if attempts < 2:
        checks = None        # 録り直せないなら聞き直す意味もない
    name = cache_key(text, voice_id, model, fmt, language)
    cached = cache_dir / f"{name}.mp3"
    heard = heard_path(cache_dir, name)
    if cached.exists() and (heard.exists() or not checks or attempts < 2):
        return cached.read_bytes()

    body = {"text": text, "model_id": model}
    if language:
        body["language_code"] = language

    best: tuple[bytes, str, list[str]] | None = None
    for attempt in range(max(1, attempts) if checks else 1):
        if cached.exists() and attempt == 0:
            # An unjudged clip from before the check existed. Listen to what we
            # already have before paying to say it again.
            audio = cached.read_bytes()
        else:
            audio = _request(f"/v1/text-to-speech/{voice_id}", key,
                             body=body, query={"output_format": fmt}, accept="audio/mpeg")
        if not audio:
            raise TTSError(f"空の音声が返りました: {text[:20]}")
        if not checks:
            best = (audio, "", [])
            break
        try:
            heard_now = transcribe(audio, key, model=stt_model, language=language)
        except TTSError as error:
            # Losing the transcript costs us the check, not the clip.
            print(f"  読みを確かめられませんでした: {text[:24]}… — {error}", file=sys.stderr)
            best = (audio, "", [])
            break
        said = heard_now["text"]
        missed = reading_misses(checks, said)
        if best is None or len(missed) < len(best[2]):
            best = (audio, said, missed)
        if not missed:
            break

    audio, said, missed = best                      # 少なくとも1回は回っている
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(audio)
    if checks:
        heard.write_text(json.dumps({"text": text, "heard": said, "missed": missed},
                                    ensure_ascii=False), encoding="utf-8")
    return audio


OVERRIDES = "voices.json"


def load_overrides(cache_dir: Path = CACHE_DIR) -> dict[str, str]:
    """Sentences assigned a voice by hand, text -> voice name.

    The escape hatch for what no check can hear. Scribe transcribes a clip by
    what the sentence must have been, not by what the voice actually produced,
    so a mangled 机 still comes back as 机 — on 2026-08-31 Shizuka read it as
    「くえ」 and then as 「くくえ」 and the transcript said 机 every time. When
    the listener's ear catches one of those, `--revoice` writes it here and the
    sentence keeps the voice he chose.
    """
    path = cache_dir / OVERRIDES
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_override(text: str, voice_name: str, cache_dir: Path = CACHE_DIR) -> None:
    """Pin one sentence to one voice, or drop the pin when voice_name is ""."""
    overrides = load_overrides(cache_dir)
    if voice_name:
        overrides[text] = voice_name
    else:
        overrides.pop(text, None)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / OVERRIDES).write_text(json.dumps(overrides, ensure_ascii=False, indent=1),
                                       encoding="utf-8")


def assign_voices(texts: list[str], voices: list[tuple[str, str]],
                  overrides: dict[str, str] | None = None
                  ) -> dict[str, tuple[str, str]]:
    """Which voice reads which sentence: text -> (name, voice_id).

    The two voices should alternate unpredictably through a page rather
    than one narrator throughout, so the choice is made per sentence — but from
    a hash of the sentence, not random(). The spread is the same 50/50 either
    way; the difference is that a hash gives the *same* answer next time, so
    rebuilding a day (a retried launchd run, an --append extra class) still
    hits the cache instead of re-synthesizing all 120 clips in freshly shuffled
    voices and billing for them a second time.
    """
    if not voices:
        return {}
    by_name = {name.lower(): (name, vid) for name, vid in voices}
    overrides = overrides or {}
    picked = {}
    for text in texts:
        chosen = by_name.get(overrides.get(text, "").lower())
        if chosen:
            picked[text] = chosen
            continue
        digest = hashlib.sha1(text.encode("utf-8")).digest()
        picked[text] = voices[digest[0] % len(voices)]
    return picked


def synthesize_all(pairs: dict[str, str], key: str, *,
                   model: str = DEFAULT_MODEL, fmt: str = DEFAULT_FORMAT,
                   cache_dir: Path = CACHE_DIR, workers: int = DEFAULT_WORKERS,
                   language: str = DEFAULT_LANGUAGE,
                   checks: dict[str, list[tuple[str, str]]] | None = None,
                   attempts: int = 1, stt_model: str = DEFAULT_STT_MODEL,
                   ) -> tuple[dict[str, bytes], list[str]]:
    """Every sentence at once, a few requests in flight at a time.

    `pairs` maps each sentence to the voice_id that should read it (see
    assign_voices). Returns (text -> mp3 bytes, texts that failed). A failure
    is not fatal: that sentence simply loses its play button, and the next
    build will try it again because nothing was cached for it.

    `checks` maps a sentence to the readings it has to come back with; see
    synthesize(). A cached clip that has never been listened to goes through the
    pool like a new one, so the check reaches yesterday's audio too — but it is
    transcribed, not re-synthesized, unless it turns out to be wrong.
    """
    unique = [t for t in pairs if t.strip()]
    clips: dict[str, bytes] = {}
    checks = checks or {}

    # Anything already cached is free and instant — take it here so the pool
    # only ever holds real network work.
    pending = []
    for text in unique:
        name = cache_key(text, pairs[text], model, fmt, language)
        cached = cache_dir / f"{name}.mp3"
        judged = heard_path(cache_dir, name).exists() or not checks.get(text) or attempts < 2
        if cached.exists() and judged:
            clips[text] = cached.read_bytes()
        else:
            pending.append(text)

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(synthesize, text, pairs[text], key, model=model,
                                   fmt=fmt, cache_dir=cache_dir, language=language,
                                   checks=checks.get(text), attempts=attempts,
                                   stt_model=stt_model): text
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
    voices alternating, a play button that doesn't name one leaves the listener
    unable to tell which he preferred.

    Files are named by their cache key so the same sentence is one file, and
    the URL is percent-encoded because the week folder has Japanese in its
    name and the page is opened over file://.

    Written unconditionally, never checked for existence first: under launchd
    A TCC-protected output root can be written but not read back, so "is it already
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


def heard_record(text: str, voice_id: str, *, model: str, fmt: str,
                 language: str, cache_dir: Path) -> dict | None:
    """What was heard when this sentence was last read by this voice, or None
    if that pairing has never been judged."""
    path = heard_path(cache_dir, cache_key(text, voice_id, model, fmt, language))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def misread(texts: list[str], pairs: dict[str, tuple[str, str]], *, model: str,
            fmt: str, language: str, cache_dir: Path) -> list[tuple[str, list[str]]]:
    """The sentences whose clips are still misread after every voice has tried,
    read back out of the .heard.json files. Reported so the listener knows which play
    buttons to distrust rather than having to catch them by ear."""
    out = []
    for text in texts:
        if text not in pairs:
            continue
        record = heard_record(text, pairs[text][1], model=model, fmt=fmt,
                              language=language, cache_dir=cache_dir)
        if record and record.get("missed"):
            out.append((text, record["missed"]))
    return out


def speak_page(texts: list[str], page: Path, *, voice: str = DEFAULT_VOICE,
               voice_id: str = "", model: str = DEFAULT_MODEL, fmt: str = DEFAULT_FORMAT,
               cache_dir: Path = CACHE_DIR, key_file: Path = KEY_FILE,
               workers: int = DEFAULT_WORKERS, language: str = DEFAULT_LANGUAGE,
               checks: dict[str, list[tuple[str, str]]] | None = None,
               attempts: int = 1, stt_model: str = DEFAULT_STT_MODEL
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

    pairs = assign_voices(texts, voices, load_overrides(cache_dir))
    spoken = {t: v[1] for t, v in pairs.items()}
    clips, failed = synthesize_all(spoken, key,
                                   model=model, fmt=fmt, cache_dir=cache_dir,
                                   workers=workers, language=language,
                                   checks=checks, attempts=attempts, stt_model=stt_model)

    # 同じ声で引き直しても直らない語がある（2026-08-31：Kozyの鶏卵・帆立貝、
    # Shizukaの文頭の「つ」）。読みが合わない文は声を替えてもう一巡する — 引き直しが
    # 効くのは読みが揺れるからで、声を替えれば揺れる幅そのものが変わる。
    if checks and attempts > 1 and len(voices) > 1:
        def still_wrong() -> list[str]:
            return [text for text, _ in misread(list(clips), pairs, model=model, fmt=fmt,
                                                language=language, cache_dir=cache_dir)]
        for _ in range(len(voices) - 1):
            wrong = still_wrong()
            if not wrong:
                break
            for text in wrong:
                pairs[text] = voices[(voices.index(pairs[text]) + 1) % len(voices)]
            more, _ = synthesize_all({t: pairs[t][1] for t in wrong}, key,
                                     model=model, fmt=fmt, cache_dir=cache_dir,
                                     workers=workers, language=language,
                                     checks=checks, attempts=attempts, stt_model=stt_model)
            clips.update(more)
        # どの声でも直らなかった文は、いちばんましだった声のクリップに戻す。
        for text in still_wrong():
            scored = []
            for voice in voices:
                record = heard_record(text, voice[1], model=model, fmt=fmt,
                                      language=language, cache_dir=cache_dir)
                if record is not None:
                    scored.append((len(record.get("missed", [])), voices.index(voice), voice))
            if scored:
                pairs[text] = min(scored)[2]
                cached = cache_dir / f"{cache_key(text, pairs[text][1], model, fmt, language)}.mp3"
                if cached.exists():
                    clips[text] = cached.read_bytes()
    try:
        urls = write_clips(clips, audio_dir_for(page), pairs, model, fmt, language)
    except OSError as error:
        # A TCC-protected output root plus launchd means the same EPERM as above.
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
    if checks and attempts > 1:
        wrong = misread(list(urls), pairs, model=model, fmt=fmt,
                        language=language, cache_dir=cache_dir)
        note += (f"／読み確認 {len(urls) - len(wrong)}/{len(urls)}文一致"
                 + (f"・要確認 {len(wrong)}文" if wrong else ""))
        for text, words in wrong:
            print(f"  読みが合いません（{'・'.join(words)}）: {text}", file=sys.stderr)
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
    parser.add_argument("--revoice", metavar="文",
                        help="この文を --voice の声に固定する（聞いておかしかったとき）。"
                             "--voice を空にすると固定を外す")
    parser.add_argument("--redo", metavar="文",
                        help="この文の録音を捨てて、次のビルドで録り直させる")
    args = parser.parse_args()

    if args.revoice:
        name = args.voice if args.voice != DEFAULT_VOICE else ""
        save_override(args.revoice, name, args.cache)
        print(f"{args.revoice} → {name or '（固定を外しました）'}")
        return 0

    if args.redo:
        dropped = 0
        for name, voice_id in KNOWN_VOICES.items():
            key = cache_key(args.redo, voice_id, args.model, args.fmt, args.language)
            for path in (args.cache / f"{key}.mp3", heard_path(args.cache, key)):
                if path.exists():
                    path.unlink()
                    dropped += 1
        print(f"{args.redo}: {dropped}件を捨てました。次のビルドで録り直します")
        return 0

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
