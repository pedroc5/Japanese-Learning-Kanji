# Setup — daily kanji practice on your Mac

Everything runs locally: your `kanji` conda env, your network, your files.

## 1. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
claude          # first run: sign in
```

## 2. Install the skill

```bash
mkdir -p ~/.claude/skills
cp -R ~/Documents/Claude-JP/kanji-skill ~/.claude/skills/kanji-practice
```

The folder holds `SKILL.md` (the routine), `build_page.py` (page builder) and
`gifdec.js` (GIF player embedded in the page).

## 3. Check the environment

```bash
conda run -n kanji python -c "import svgpathtools, PIL, requests; print('ok')"
conda run -n kanji python ~/Documents/Kanji-gif-creator/kanji_gif.py \
  https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/06cca.svg \
  --outdir /tmp/gt --size 240 --grid && open /tmp/gt/06cca.gif
```

If a GIF opens showing 泊 being written stroke by stroke, the pipeline works.

## 4. Try it once by hand

```bash
cd ~/Documents/Claude-JP
claude
```

Then type `/kanji-practice` and let it run. Approve the tool prompts. The page
lands in `~/Documents/Claude-JP/漢字/`.

## 5. Schedule it

`run_daily.sh` in this folder wraps the whole thing. Make it executable and
register the launchd job:

```bash
chmod +x ~/.claude/skills/kanji-practice/run_daily.sh
cp ~/.claude/skills/kanji-practice/com.pedro.kanji-daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pedro.kanji-daily.plist
```

It runs every day at 07:30. To change the time, edit `StartCalendarInterval` in
the plist, then `launchctl unload` + `load` it again.

Run it right now to test:

```bash
launchctl start com.pedro.kanji-daily
tail -f ~/Documents/Claude-JP/漢字/build.log
```

To stop it permanently:

```bash
launchctl unload ~/Library/LaunchAgents/com.pedro.kanji-daily.plist
```

## Notes

- The first scheduled run may fail on permissions. Run it interactively once
  (step 4) so Claude Code remembers the approvals for that folder.
- `run_daily.sh` assumes miniconda at `~/miniconda3`; it also tries
  `~/anaconda3`. Edit the path if yours differs.
- Old GIFs are cached in `~/Documents/Claude-JP/漢字/gif/`, so repeated kanji
  cost nothing.
- Once this is running, you can delete the Cowork scheduled task so you don't
  get two pages a day.
