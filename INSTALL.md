# Setup — daily kanji practice on your Mac

Everything runs locally: your machine, your network, your files. Nothing in
this repo hard-codes a folder on anyone's computer; the two locations that
differ per machine — where the pages go and where your Claude Code permissions
for that folder live — are set once in `config.json` and
`.claude/settings.local.json`, neither of which is tracked by git.

The skill folder is self-contained: the builders, the SVG cache, the study
history and the per-day content JSON all live inside it. Only the generated
pages (`漢字練習_*.html`, `復習_*.html`, `まとめ_*.html`) and the logs go to the
**output root** you configure.

The study record (`kanji_history.json`) and the per-day content JSON
(`.content/content_*.json`) are deliberately kept in the skill folder instead.
On macOS, `~/Documents`, `~/Desktop` and `~/Downloads` are TCC-protected: a
launchd-spawned process can create new files there but cannot list the folder
or read back files another process wrote, so anything the scheduled run has to
*re-read* has to live outside it — whatever you set the output root to.

## 1. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
claude          # first run: sign in
```

## 2. Install the skill

```bash
mkdir -p ~/.claude/skills
git clone <this repo> ~/.claude/skills/kanji-practice
```

The folder holds `SKILL.md` (the routine), `config.py`/`config.example.json`
(the per-machine settings), `build_page.py`/`build_review.py` (page builders),
`test_build_page.py` (their smoke tests), `ask_server.py` (the local
chat/summary server), `voicevox.py`/`tts.py`/`sbv2.py` (the three
text-to-speech back ends), `kanji_gif.py` (a standalone stroke-order GIF maker,
no longer used by the pages), and `.claude/settings.json` (pre-approved
permissions for unattended runs).

The scripts locate the skill folder from their own path, so the clone does not
have to sit under `~/.claude/skills` — only the two launchd plists name that
location, and they are the one place to edit if you put it elsewhere.

## 3. Configure

```bash
cd ~/.claude/skills/kanji-practice
cp config.example.json config.json
$EDITOR config.json          # at minimum, set output_root
python3 config.py            # prints the settings in force and where each came from
```

Every key is optional; leaving one out falls back to the default in
`config.py`. See the README for what each key does.

Then tell Claude Code it may write to that folder, in the **untracked** local
settings file (the tracked `.claude/settings.json` stays path-free so the repo
works on any machine):

```bash
cat > .claude/settings.local.json <<'JSON'
{
  "permissions": {
    "allow": [
      "Read(//ABSOLUTE/PATH/TO/YOUR/OUTPUT/ROOT/**)",
      "Edit(//ABSOLUTE/PATH/TO/YOUR/OUTPUT/ROOT/**)",
      "Write(//ABSOLUTE/PATH/TO/YOUR/OUTPUT/ROOT/**)"
    ]
  }
}
JSON
```

## 4. Check the environment

The page builders need only the standard library — they draw the stroke order
as inline SVG straight from KanjiVG, so no conda environment is involved:

```bash
python3 test_build_page.py
```

If that reports `OK`, the builders work. It touches no real data: never run
`build_page.py` by hand without redirecting `--root`, `--history`,
`--content-out` and `--tts-cache`, since their defaults point at your live
history and caches.

`kanji_gif.py` is a separate tool and is the only thing that still wants the
`kanji` conda environment:

```bash
conda run -n kanji python -c "import svgpathtools, PIL, requests; print('ok')"
```

## 5. Try it once by hand

```bash
cd ~/.claude/skills/kanji-practice
claude
```

Then type `/kanji-practice` and let it run. Running from this folder means the
permissions in `.claude/settings.json` (plus your `settings.local.json`) apply
automatically, so it won't stop to ask for WebFetch/Bash/Edit approval. The
page lands under your configured `output_root`.

## 6. Schedule the daily page

`run_daily.sh` wraps the whole thing and `cd`s into the skill folder itself
before invoking Claude (so the same pre-approved permissions apply). Make it
executable and register the launchd job:

```bash
chmod +x run_daily.sh
cp com.kanji-practice.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kanji-practice.daily.plist
```

It runs Monday–Friday at 9:00, 12:00 and 16:00 — the later two only act if the
morning run left no page — and builds a Friday weekly recap. To change the
schedule, edit `StartCalendarInterval` in the plist, then `launchctl unload` +
`load` it again. If your clone is not at `~/.claude/skills/kanji-practice`,
edit the path in `ProgramArguments` first.

Run it right now to test:

```bash
launchctl start com.kanji-practice.daily
tail -f "$(python3 -c 'import config; print(config.path("output_root"))')/build.log"
```

To stop it permanently:

```bash
launchctl unload ~/Library/LaunchAgents/com.kanji-practice.daily.plist
```

`reenable_daily_kanji.sh` puts it back after a temporary pause.

## 7. Start the chat/summary server

The page's chat sidebar and "まとめて" summary button need a small local server
running in the background:

```bash
chmod +x run_ask_server.sh
cp com.kanji-practice.ask-server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kanji-practice.ask-server.plist
```

It starts at login and restarts itself if it crashes (`KeepAlive`), listening
on `http://127.0.0.1:8765` (`ask_server_port` in `config.json`). Logs go to
`<output_root>/ask_server.log`. Check it's up with
`launchctl list | grep kanji-ask-server`.

This server also keeps the durable copy of your quiz answers and traced
characters, in `.page_state/`. Without it the pages still save your work, but
only in the browser's own storage — which clearing browsing data erases. Run it
if you want that work to last.

After changing `ask_server.py`, reload it — the running copy keeps the old code:

```bash
launchctl kickstart -k gui/$(id -u)/com.kanji-practice.ask-server
```

## Notes

- The first scheduled run may fail on permissions if `.claude/settings.json`
  doesn't yet cover something a question happens to need — check
  `<output_root>/build.log` / `ask_server.log`.
- Neither `run_daily.sh` nor `run_ask_server.sh` needs conda — the builders and
  the server are standard library only. `kanji_gif.py` is the one remaining
  thing that wants the `kanji` environment.
- Raw KanjiVG SVGs are cached in `.kanjivg_cache/` inside the skill folder.
  Repeated kanji cost nothing.
- Once this is running, delete any other scheduled task doing the same thing so
  you don't get two pages a day.

## Changing the launchd labels

The jobs are labelled `com.kanji-practice.daily` and
`com.kanji-practice.ask-server`. To use a different prefix, set
`launchd_prefix` in `config.json` (which is what `reenable_daily_kanji.sh`
reads), then rename the plists and their `Label` keys to match.

If you already had jobs loaded under a different label, an already-loaded job
keeps running under the old one — unload and remove it before loading the new:

```bash
OLD=<the old label>
launchctl unload ~/Library/LaunchAgents/$OLD.plist
rm ~/Library/LaunchAgents/$OLD.plist
launchctl list | grep kanji      # should show only the new labels
```
