#!/bin/zsh
# 平日の漢字練習ページを作る（launchdから呼ばれる。月〜金 9:00
# — 時刻は com.pedro.kanji-daily.plist の StartCalendarInterval で決まる）
set -u

# launchd はログインシェルの PATH を引き継がないので、claude のある場所を明示的に
# 足しておく（これがないと "command not found" で毎朝失敗する）。
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
LOG="$HOME/Documents/Claude-JP/漢字/build.log"
mkdir -p "$(dirname "$LOG")"

# conda はもう要らない。筆順はインラインSVGで描くようになり、build_page.py も
# build_review.py も標準ライブラリだけで動くため、システムの python3 で足りる。
# （GIFを作る kanji_gif.py だけは今も kanji 環境を使うが、日次では呼ばない。）

# このブロックの出力（標準出力・標準エラーの両方）をまとめてログに追記する
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  # スキルフォルダ自体をカレントディレクトリにする。ここに .claude/settings.json があるので
  # 自動実行に必要な権限（WebFetch/Bash/Edit）がこのディレクトリ基準で読み込まれる。
  # 出力先(~/Documents/Claude-JP/漢字/)は変わらず絶対パスのまま。
  cd "$HOME/.claude/skills/kanji-practice" || exit 1
  claude -p "/kanji-practice" --permission-mode acceptEdits
  echo "exit: $?"

  # 金曜日は、その週の全クラスをまとめた復習ページも作る（機械的な集計なのでclaudeは呼ばない）
  if [ "$(date '+%u')" = "5" ]; then
    echo "-- 金曜日：週次復習を作成 --"
    python3 "$HOME/.claude/skills/kanji-practice/build_review.py"
    echo "review exit: $?"
  fi
} >>"$LOG" 2>&1
