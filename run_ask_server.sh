#!/bin/zsh
# 質問サーバーを起動する（launchdから常駐で呼ばれる。
# 設定は com.kanji-practice.ask-server.plist）
set -u

# launchd はログインシェルの PATH を引き継がないので、ask_server.py が呼び出す
# claude コマンドの場所を明示的に足しておく。
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

# ログは出力先の直下。パスは config.py に聞く（run_daily.sh と同じ理由）。
# このスクリプト自身の置き場＝スキルフォルダ。パスを書かずに導くので、
# チェックアウト先がどこでも（~/.claude/skills/ の外でも）そのまま動く。
SKILL="${0:A:h}"
ROOT="$(python3 -c "import sys; sys.path.insert(0, '$SKILL'); import config; print(config.path('output_root'))")"
LOG="${ROOT:-$HOME}/ask_server.log"
mkdir -p "$(dirname "$LOG")"

# exec で置き換える（このシェルを残さず、launchd が python を直接監視できるように）。
# サーバー自体は標準ライブラリだけで動くので、conda環境は要らない（素の python3 でよい）。
exec python3 "$SKILL/ask_server.py" >>"$LOG" 2>&1
