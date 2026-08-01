#!/bin/zsh
# 質問サーバーを起動する（launchdから常駐で呼ばれる）
set -u

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
LOG="$HOME/Documents/Claude-JP/漢字/ask_server.log"
mkdir -p "$(dirname "$LOG")"

for c in "/opt/miniconda3" "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "/opt/homebrew/Caskroom/miniconda/base"; do
  if [ -f "$c/etc/profile.d/conda.sh" ]; then
    source "$c/etc/profile.d/conda.sh"
    break
  fi
done

exec python3 "$HOME/.claude/skills/kanji-practice/ask_server.py" >>"$LOG" 2>&1
