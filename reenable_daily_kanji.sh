#!/bin/zsh
# 一時停止した毎日の漢字練習（com.kanji-practice.daily）を再開する。
# 2026-08-12 に「今週は自動生成を止める」と頼まれて止めたもの。
# 再開したい日（例：月曜 2026-08-17）に、このスクリプトを一度実行するだけでよい。
# 金曜の週次復習（build_review.py）は run_daily.sh の中から呼ばれるので、
# これを再開すれば復習ページも自動で戻る。
set -u

# ラベルは config.json の launchd_prefix に従う（既定 com.kanji-practice）。
# このスクリプト自身の置き場＝スキルフォルダ。パスを書かずに導くので、
# チェックアウト先がどこでも（~/.claude/skills/ の外でも）そのまま動く。
SKILL="${0:A:h}"
LABEL="$(python3 -c "import sys; sys.path.insert(0, '$SKILL'); import config; print(config.get('launchd_prefix'))").daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl enable "gui/$UID/$LABEL"
launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null

if launchctl list | grep -q "$LABEL"; then
  echo "$LABEL を再開しました（平日9:00に自動実行）。"
else
  echo "$LABEL の再開に失敗しました。$PLIST を確認してください。" >&2
  exit 1
fi
