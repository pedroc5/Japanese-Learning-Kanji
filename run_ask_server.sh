#!/bin/zsh
# 質問サーバーを起動する（launchdから常駐で呼ばれる。
# 設定は com.pedro.kanji-ask-server.plist）
set -u

# launchd はログインシェルの PATH を引き継がないので、ask_server.py が呼び出す
# claude コマンドの場所を明示的に足しておく。
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
LOG="$HOME/Documents/Claude-JP/漢字/ask_server.log"
mkdir -p "$(dirname "$LOG")"

# conda を使えるようにする（インストール先は環境によって違うので順に探す）
for c in "/opt/miniconda3" "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "/opt/homebrew/Caskroom/miniconda/base"; do
  if [ -f "$c/etc/profile.d/conda.sh" ]; then
    source "$c/etc/profile.d/conda.sh"
    break
  fi
done

# exec で置き換える（このシェルを残さず、launchd が python を直接監視できるように）。
# サーバー自体は標準ライブラリだけで動くので、conda環境ではなく素の python3 でよい。
exec python3 "$HOME/.claude/skills/kanji-practice/ask_server.py" >>"$LOG" 2>&1
