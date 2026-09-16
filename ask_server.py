#!/usr/bin/env python3
"""Local HTTP server the kanji-practice pages call to chat with Claude live
and show the answer inline, instead of copy-pasting into a terminal.

Runs as a background launchd service (com.kanji-practice.ask-server.plist,
started via run_ask_server.sh) so it's already up whenever a practice page
is opened. Each browser-side conversation (a random id generated when the
page loads) is mapped to a Claude Code session id, so follow-up questions
use `claude -p --resume <session-id>` and keep the thread of conversation —
no separate API key, just the existing Claude Code login on this machine.

It also keeps the pages' own state — quiz answers, the 書き取り strokes, the
chat thread id — on disk under .page_state/, so that work survives things the
browser does not guarantee: clearing browsing data, a different browser or
profile, a private window. The page writes to localStorage first (instant, and
it still works with this server down) and mirrors here for durability.

    python ask_server.py [--port 8765]   # 既定は config.json の ask_server_port
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_page  # noqa: E402  (reuse the same CSS as 漢字練習_*.html for a consistent look)
import config  # noqa: E402

CWD = Path(__file__).resolve().parent  # スキルフォルダ。.claude/settings.json の権限がここ基準で読み込まれる
TIMEOUT = 180
SUMMARY_TIMEOUT = 240

# ページごとの保存状態（クイズの答え・書き取りの線・会話id）。
#
# なぜサーバー側にも持つのか：ページは file:// で開かれるので、localStorage の中身は
# ブラウザの「サイトデータ」そのもの。「閲覧履歴を消去」でCookieとサイトデータを選べば、
# その日の答えも書き取りも全部まとめて消える（2026-09に実際に起きた。8-31までの記録が
# 残っていて、9月分が1日も無かった）。別のブラウザやプロファイル、シークレットウィンドウ
# でも同じことになる。ここに置けば消えない。
#
# スキルフォルダに置く理由は .content/ と同じ：launchd下のこのプロセスが読み書きする
# 必要があり、TCC保護下のフォルダでは既存ファイルを読み返せない（build_page.DEFAULT_HISTORY）。
PAGE_STATE_DIR = CWD / ".page_state"

# 書いてよいキー。ページ側が増やしたいときはここに足す。未知のキーは黙って捨てる
# （file:// からは誰でも叩けるポートなので、ディスクに書く内容は絞っておく）。
STATE_KEYS = ("quiz", "pads", "conv")

# 1ページ分の上限。書き取りの線は点列なので一番大きいが、10字×5マス×全画でも
# 数百KBに収まる。壊れた／悪意のある書き込みでディスクを埋めないための蓋。
MAX_STATE_BYTES = 4 * 1024 * 1024

_state_lock = threading.Lock()


def state_file(page_id: str) -> Path:
    """page_id をそのままファイル名にはしない（"daily:2026-09-16" の : や、
    もっと悪いものが来る）。英数字・_・- 以外は全部 _ に潰す。

    ドットも残さない。残すと "../.." が ".._.." のまま通り、フォルダから出られは
    しないものの、"." や ".." のような名前を作れてしまう。ここで許す文字を最小に
    しておくほうが、後から数え直すより安い。
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", page_id)[:120] or "unknown"
    return PAGE_STATE_DIR / f"{safe}.json"


def load_state(page_id: str) -> dict:
    """そのページの保存状態。無ければ空。読めなくても落とさない。"""
    try:
        return json.loads(state_file(page_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(page_id: str, incoming: dict) -> dict:
    """届いたキーだけを、より新しいほうを残して書き込む。

    値は {"v": 中身, "t": 書いた時刻(ms)} の形で来る。サーバーが持っているものより
    t が古い書き込みは捨てる — サーバーを止めている間にブラウザ側が進んでいることも、
    その逆もあるので、どちらが新しいかは時刻で決める。返すのは書き込み後の全体。
    """
    with _state_lock:
        current = load_state(page_id)
        for key, entry in incoming.items():
            if key not in STATE_KEYS or not isinstance(entry, dict) or "v" not in entry:
                continue
            when = entry.get("t")
            when = when if isinstance(when, (int, float)) else 0
            have = current.get(key)
            if isinstance(have, dict) and isinstance(have.get("t"), (int, float)) and have["t"] > when:
                continue
            current[key] = {"v": entry["v"], "t": when}
        blob = json.dumps(current, ensure_ascii=False)
        if len(blob.encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("保存する状態が大きすぎます。")
        PAGE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        # 一旦別名に書いてから差し替える。書いている最中に読まれても、
        # 半分だけのJSONを掴むことがない。
        tmp = state_file(page_id).with_suffix(".json.tmp")
        tmp.write_text(blob, encoding="utf-8")
        tmp.replace(state_file(page_id))
        return current


# conversation_id (browser-side) -> claude session_id. Grows for the life of the
# process and is dropped on restart: at a handful of conversations a day that
# costs nothing, and losing it only means the next question starts a fresh
# thread instead of resuming. Not worth an eviction policy.
_sessions: dict[str, str] = {}
_lock = threading.Lock()

# サイドバーのチャット用の指示。質問文には混ぜず --append-system-prompt で渡すので、
# 会話ログには出ず、--resume で続く各ターンにも同じように効く。
ASK_SYSTEM_PROMPT = (
    "Answer as a Japanese language teacher, correcting incorrect or unnatural "
    "formulations. Use examples, definitions, and when possible backup your "
    "answer on existing documents."
)


def run_claude(prompt: str, allowed_tools: str, timeout: int,
               resume_session: str | None = None,
               system_prompt: str | None = None) -> tuple[subprocess.CompletedProcess, dict | None]:
    """Run one headless `claude -p` call. Returns (process, parsed JSON or None).

    `allowed_tools` must be BARE, space-separated tool names ("Read Write").
    Path-scoped rules — `Read(path/**)`, whether written here, in
    .claude/settings.json, or in settings.local.json — are silently never
    evaluated under headless "-p": no workspace-trust dialog can be shown, so
    scoped rules are dropped entirely and every matching tool call is denied.
    Verified empirically; only bare names are honoured.

    `system_prompt` is appended to Claude Code's own system prompt rather than
    replacing it, so the CLI's normal behaviour is left intact.

    Raises the usual subprocess errors (TimeoutExpired, FileNotFoundError) for
    the caller to turn into an HTTP response.
    """
    cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
           "--allowedTools", allowed_tools, "--output-format", "json"]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    if resume_session:
        cmd += ["--resume", resume_session]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(CWD))
    try:
        return proc, json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc, None


def summary_section(number: int, title: str, note: str = "") -> str:
    """One foldable まとめ section, matching the practice page's markup.

    Same `details.sec` pattern build_page.collapsible() emits: open to start
    with, the <h2> itself acting as the click target.
    """
    lines = [f"<!-- ===== {number}. {title} ===== -->"]
    if note:
        lines.append(f"<!-- {note} -->")
    lines += [
        '<details class="sec" open>',
        f"  <summary><h2>{number}. {title}</h2></summary>",
        '  <div class="sec-body">',
        "    (ここに内容)",
        "  </div>",
        "</details>",
    ]
    return build_page.indent("\n".join(lines), 2)


def summary_skeleton(day: str) -> str:
    """The HTML shell the model fills in, so every まとめ page looks the same
    as that day's 漢字練習 page (same CSS, same section order, same indented
    and commented layout)."""
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="ja">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>今日の振り返り {day}</title>",
        "<style>",
        build_page.CSS.strip("\n"),
        "</style>",
        "</head>",
        "",
        "<body>",
        "",
        '<div class="page">',
        '  <div class="wrap">',
        "    <h1>今日の振り返り</h1>",
        f'    <p class="sub">{day}　テーマ：<b>(ここにテーマ)</b></p>',
        "",
        '    <div class="box warm">(ここに今日の一言まとめ)</div>',
        "",
        summary_section(1, "今日学んだ漢字の要点"),
        "",
        summary_section(2, "クイズの結果"),
        "",
        summary_section(3, "チャットから追加で学んだこと",
                        "該当する知識があるときだけ。単語と意味・読みを短く列挙する。"
                        "なければこの節ごと省略"),
        "  </div>",
        "</div>",
        "",
        "</body>",
        "</html>",
    ])


def summary_prompt(content_file: str, summary_path: str, day: str,
                   quiz_results: list[dict], chat: list[dict]) -> str:
    """Build the instruction for the nested `claude -p` that writes まとめ_<date>.html."""
    quiz_lines = [
        f"- 問題「{r.get('q', '')}」／学習者の解答「{r.get('given') or '(空欄)'}」／判定：{r.get('verdict', '?')}"
        for r in quiz_results
    ]
    quiz_block = "\n".join(quiz_lines) or "(クイズの解答はまだ入力されていません)"

    chat_lines = [
        f"{'学習者' if m.get('who') == 'you' else 'Claude'}: {m.get('text', '')}"
        for m in chat if m.get("text")
    ]
    chat_block = "\n".join(chat_lines) or "(チャットでの質問はありませんでした)"

    return (
        "（ファイル操作はReadツールとWriteツールだけを使ってください。"
        "catやlsなどのBashは使わないでください。）\n\n"
        f"{content_file} を読んで、今日の漢字練習（JLPT学習者向け）の振り返りまとめを"
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
        f"```html\n{summary_skeleton(day)}\n```\n\n"
        "**書き出すHTMLは人が読める形にしてください。** 具体的には、骨格のインデント"
        "（入れ子ごとに半角スペース2つ）とセクションのコメントをそのまま保ち、追加する内容にも"
        "同じインデントを付ける。1行にタグを詰め込まず、<tr>や<li>は1行に1つずつ書く。"
        "各セクションは骨格どおり <details class=\"sec\" open> のまま残し（最初は開いた状態で、"
        "見出しをクリックするとたためる）、その中の <div class=\"sec-body\"> に内容を入れてください。"
    )


def summary_target(content_file: str) -> tuple[str, str]:
    """content_<date>.json の名前から (その日の日付, まとめの書き出し先) を決める。

    まとめは学習者が読むページなので、その日の漢字練習ページと同じ週フォルダ
    （<output_root>/<週>/）に置く。**内容JSONの隣ではない**：内容JSONは
    TCCの都合でスキルフォルダへ移してあり（build_page.DEFAULT_CONTENT_DIR）、
    そこを基準にするとまとめまでスキルフォルダに紛れ込む。
    日付として読めない名前のときは今日の日付にする（週フォルダの計算に日付が要るため）。
    """
    name_match = re.match(r"content_(\d{4}-\d{2}-\d{2})\.json$", Path(content_file).name)
    day = name_match.group(1) if name_match else date.today().isoformat()
    return day, str(build_page.day_page_path(day).parent / f"まとめ_{day}.html")


def save_quiz_results(day: str, results: list[dict]) -> None:
    """Leave the day's quiz verdicts where the next build can pick them up.

    kanji_history.json is the natural home and now sits in the skill folder,
    within reach of this process — but the daily build rewrites that file whole,
    so writing it from this always-on server would race that rewrite. The
    results are dropped here instead and build_page.ingest_quiz_results() folds
    them into the history on the next daily run.

    Best-effort: a failure here must not cost the learner their まとめ, so it is
    reported and swallowed.
    """
    attempted = [r for r in results if r.get("char") and r.get("verdict") != "未回答"]
    if not attempted:
        return
    try:
        build_page.QUIZ_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = build_page.QUIZ_RESULTS_DIR / f"{day}.json"
        path.write_text(
            json.dumps({"date": day, "results": attempted}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as e:                           # noqa: BLE001
        print(f"クイズの成績を保存できませんでした: {e}", file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    """/ask and /summarize (POST) drive the sidebar; /state (GET and POST)
    keeps each page's answers and strokes on disk."""

    def _cors(self) -> None:
        """Pages are opened as file:// URLs, so every request is cross-origin."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/state":
            page_id = urllib.parse.parse_qs(parsed.query).get("page_id", [""])[0]
            if not page_id:
                self._send_json(400, {"error": "page_id がありません。"})
                return
            self._send_json(200, {"state": load_state(page_id)})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/ask":
            self._handle_ask()
        elif self.path == "/summarize":
            self._handle_summarize()
        elif self.path == "/state":
            self._handle_state()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_state(self) -> None:
        """POST /state — ページの下書き（答え・線・会話id）を書き留める。

        claudeを呼ばないので速い。ページ側はキー入力のたびではなく、少し溜めてから
        送ってくる（STORE_JS のデバウンス）。
        """
        try:
            body = self._read_json()
            page_id = (body.get("page_id") or "").strip()
            state = body.get("state")
            if not page_id or not isinstance(state, dict):
                self._send_json(400, {"error": "リクエストが不正です。"})
                return
            self._send_json(200, {"state": save_state(page_id, state)})
        except ValueError as e:
            self._send_json(413, {"error": str(e)})
        except Exception as e:                        # noqa: BLE001
            self._send_json(500, {"error": f"エラー: {e}"})

    def _read_json(self) -> dict:
        """Parse the request body as JSON (empty body -> {})."""
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _handle_ask(self) -> None:
        """POST /ask — one turn of the sidebar conversation."""
        try:
            body = self._read_json()
            question = (body.get("question") or "").strip()
            conv_id = (body.get("conversation_id") or "").strip()
        except Exception:
            question, conv_id = "", ""
        if not question:
            self._send_json(400, {"error": "質問が空です。"})
            return

        # Resuming the browser tab's previous session is what makes the sidebar
        # a conversation rather than a series of unrelated one-shot answers.
        with _lock:
            resume_sid = _sessions.get(conv_id) if conv_id else None

        try:
            proc, data = run_claude(question, "Read WebFetch", TIMEOUT, resume_sid,
                                    system_prompt=ASK_SYSTEM_PROMPT)

            if data and data.get("session_id"):
                if conv_id:
                    with _lock:
                        _sessions[conv_id] = data["session_id"]
                answer = (data.get("result") or "").strip() or "(応答がありませんでした)"
                self._send_json(200, {"answer": answer})
            else:
                # Unparseable output still gets shown rather than swallowed.
                fallback = ((proc.stdout or "").strip() or (proc.stderr or "").strip()
                            or "(応答がありませんでした)")
                self._send_json(200, {"answer": fallback})
        except subprocess.TimeoutExpired:
            self._send_json(504, {"error": "応答がタイムアウトしました。もう一度お試しください。"})
        except FileNotFoundError:
            self._send_json(500, {"error": "claude コマンドが見つかりません（PATHを確認してください）。"})
        except Exception as e:                        # noqa: BLE001
            self._send_json(500, {"error": f"エラー: {e}"})

    def _handle_summarize(self) -> None:
        """Build and write まとめ_<date>.html for today's class.

        This process deliberately never touches the output root itself.
        When that root is under ~/Documents (or ~/Desktop, ~/Downloads), a
        launchd-spawned python3 has no Full Disk / Files-and-Folders grant for
        it, so any direct os/pathlib read here fails with EPERM — even though
        the same code works when the server is
        started by hand from Terminal, which inherits Terminal's own grant.
        The `claude` binary is unaffected and already reads/writes there fine,
        so all file access is delegated to the nested `claude -p` call. Quiz
        scoring likewise arrives pre-graded from the browser (which already
        has the answer key for its own "check answers" button), so this file
        never has to parse the quiz key either.

        Read/Write/Edit/Bash are all allow-listed because the model doesn't
        reliably pick the same tool for the same job run to run — it may Write
        a fresh file one time and Edit the existing まとめ_<date>.html the
        next (the prompt says overwriting is fine), or shell out with cat/ls
        instead of Read. The prompt still asks for Read/Write only, as a
        nudge, but one denial fails the whole call, so the wider list stays as
        a safety net. See run_claude() for why the names must be unscoped.
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

        # content_<date>.json という命名規則から日付と書き出し先を決める（ファイルを開かずに済む）
        day, summary_path = summary_target(content_file)

        save_quiz_results(day, quiz_results)

        prompt = summary_prompt(content_file, summary_path, day, quiz_results, chat)

        try:
            proc, data = run_claude(prompt, "Read Write Edit Bash", SUMMARY_TIMEOUT)

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
                msg = msg or (proc.stdout or proc.stderr or "").strip() or "まとめの作成に失敗しました。"
                self._send_json(500, {"error": msg})
        except subprocess.TimeoutExpired:
            self._send_json(504, {"error": "まとめの作成がタイムアウトしました。もう一度お試しください。"})
        except FileNotFoundError:
            self._send_json(500, {"error": "claude コマンドが見つかりません（PATHを確認してください）。"})
        except Exception as e:                         # noqa: BLE001
            self._send_json(500, {"error": f"エラー: {e}"})


def main() -> int:
    parser = argparse.ArgumentParser(description="漢字練習ページ用のローカル会話サーバー")
    parser.add_argument("--port", type=int, default=config.port("ask_server_port"),
                        help="待ち受けるポート（既定：config.jsonのask_server_port）")
    args = parser.parse_args()

    # Threaded so a long まとめ call doesn't block the chat sidebar; bound to
    # loopback only, since the pages are local files on this machine.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"kanji ask_server listening on http://127.0.0.1:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
