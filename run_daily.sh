#!/bin/zsh
# 平日の漢字練習ページを作る（launchdから呼ばれる。月〜金 10:00）
set -u

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
LOG="$HOME/Documents/Claude-JP/漢字/build.log"
mkdir -p "$(dirname "$LOG")"

# conda を使えるようにする
for c in "/opt/miniconda3" "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "/opt/homebrew/Caskroom/miniconda/base"; do
  if [ -f "$c/etc/profile.d/conda.sh" ]; then
    source "$c/etc/profile.d/conda.sh"
    break
  fi
done

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  cd "$HOME/Documents/Claude-JP" || exit 1
  claude -p "/kanji-practice" --permission-mode acceptEdits
  echo "exit: $?"

  # 金曜日は、その週の全クラスをまとめた復習ページも作る（機械的な集計なのでclaudeは呼ばない）
  if [ "$(date '+%u')" = "5" ]; then
    echo "-- 金曜日：週次復習を作成 --"
    conda run -n kanji python "$HOME/.claude/skills/kanji-practice/build_review.py"
    echo "review exit: $?"
  fi
} >>"$LOG" 2>&1
