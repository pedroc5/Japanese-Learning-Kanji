# Setup — daily kanji practice on your Mac

Everything runs locally: your `kanji` conda env, your network, your files.
The skill folder is self-contained — the GIF-maker script, its SVG cache, and
the permission settings for unattended runs all live inside
`~/.claude/skills/kanji-practice/`. Only the generated pages themselves
(`content_*.json`, `漢字練習_*.html`, `kanji_history.json`, the GIF cache) live
under `~/Documents/Claude-JP/漢字/` — that output location never changes.

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
(page builders), `ask_server.py` (the local chat/summary server),
`kanji_gif.py` (stroke-order GIF maker), `gifdec.js` (GIF player embedded in
the page), and `.claude/settings.json` (pre-approved permissions for
unattended runs).

## 3. Check the environment

```bash
conda run -n kanji python -c "import svgpathtools, PIL, requests; print('ok')"
conda run -n kanji python ~/.claude/skills/kanji-practice/kanji_gif.py \
  https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/06cca.svg \
  --outdir /tmp/gt --size 240 --grid && open /tmp/gt/06cca.gif
```

If a GIF opens showing 泊 being written stroke by stroke, the pipeline works.

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

The page's chat sidebar, "まとめて" summary button, and furigana toggle need a
small local server running in the background:

```bash
chmod +x ~/.claude/skills/kanji-practice/run_ask_server.sh
cp ~/.claude/skills/kanji-practice/com.pedro.kanji-ask-server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pedro.kanji-ask-server.plist
```

It starts at login and restarts itself if it crashes (`KeepAlive`), listening
on `http://127.0.0.1:8765`. Logs: `~/Documents/Claude-JP/漢字/ask_server.log`.
Check it's up with `launchctl list | grep kanji-ask-server`.

## Notes

- The first scheduled run may fail on permissions if `.claude/settings.json`
  doesn't yet cover something a question happens to need — check
  `~/Documents/Claude-JP/漢字/build.log` / `ask_server.log`.
- `run_daily.sh`/`run_ask_server.sh` look for conda at `/opt/miniconda3` first,
  then `~/miniconda3`, `~/anaconda3`, `~/miniforge3`. Edit the path if yours
  differs.
- Old GIFs are cached in `~/Documents/Claude-JP/漢字/gif/`; raw KanjiVG SVGs
  are cached in `~/.claude/skills/kanji-practice/.kanjivg_cache/`. Repeated
  kanji cost nothing.
- Once this is running, you can delete any other scheduled task doing the
  same thing so you don't get two pages a day.
