#!/usr/bin/env python3
"""Local HTTP server the kanji-practice pages call to chat with Claude live
and show the answer inline, instead of copy-pasting into a terminal.

Runs as a background launchd service (com.pedro.kanji-ask-server.plist,
started via run_ask_server.sh) so it's already up whenever a practice page
is opened. Each browser-side conversation (a random id generated when the
page loads) is mapped to a Claude Code session id, so follow-up questions
use `claude -p --resume <session-id>` and keep the thread of conversation —
no separate API key, just Pedro's existing Claude Code login.

    python ask_server.py [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_page as bp  # noqa: E402  (reuse the same CSS as 漢字練習_*.html for a consistent look)

CWD = Path(__file__).resolve().parent  # スキルフォルダ。.claude/settings.json の権限がここ基準で読み込まれる
TIMEOUT = 180
SUMMARY_TIMEOUT = 240

_sessions: dict[str, str] = {}   # conversation_id (browser-side) -> claude session_id
_lock = threading.Lock()

# サイドバーのチャット用の指示。質問文には混ぜず --append-system-prompt で渡すので、
# 会話ログには出ず、--resume で続く各ターンにも同じように効く。
ASK_SYSTEM_PROMPT = (
    "Answer as a Japanese language teacher, correcting incorrect or unnatural "
    "formulations. Use examples, definitions, and when possible backup your "
    "answer on existing documents."
)


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/ask":
            self._handle_ask()
        elif self.path == "/summarize":
            self._handle_summarize()
        else:
            self._send_json(404, {"error": "not found"})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _handle_ask(self) -> None:
        try:
            body = self._read_json()
            question = (body.get("question") or "").strip()
            conv_id = (body.get("conversation_id") or "").strip()
        except Exception:
            question, conv_id = "", ""
        if not question:
            self._send_json(400, {"error": "質問が空です。"})
            return

        with _lock:
            resume_sid = _sessions.get(conv_id) if conv_id else None

        # 素の "-p" ヘッドレス実行では、settings.json/settings.local.json の
        # パス限定ルール（例: Read(path/**)）は一切評価されず、常に権限拒否になる
        # （ワークスペース信頼ダイアログが出せないため）。ツール名だけを渡す
        # --allowedTools は素の "-p" でも効くので、こちらで代替する。
        cmd = ["claude", "-p", question, "--permission-mode", "acceptEdits",
               "--allowedTools", "Read WebFetch",
               "--output-format", "json",
               "--append-system-prompt", ASK_SYSTEM_PROMPT]
        if resume_sid:
            cmd += ["--resume", resume_sid]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=str(CWD))
            try:
                data = json.loads(r.stdout)
            except json.JSONDecodeError:
                data = None

            if data and data.get("session_id"):
                if conv_id:
                    with _lock:
                        _sessions[conv_id] = data["session_id"]
                answer = (data.get("result") or "").strip() or "(応答がありませんでした)"
                self._send_json(200, {"answer": answer})
            else:
                fallback = (r.stdout or "").strip() or (r.stderr or "").strip() or "(応答がありませんでした)"
                self._send_json(200, {"answer": fallback})
        except subprocess.TimeoutExpired:
            self._send_json(504, {"error": "応答がタイムアウトしました。もう一度お試しください。"})
        except FileNotFoundError:
            self._send_json(500, {"error": "claude コマンドが見つかりません（PATHを確認してください）。"})
        except Exception as e:                        # noqa: BLE001
            self._send_json(500, {"error": f"エラー: {e}"})

    def _handle_summarize(self) -> None:
        """Builds and writes まとめ_<date>.html for today's class.

        Deliberately never touches ~/Documents/Claude-JP from this Python
        process: a launchd-spawned python3 has no Full Disk / Files-and-
        Folders grant for that (TCC-protected) folder and any direct
        os/pathlib read here fails with EPERM, even though the very same
        code worked when the server was started by hand from Terminal
        (which inherits Terminal's own grant). The `claude` binary itself
        is unaffected by that — it already reads/writes there fine — so
        all file access is delegated to the nested `claude -p` call. Quiz
        scoring is done by the browser (which already has the answer key
        for its own "check answers" button) and sent over already graded,
        so this file never needs to parse the quiz key either.

        The nested `claude -p` call itself needs `--allowedTools` with bare
        tool names (e.g. "Read Write Edit Bash"). Path-scoped rules like
        `Read(path/**)` — whether in .claude/settings.json,
        settings.local.json, or passed via `--allowedTools "Read(path/**)"`
        — are silently never evaluated in headless "-p" mode (no workspace
        trust dialog can be shown, so scoped rules are dropped entirely and
        every matching tool call is denied). Verified empirically: only
        bare, unscoped tool names in --allowedTools are honored headlessly.

        All four (Read/Write/Edit/Bash) are allow-listed because the model
        doesn't reliably pick the same tool for the same task run to run —
        e.g. it may Write a fresh file on one run but Edit the existing
        まとめ_<date>.html on the next (since the prompt says overwriting
        is fine), or occasionally shell out with cat/ls instead of Read.
        The prompt still asks for Read/Write and says not to use Bash, as
        a nudge, but a denial on any one unlisted tool fails the whole
        call, so all four stay allow-listed as a safety net regardless.
        """
        try:
            body = self._read_json()
            content_file = (body.get("content_file") or "").strip()
            quiz_results = body.get("quiz_results") or []
            chat = body.get("chat") or []
        except Exception:
            self._send_json(400, {"error": "リクエストが不正です。"})
            return

        if not content_file:
            self._send_json(400, {"error": "今日のcontent JSONの場所が分かりませんでした。"})
            return

        # content_<date>.json という命名規則から日付を取り出す（ファイルを開かずに済む）
        name_match = re.match(r"content_(.+)\.json$", Path(content_file).name)
        d = name_match.group(1) if name_match else "today"
        summary_path = str(PurePosixPath(content_file).parent / f"まとめ_{d}.html")

        quiz_lines = [
            f"- 問題「{r.get('q', '')}」／Pedroの解答「{r.get('given') or '(空欄)'}」／判定：{r.get('verdict', '?')}"
            for r in quiz_results
        ]
        quiz_block = "\n".join(quiz_lines) or "(クイズの解答はまだ入力されていません)"

        chat_lines = [
            f"{'Pedro' if m.get('who') == 'you' else 'Claude'}: {m.get('text', '')}"
            for m in chat if m.get("text")
        ]
        chat_block = "\n".join(chat_lines) or "(チャットでの質問はありませんでした)"

        skeleton = (
            "<!DOCTYPE html>\n<html lang=\"ja\">\n<head>\n<meta charset=\"UTF-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>今日の振り返り {d}</title>\n<style>\n{bp.CSS}\n</style>\n</head>\n<body>\n"
            "<div class=\"page\"><div class=\"wrap\">\n"
            "<h1>今日の振り返り</h1>\n"
            f"<p class=\"sub\">{d}　テーマ：<b>(ここにテーマ)</b></p>\n"
            "<div class=\"box warm\">(ここに今日の一言まとめ)</div>\n"
            "<h2>1. 今日学んだ漢字の要点</h2>\n(ここに内容)\n"
            "<h2>2. クイズの結果</h2>\n(ここに内容)\n"
            "<h2>3. チャットから追加で学んだこと</h2>\n"
            "(該当する知識があるときだけ。単語と意味・読みを短く列挙する。なければこの見出しごと省略)\n"
            "</div></div>\n</body>\n</html>"
        )

        prompt = (
            "（ファイル操作はReadツールとWriteツールだけを使ってください。"
            "catやlsなどのBashは使わないでください。）\n\n"
            f"{content_file} を読んで、今日の漢字練習（Pedro、JLPT学習者向け）の振り返りまとめを"
            "作成してください。クイズの正誤はブラウザ側で既に判定済みなので、以下の結果をそのまま使い、"
            "ファイルを読み直して採点し直す必要はありません。\n\n"
            f"【クイズの結果（ブラウザ側で判定済み）】\n{quiz_block}\n\n"
            f"【今日のチャットでの会話（そのままの文字起こし。まとめへの転記用ではなく、"
            f"知識を拾うための参考情報）】\n{chat_block}\n\n"
            "上記を踏まえて、今日のクラスの振り返りまとめを日本語で作成し、Writeツールで次のファイルに"
            f"HTMLとして書き込んでください（既存ファイルがあれば上書きしてよい）：{summary_path}\n\n"
            "まとめに含める内容：\n"
            "1. 今日のテーマと学んだ漢字の要点（読み・意味を簡潔に、ファイルの内容から）\n"
            "2. クイズの結果（正解・不正解の傾向、間違えやすかった読み方があれば指摘）\n"
            "3. チャットから追加で学んだこと — **チャットのやり取りそのものは書き写さない。**"
            "そこから得られた日本語の知識（単語とその意味・読み、文法の説明、漢字の使い分けなど）だけを、"
            "他の単語と同じような形式で短く追加する。例えば「薬局の読み方は？」→「やっきょく」という"
            "やり取りがあれば、まとめには「薬局：やっきょく（薬を売る店）」のように書く。"
            "雑談など日本語学習に関係ない内容しかなければ、この項目自体を省略する。\n\n"
            "**見た目は今日の漢字練習ページ（漢字練習_*.html）と同じデザインで統一してください。**"
            "そのために、次のHTML骨格をそのまま使い、<style>の中身はこのCSSをそのまま丸ごとコピーして"
            "埋め込んでください（変更しない）。(ここに内容)の部分だけ、実際のまとめ内容"
            "（見出し・段落・<ul>や<table>を適宜使う）に置き換えてください：\n\n"
            f"```html\n{skeleton}\n```"
        )

        try:
            r = subprocess.run(
                ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
                 "--allowedTools", "Read Write Edit Bash", "--output-format", "json"],
                capture_output=True, text=True, timeout=SUMMARY_TIMEOUT, cwd=str(CWD),
            )
            try:
                data = json.loads(r.stdout)
            except json.JSONDecodeError:
                data = None

            denials = (data or {}).get("permission_denials") or []
            if data is not None and not data.get("is_error") and not denials:
                self._send_json(200, {"file": summary_path})
            elif denials:
                # is_error is often still false here — claude just explained it needs
                # permission instead of erroring, so a naive is_error check reports a
                # false "success" even though nothing was written. This came up when
                # the skill folder's workspace trust wasn't accepted yet: settings.json's
                # allow-list gets silently ignored, so acceptEdits doesn't actually
                # auto-approve Write, and it gets denied instead of accepted.
                tools = "、".join(sorted({d.get("tool_name", "?") for d in denials}))
                self._send_json(500, {
                    "error": f"まとめを書き込めませんでした（権限が拒否されました: {tools}）。"
                             "スキルフォルダの信頼ダイアログが未承認の可能性があります。"
                })
            else:
                msg = (data or {}).get("result") if data else None
                msg = msg or (r.stdout or r.stderr or "").strip() or "まとめの作成に失敗しました。"
                self._send_json(500, {"error": msg})
        except subprocess.TimeoutExpired:
            self._send_json(504, {"error": "まとめの作成がタイムアウトしました。もう一度お試しください。"})
        except FileNotFoundError:
            self._send_json(500, {"error": "claude コマンドが見つかりません（PATHを確認してください）。"})
        except Exception as e:                         # noqa: BLE001
            self._send_json(500, {"error": f"エラー: {e}"})


def main() -> int:
    p = argparse.ArgumentParser(description="漢字練習ページ用のローカル会話サーバー")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"kanji ask_server listening on http://127.0.0.1:{a.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
