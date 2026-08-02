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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CWD = Path(__file__).resolve().parent  # スキルフォルダ。.claude/settings.json の権限がここ基準で読み込まれる
TIMEOUT = 180
SUMMARY_TIMEOUT = 240

_sessions: dict[str, str] = {}   # conversation_id (browser-side) -> claude session_id
_lock = threading.Lock()


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

        cmd = ["claude", "-p", question, "--permission-mode", "acceptEdits",
               "--output-format", "json"]
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
        try:
            body = self._read_json()
            content_file = (body.get("content_file") or "").strip()
            quiz_results = body.get("quiz_results") or []
            chat = body.get("chat") or []
        except Exception:
            self._send_json(400, {"error": "リクエストが不正です。"})
            return

        cf = Path(content_file) if content_file else None
        if not cf or not cf.exists():
            self._send_json(400, {"error": "今日のcontent JSONが見つかりませんでした。"})
            return

        try:
            content = json.loads(cf.read_text(encoding="utf-8"))
        except Exception as e:                         # noqa: BLE001
            self._send_json(500, {"error": f"content JSONの読み込みに失敗しました: {e}"})
            return

        d = content.get("date", "")
        theme = content.get("theme", "")
        kanji_chars = "、".join(k.get("char", "") for k in content.get("kanji", []))

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

        summary_path = cf.parent / f"まとめ_{d}.html"
        prompt = (
            f"今日（{d}）の漢字練習「{theme}」（{kanji_chars}）について、Pedro（JLPT学習者）向けの"
            "振り返りまとめを作りたいです。\n\n"
            f"【今日の内容ファイル】{cf}\n\n"
            f"【クイズの結果】\n{quiz_block}\n\n"
            f"【チャットでの質問・会話】\n{chat_block}\n\n"
            "上記を踏まえて、今日のクラスの振り返りまとめを日本語で作成し、Writeツールで次のファイルに"
            f"HTMLとして書き込んでください（既存ファイルがあれば上書きしてよい）：{summary_path}\n\n"
            "まとめに含める内容：\n"
            "1. 今日のテーマと学んだ漢字の要点（読み・意味を簡潔に）\n"
            "2. クイズの結果（正解・不正解の傾向、間違えやすかった読み方があれば指摘）\n"
            "3. チャットで質問した内容の振り返り（特に単語・語彙について質問していれば、その説明を"
            "まとめにも記載する。チャットがなければこの項目は省略してよい）\n"
            "見出しやリストを使って読みやすく、簡潔にまとめてください。他のページのような凝ったCSSは"
            "不要で、シンプルなHTML（見出し・段落・リスト程度）で構いません。"
        )

        try:
            r = subprocess.run(
                ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--output-format", "json"],
                capture_output=True, text=True, timeout=SUMMARY_TIMEOUT, cwd=str(CWD),
            )
            try:
                data = json.loads(r.stdout)
            except json.JSONDecodeError:
                data = None

            if summary_path.exists():
                self._send_json(200, {"file": str(summary_path)})
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
