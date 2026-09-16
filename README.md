# kanji-practice

A [Claude Code](https://claude.com/claude-code) skill that builds a new JLPT
kanji practice page every weekday morning — ten kanji on one theme, with
readings, vocabulary, example sentences, animated stroke order, a graded quiz,
spoken audio for every sentence, and a chat sidebar that answers questions
about the page.

The output is a single self-contained HTML file you open in a browser. There is
no app, no account and no server beyond a small local one: everything runs on
your machine.

```
<output_root>/
└── 2026-08-03〜08-09/                 ← one folder per week
    ├── 漢字練習_2026-08-05.html        ← the day's page
    ├── 漢字練習_2026-08-05_audio/      ← its mp3/m4a clips
    ├── まとめ_2026-08-05.html          ← the day's wrap-up (written on demand)
    └── 復習_2026-08-07.html            ← Friday's weekly review
```

---

## What a page contains

For each of the ten kanji:

- **JLPT level**, on'yomi / kun'yomi, meaning, stroke count and radical.
- **Stroke order**, drawn as an inline SVG animation straight from
  [KanjiVG](https://kanjivg.tagaini.net/) — no images, no GIFs, no JS library.
- **Around ten words** using the character, balanced across its readings, each
  with reading, a Japanese gloss and an English one.
- **Around ten example sentences**, each with a play button.
- **Tracing boxes** to write the character by hand.

Then, for the page as a whole:

- **A quiz** — reading questions on sentences the learner has not seen, graded
  in the browser, with notes explaining each answer.
- **Furigana toggle** — every kanji in every sentence carries ruby text, hidden
  by default and revealed with one button, so a page is readable both ways.
- **Audio** on every example and quiz sentence, in four alternating voices.
- **A chat sidebar** that talks to Claude about anything, and a まとめて button
  that writes a review of how the quiz went.

Your quiz answers and the characters you trace are saved as you work, and are
still there when you come back — see [Saved work](#saved-work).

Once a character has appeared it is never used again: `kanji_history.json`
records every kanji, theme and day, and the model reads it before choosing.

## How it works

```
  SKILL.md              the routine Claude follows: pick a theme, choose ten
    │                   kanji, verify each level on jlptsensei.com, verify each
    │                   reading on jisho.org, write the content
    ▼
  content JSON          one file per day (kanji, words, examples, quiz)
    │
    ▼
  build_page.py         fetches KanjiVG, draws the strokes, calls the TTS
    │                   back end, assembles the HTML
    ├──▶ voicevox.py    local speech synthesis (default)
    ├──▶ tts.py         ElevenLabs (needs an API key)
    └──▶ sbv2.py        Style-BERT-VITS2 (local, heavier; off by default)
    ▼
  漢字練習_<date>.html  + an audio folder beside it

  build_review.py       Friday: rolls the week's pages into one review, hardest
                        kanji first, using the quiz scores in the history
  ask_server.py         a small loopback HTTP server: the page's chat sidebar,
                        the まとめて button, and the durable copy of your
                        answers and drawings (.page_state/)
```

The daily run is `run_daily.sh`, invoked by launchd on weekday mornings. It
wakes the Mac with `caffeinate`, runs `claude -p "/kanji-practice"`, and skips
the day if that day's page already exists. `reenable_daily_kanji.sh` restarts
the schedule after a pause.

## Requirements

- macOS (the scheduling, `afconvert` and the VOICEVOX engine path are
  Mac-specific; the builders themselves are not).
- Claude Code, signed in. No separate API key — the page's chat sidebar shells
  out to the same `claude` binary.
- Python 3.10+. **The builders use only the standard library.**
- [VOICEVOX](https://voicevox.hiroshiba.jp/) for audio — free, local, and the
  default. Optional: pages build without it, just silent.
- Optional: an ElevenLabs key (`.elevenlabs_key`, never committed) if you
  prefer that back end, and a conda env named `kanji` for `kanji_gif.py`.

## Install

See **[INSTALL.md](INSTALL.md)** for the full walkthrough. The short version:

```bash
git clone <this repo> ~/.claude/skills/kanji-practice
cd ~/.claude/skills/kanji-practice
cp config.example.json config.json
$EDITOR config.json            # set output_root
python3 test_build_page.py     # should print OK
claude                         # then type /kanji-practice
```

## Configuration

Nothing in this repo hard-codes a folder on anyone's machine. Everything
machine-specific lives in **`config.json`**, which is not tracked by git — copy
`config.example.json` and edit. Every key is optional; anything you leave out
falls back to the default in `config.py`.

| Key | Default | What it is |
| --- | --- | --- |
| `output_root` | `~/Documents/kanji-practice` | Where the pages, audio folders and logs go. Week folders are created under it. |
| `ask_server_port` | `8765` | Loopback port for the chat/summary server. |
| `voicevox_engine_url` | `http://127.0.0.1:50021` | Where the VOICEVOX engine listens. |
| `voicevox_engine_binary` | `/Applications/VOICEVOX.app/…/vv-engine/run` | The engine binary, started headless if nothing is listening. |
| `launchd_prefix` | `com.kanji-practice` | Label prefix for the launchd jobs. |

Each key can also be overridden by an environment variable — `KANJI_OUTPUT_ROOT`,
`KANJI_ASK_PORT`, `VOICEVOX_ENGINE`, `VOICEVOX_ENGINE_BINARY`,
`KANJI_LAUNCHD_PREFIX` — which wins over the file. Point `KANJI_CONFIG` at
another path to keep your config outside the checkout.

To see what is actually in force and where each value came from:

```bash
python3 config.py
```

### Two locations that are *not* configurable

The **study history** (`kanji_history.json`) and the **per-day content JSON**
(`.content/`) always live in the skill folder, next to the code. This is
deliberate. On macOS, `~/Documents`, `~/Desktop` and `~/Downloads` are
TCC-protected: a process launchd starts can *create* files there but cannot
list the folder or read back what another process wrote. Both of those files
have to be re-read by the Friday review and the chat server, so they have to
sit outside any protected folder — whatever you set `output_root` to.

### Claude Code permissions

`.claude/settings.json` is tracked and deliberately free of absolute paths: it
pre-approves the WebFetch domains and the builder commands, which is all that
is portable. Permissions tied to *your* machine — writing into your output root
— go in `.claude/settings.local.json`, which is not tracked:

```json
{
  "permissions": {
    "allow": [
      "Read(//ABSOLUTE/PATH/TO/YOUR/OUTPUT/ROOT/**)",
      "Edit(//ABSOLUTE/PATH/TO/YOUR/OUTPUT/ROOT/**)",
      "Write(//ABSOLUTE/PATH/TO/YOUR/OUTPUT/ROOT/**)"
    ]
  }
}
```

## Audio

Three back ends, chosen with `--tts-engine`:

- **VOICEVOX** (default, `voicevox.py`) — free, local, deterministic. Four
  speakers alternate through a page. Its `/audio_query` endpoint returns the
  reading *before* synthesising, so a misreading is caught as a fact rather
  than guessed at by ear; the build reports every sentence where the engine's
  reading disagrees with the page's furigana. Fix one with the user dictionary:

  ```bash
  python3 voicevox.py --teach 猿人 エンジン    # teach a reading
  python3 voicevox.py --words                  # list what's taught
  python3 voicevox.py --reading "文"           # reading only, no synthesis
  ```

  Teach **words or phrases, never short ambiguous spellings** — the dictionary
  matches on the written form, so `はち → ハチ` also swallows the particle は in
  unrelated sentences. VOICEVOX requires a credit line; the page footer carries
  it automatically. Don't remove it.

- **ElevenLabs** (`tts.py`) — needs a key in `.elevenlabs_key` or
  `ELEVENLABS_API_KEY`. Costs credits, and verifies readings by transcribing
  the audio back, which is slower and less certain than asking the engine.

- **Style-BERT-VITS2 JP-Extra** (`sbv2.py`) — local, higher quality on paper,
  but judged less natural here. The code is kept; the venv and models are not
  shipped. Asking for it without them falls back to VOICEVOX.

## Saved work

Quiz answers, the strokes you draw in the tracing boxes and the chat thread are
saved as you go, and restored when you reopen the page.

They are kept in **two places**, because one is not enough:

- **`localStorage`** — instant and synchronous, so the page comes back with your
  work already in it before anything else loads. But a page opened as a
  `file://` URL is just "site data" to the browser: **clearing browsing data
  wipes every page's answers and drawings at once**, and a different browser, a
  different profile or a private window never sees them.
- **`.page_state/<page>.json` in the skill folder**, written through
  `ask_server`'s `/state` endpoint. This is the copy that survives all of the
  above. The page mirrors to it as you work (debounced, plus a `sendBeacon` on
  close so the last keystrokes are not lost).

On open, the page reads `localStorage` immediately, then asks the server; each
saved value carries the time it was written, and **the newer copy wins**. That
matters in both directions — you may have worked with the server down, or
cleared your browser with the server holding the only copy.

With `ask_server` not running, everything still works exactly as it did before:
`localStorage` alone, no errors, no waiting.

## Repository layout

| File | What it is |
| --- | --- |
| `SKILL.md` | The routine Claude follows. Written in Japanese; the substance of the skill. |
| `config.py`, `config.example.json` | Per-machine settings and their resolution order. |
| `build_page.py` | Builds the daily page: KanjiVG strokes, furigana, quiz, audio, CSS/JS. |
| `build_review.py` | Builds the Friday weekly review from the history. |
| `ask_server.py` | Loopback HTTP server behind the chat sidebar and まとめて button. |
| `voicevox.py`, `tts.py`, `sbv2.py`, `sbv2_worker.py` | The three speech back ends. |
| `test_build_page.py` | 131 smoke tests. Touches no real data. |
| `run_daily.sh`, `run_ask_server.sh`, `reenable_daily_kanji.sh` | launchd entry points. |
| `com.kanji-practice.*.plist` | The launchd jobs (schedule, keep-alive, logs). |
| `kanji_gif.py`, `kanji_gif_README.md` | Standalone stroke-order GIF maker. Not used by the pages. |
| `INSTALL.md` | Full setup walkthrough. |

Untracked, created as you use it: `config.json`, `.claude/settings.local.json`,
`kanji_history.json`, `.content/`, `.quiz_results/`, `.page_state/`, the caches
(`.kanjivg_cache/`, `.vv_cache/`, `.tts_cache/`, `.sbv2_cache/`) and
`.elevenlabs_key`.

## Development

```bash
python3 test_build_page.py
```

**Do not run `build_page.py` or `build_review.py` by hand with default paths.**
Their defaults point at your live history, quiz results and TTS cache — a
single smoke run will append a fake day to the history, overwrite themes, and
consume pending quiz verdicts. Redirect all four:

```bash
python3 build_page.py content.json \
  --root /tmp/kanji-smoke --history /tmp/kanji-smoke/history.json \
  --content-out /tmp/kanji-smoke/content.json --tts-cache /tmp/kanji-smoke/cache
```

`test_build_page.py` is the safe check; it writes nothing outside its own
temporary directories.

## Credits and licensing

- Stroke data: [KanjiVG](https://kanjivg.tagaini.net/) by Ulrich Apel,
  CC BY-SA 3.0. Fetched at build time and cached locally.
- Level and reading checks: [jlptsensei.com](https://jlptsensei.com) and
  [jisho.org](https://jisho.org).
- Speech: [VOICEVOX](https://voicevox.hiroshiba.jp/) — each page credits the
  speakers it used, as the terms require. The Style-BERT-VITS2 JVNV models are
  CC BY-SA 4.0.

The generated pages are personal study material. Check each upstream project's
terms before redistributing anything built with them.
