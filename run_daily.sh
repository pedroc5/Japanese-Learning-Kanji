#!/bin/zsh
# 平日の漢字練習ページを作る（launchdから呼ばれる。月〜金 9:00・12:00・16:00
# — 時刻は com.kanji-practice.daily.plist の StartCalendarInterval で決まる）
set -u

# 2026-08-18: 眠っているMacを起こしたままにする。9:00の予約はMacが深い睡眠中で
# 逃され、launchdは次の目覚め（09:11:19のdark wake）に回してくれたのだが、その
# 2秒後にpowerdが「Sleep Service Back to Sleep」で寝かせ直したため、claudeは
# 最初の一文を書いた時点で
#   API Error: Your computer went to sleep mid-response.
# となって死んだ（08-12も同じ死に方。08-13・08-14の欠落もおそらくこれ）。
# 数分かかるネットワーク越しの仕事を2秒のdark wakeの中で始めても終わらないので、
# 実行の頭で自分自身を caffeinate 越しに起動し直し、後片付けまでの全体を起きた
# ままにする。-i は電池運転でも効く（当日は電池66%だった）、-s はAC電源のとき、
# -m はディスクが寝ないように。画面は点けたくないので -u は使わない。
if [ -z "${KANJI_CAFFEINATED:-}" ]; then
  export KANJI_CAFFEINATED=1
  exec /usr/bin/caffeinate -ims "$0" "$@"
fi

# launchd はログインシェルの PATH を引き継がないので、claude のある場所を明示的に
# 足しておく（これがないと "command not found" で毎朝失敗する）。
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

# 出力先はマシンごとに違うので、パスをここで綴り直さず config.py に聞く
# （config.json の output_root、または KANJI_OUTPUT_ROOT で決まる）。綴り直すと
# Python側と二重管理になり、片方だけ直したときに静かにずれる。
# このスクリプト自身の置き場＝スキルフォルダ。パスを書かずに導くので、
# チェックアウト先がどこでも（~/.claude/skills/ の外でも）そのまま動く。
SKILL="${0:A:h}"
ROOT="$(python3 -c "import sys; sys.path.insert(0, '$SKILL'); import config; print(config.path('output_root'))")"
if [ -z "$ROOT" ]; then
  echo "出力先を config.py から読めませんでした。$SKILL/config.json を確認してください。" >&2
  exit 1
fi
LOG="$ROOT/build.log"
mkdir -p "$(dirname "$LOG")"

# この実行が「予約実行そのもの」であることと、内容JSONの置き場をclaudeに教える。
# 2026-08-17: この2つが無かったため、9:25の自動実行が `launchctl list` に
# com.kanji-practice.daily が動いているのを見つけ、それを「別の実行」と誤認して
# 待機に入り、自分自身を待って exit 0 で終わった（ページは1本も作られず）。
# 見つかったのは自分自身だった。KANJI_DAILY_RUN があるときは待たない（SKILL.md 5・9）。
export KANJI_DAILY_RUN=1
# 実行ごとに別のファイルへ書くので、手元の対話セッションと内容を奪い合うことがなくなる
# （共有の /tmp/kanji_content.json が原因の取り違え。2026-08-11に実際に発生）。
export KANJI_CONTENT_JSON="/tmp/kanji_content_daily_$$.json"
trap 'rm -f "$KANJI_CONTENT_JSON"' EXIT

# conda はもう要らない。筆順はインラインSVGで描くようになり、build_page.py も
# build_review.py も標準ライブラリだけで動くため、システムの python3 で足りる。
# （GIFを作る kanji_gif.py だけは今も kanji 環境を使うが、日次では呼ばない。）

TODAY="$(date '+%Y-%m-%d')"
# 週フォルダの名前は build_page.week_folder が決める。ここで綴り直すと二重管理に
# なるので、そのまま呼んで聞く（パスを組み立てるだけでファイルには触らない）。
WEEK="$(python3 -c "import sys; sys.path.insert(0, '$SKILL'); import build_page; print(build_page.week_folder('$TODAY'))")"
PAGE="$ROOT/$WEEK/漢字練習_$TODAY.html"
REVIEW="$ROOT/$WEEK/復習_$TODAY.html"

# このブロックの出力（標準出力・標準エラーの両方）をまとめてログに追記する
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  # スキルフォルダ自体をカレントディレクトリにする。ここに .claude/settings.json があるので
  # 自動実行に必要な権限（WebFetch/Bash/Edit）がこのディレクトリ基準で読み込まれる…はずだったが、
  # headless の `-p` ではパス限定の許可ルール（Read(path/**) など）は、settings.json /
  # settings.local.json のどちらに書いてあっても一切評価されない（trust dialogを出せないため
  # 黙って無視される。ask_server.py の run_claude() で実証済み）。そのせいで
  # kanji_history.json（スキルフォルダの外）を読めず、権限を尋ねる文章だけ出して exit 0 で
  # 終わり、ページが1本も生成されない日が続いていた。ask_server.py と同じく、ベア名の
  # --allowedTools で明示的に許可する（パス限定ではなくツール単位の許可なので headless でも効く）。
  cd "$SKILL" || exit 1

  # 2026-08-18: 1日に3回（9:00・12:00・16:00）起きるようにしたので、既にできて
  # いる日は何もしない。朝の失敗を昼と夕方に取り返すための予約で、成功した日に
  # 二度書きするためのものではない。判定に迷ったとき（ページの有無が読めない等）は
  # 「作る」側に倒れるので、黙って一日飛ばすことはない。
  if [ -f "$PAGE" ]; then
    echo "今日のページは既にある（$PAGE）。claudeは呼ばない。"
  else
    # 失敗しても、その場でもう一度だけ試す。caffeinate で起きたままなので、
    # 睡眠が原因の失敗なら2回目は通る。セッション上限のように待っても直らない
    # 失敗は、次の予約（昼・夕方）に任せる。
    for attempt in 1 2; do
      if [ "$attempt" -gt 1 ]; then
        echo "-- 1回目でページができなかったので再試行（$attempt 回目）--"
        sleep 60
      fi
      claude -p "/kanji-practice" --permission-mode acceptEdits \
        --allowedTools "Read Write Edit Bash WebFetch"
      echo "exit: $?"
      if [ -f "$PAGE" ]; then
        break
      fi
      echo "ページができていない：$PAGE"
    done
  fi

  # 金曜日は、その週の全クラスをまとめた復習ページも作る（機械的な集計なのでclaudeは呼ばない）
  # 復習ページも「無ければ作る」。2026-08-07 は日次ページの後で復習だけが
  # PermissionError で落ちたので、日次が成功した日でも復習を作り直せるように、
  # 上の早期スキップとは別に判定する。
  if [ "$(date '+%u')" = "5" ] && [ ! -f "$REVIEW" ]; then
    echo "-- 金曜日：週次復習を作成 --"
    python3 "$SKILL/build_review.py"
    echo "review exit: $?"
  fi
} >>"$LOG" 2>&1
