# Setup — daily kanji practice on your Mac

Everything runs locally: your `kanji` conda env, your network, your files.
The skill folder is self-contained — the GIF-maker script, its SVG cache, and
the permission settings for unattended runs all live inside
`~/.claude/skills/kanji-practice/`. Only the generated pages themselves
(`漢字練習_*.html`, `復習_*.html`, `まとめ_*.html`) and the logs live under
`~/Documents/Claude-JP/漢字/` — that output location never changes.

The study record (`kanji_history.json`) and the per-day content JSON
(`.content/content_*.json`) are deliberately kept in the skill folder instead.
`~/Documents` is TCC-protected: a launchd-spawned process can create new files
there but cannot list the folder or read back files another process wrote, so
anything the scheduled run has to *re-read* has to live outside it.

## 1. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
claude          # first run: sign in
```

## 2. Install the skill

```bash
mkdir -p ~/.claude/skills
cp -R kanji-practice ~/.claude/skills/kanji-practice
```

The folder holds `SKILL.md` (the routine), `build_page.py`/`build_review.py`
(page builders), `test_build_page.py` (their smoke tests), `ask_server.py`
(the local chat/summary server), `kanji_gif.py` (a standalone stroke-order GIF
maker, no longer used by the pages), and `.claude/settings.json` (pre-approved
permissions for unattended runs).

## 3. Check the environment

The page builders need only the standard library — they draw the stroke order
as inline SVG straight from KanjiVG, so no conda environment is involved:

```bash
python3 ~/.claude/skills/kanji-practice/test_build_page.py
```

If that reports `OK`, the builders work. `kanji_gif.py` is a separate tool and
is the only thing that still wants the `kanji` environment:

```bash
conda run -n kanji python -c "import svgpathtools, PIL, requests; print('ok')"
conda run -n kanji python ~/.claude/skills/kanji-practice/kanji_gif.py \
  https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/06cca.svg \
  --outdir /tmp/gt --size 240 --grid && open /tmp/gt/06cca.gif
```

## 4. Try it once by hand

```bash
cd ~/.claude/skills/kanji-practice
claude
```

Then type `/kanji-practice` and let it run. Running from this folder (rather
than `~/Documents/Claude-JP`) means the permissions in `.claude/settings.json`
apply automatically, so it won't stop to ask for WebFetch/Bash/Edit approval.
The page still lands in `~/Documents/Claude-JP/漢字/`.

## 5. Schedule the daily page

`run_daily.sh` wraps the whole thing and `cd`s into this skill folder itself
before invoking Claude (so the same pre-approved permissions apply). Make it
executable and register the launchd job:

```bash
chmod +x ~/.claude/skills/kanji-practice/run_daily.sh
cp ~/.claude/skills/kanji-practice/com.pedro.kanji-daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pedro.kanji-daily.plist
```

It runs Monday–Friday at 10:00 (also builds a Friday weekly recap). To change
the schedule, edit `StartCalendarInterval` in the plist, then `launchctl
unload` + `load` it again.

Run it right now to test:

```bash
launchctl start com.pedro.kanji-daily
tail -f ~/Documents/Claude-JP/漢字/build.log
```

To stop it permanently:

```bash
launchctl unload ~/Library/LaunchAgents/com.pedro.kanji-daily.plist
```

## 6. Start the chat/summary server

The page's chat sidebar and "まとめて" summary button need a small local server
running in the background:

```bash
chmod +x ~/.claude/skills/kanji-practice/run_ask_server.sh
cp ~/.claude/skills/kanji-practice/com.pedro.kanji-ask-server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pedro.kanji-ask-server.plist
```

It starts at login and restarts itself if it crashes (`KeepAlive`), listening
on `http://127.0.0.1:8765`. Logs: `~/Documents/Claude-JP/漢字/ask_server.log`.
Check it's up with `launchctl list | grep kanji-ask-server`.

After changing `ask_server.py`, reload it — the running copy keeps the old code:

```bash
launchctl kickstart -k gui/$(id -u)/com.pedro.kanji-ask-server
```

## Notes

- The first scheduled run may fail on permissions if `.claude/settings.json`
  doesn't yet cover something a question happens to need — check
  `~/Documents/Claude-JP/漢字/build.log` / `ask_server.log`.
- Neither `run_daily.sh` nor `run_ask_server.sh` needs conda any more — the
  builders and the server are standard library only. `kanji_gif.py` is the one
  remaining thing that wants the `kanji` environment.
- Raw KanjiVG SVGs are cached in
  `~/.claude/skills/kanji-practice/.kanjivg_cache/`. Repeated kanji cost nothing.
- Once this is running, you can delete any other scheduled task doing the
  same thing so you don't get two pages a day.
