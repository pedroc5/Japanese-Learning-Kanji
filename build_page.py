#!/usr/bin/env python3
"""Build the daily kanji practice HTML page from a content JSON file.

The model writes the content (kanji, readings, words, examples, quiz) as JSON;
this script does the mechanical part: generating the stroke-order GIFs with
kanji_gif.py, embedding them as base64, and assembling the page.

    python build_page.py content.json --out ~/Documents/Claude-JP/漢字/漢字練習_2026-08-02.html

See SKILL.md for the JSON schema.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MAKER = HERE / "kanji_gif.py"
DEFAULT_SVG_CACHE = HERE / ".kanjivg_cache"
KVG_RAW = "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/{cp}.svg"
DEFAULT_HISTORY = Path.home() / "Documents" / "Claude-JP" / "漢字" / "kanji_history.json"


# --------------------------------------------------------------------------- #
# 履歴（過去に使った字・テーマを二度と繰り返さないための記録）
# --------------------------------------------------------------------------- #

def load_history(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"kanji": {}, "days": []}


def save_history(path: Path, hist: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def update_history(hist: dict, content: dict, kind: str, d: str, content_file: str) -> list[str]:
    """Record this day's kanji/theme in the history. Returns chars that were
    already present on a DIFFERENT date (a real repeat — the selection step
    should have caught this via load_history(), so it's surfaced as a
    warning, not silently ignored). Re-processing the same date (e.g. a
    same-day extra class gets merged into the existing page and the whole
    merged content is re-saved) is not a repeat — matched by date, not
    silently skipped, or every kanji from the earlier class in the same day
    would falsely show up as a dupe. Review days aggregate kanji that are
    already known, so they don't touch the kanji dict at all."""
    dupes: list[str] = []
    chars = [k["char"] for k in content["kanji"]]
    if kind != "review":
        for k in content["kanji"]:
            char = k["char"]
            existing = hist["kanji"].get(char)
            if existing and existing["date"] != d:
                dupes.append(char)
            else:
                hist["kanji"][char] = {
                    "date": d, "level": k.get("level", "?"), "theme": content.get("theme", ""),
                }
    day = next((day for day in hist["days"]
                if day.get("date") == d and day.get("content_file") == content_file), None)
    if day:
        day["kind"], day["theme"], day["chars"] = kind, content.get("theme", ""), chars
    else:
        hist["days"].append({
            "date": d, "kind": kind, "theme": content.get("theme", ""),
            "chars": chars, "content_file": content_file,
        })
    return dupes

CSS = """
  :root{
    --navy:#1c3f66; --accent:#b3541e; --green:#1c6640;
    --light:#eef2f7; --peach:#fdf1e7; --grey:#666; --border:#c3cdd9;
  }
  *{box-sizing:border-box}
  body{
    margin:0; padding:32px 20px 64px;
    font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP","Yu Gothic",sans-serif;
    font-size:16px; line-height:1.75; color:#1a1a1a; background:#f7f8fa;
  }
  rt{font-size:11px; color:var(--grey); user-select:none; display:none}
  body.furigana-on rt{display:ruby-text}
  .furigana-toggle-box{position:fixed; top:20px; left:20px; z-index:50}
  .furigana-toggle{font-family:inherit; font-size:13px; font-weight:700; color:var(--navy);
                    background:#fff; border:1px solid var(--navy); border-radius:20px;
                    padding:8px 18px; cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,.15)}
  .furigana-toggle:hover{background:var(--light)}
  body.furigana-on .furigana-toggle{background:var(--navy); color:#fff}
  .page{display:flex; align-items:flex-start; gap:24px; max-width:1120px; margin:0 auto}
  .wrap{flex:1 1 auto; min-width:0; max-width:820px; background:#fff; padding:40px 44px 48px;
        border-radius:10px; box-shadow:0 1px 4px rgba(0,0,0,.08)}
  .chatbox{flex:0 0 260px; width:260px; position:sticky; top:20px; align-self:flex-start;
           background:#fff; padding:16px 18px; border-radius:10px;
           box-shadow:0 1px 4px rgba(0,0,0,.08); max-height:calc(100vh - 40px);
           display:flex; flex-direction:column}
  .chatbox-title{font-size:17px; font-weight:700; color:var(--navy); margin-bottom:4px}
  .chatlog{flex:1 1 auto; min-height:80px; overflow-y:auto; margin:8px 0; padding-right:2px}
  .chatmsg{font-size:13px; line-height:1.6; margin-bottom:10px; white-space:pre-wrap}
  .chatmsg .who{display:block; font-size:11px; font-weight:700; margin-bottom:2px}
  .chatmsg.you .who{color:var(--navy)}
  .chatmsg.bot .who{color:var(--green)}
  .chatmsg.bot{background:var(--light); border-radius:6px; padding:8px 10px}
  .chatinput{flex:0 0 auto}
  .chatctl{display:flex; align-items:center; gap:10px; flex-wrap:wrap}
  .chatsend{font-family:inherit; font-size:13px; font-weight:700; color:#fff;
            background:var(--navy); border:0; border-radius:4px; padding:7px 16px; cursor:pointer}
  .chatsend:hover{background:#183453}
  .chatsend:disabled{opacity:.6; cursor:default}
  .chat-msg{font-size:12px; color:var(--grey)}
  h1{font-size:28px; color:var(--navy); margin:0 0 4px}
  .sub{color:var(--grey); font-size:14px; margin:0 0 18px; padding-bottom:14px;
       border-bottom:2px solid var(--navy)}
  h2{background:var(--navy); color:#fff; font-size:19px; margin:34px 0 14px;
     padding:9px 14px; border-radius:4px}
  h3{color:var(--accent); font-size:16px; margin:22px 0 8px}
  p{margin:0 0 8px}
  .en{color:var(--grey); font-size:14px}
  .ex{margin:0 0 4px 18px}
  .box{background:var(--light); border-left:4px solid #7c93ad; padding:14px 18px;
       margin:10px 0 16px; border-radius:0 4px 4px 0}
  .box.warm{background:var(--peach); border-left-color:var(--accent)}
  .box p:last-child{margin-bottom:0}
  .ng{color:var(--accent); font-weight:700}
  .ok{color:var(--green); font-weight:700}
  .kanji{font-size:40px; font-weight:700; color:var(--navy); line-height:1.2}
  .lvl{display:inline-block; background:var(--navy); color:#fff; font-size:12px;
       padding:2px 8px; border-radius:10px; vertical-align:middle; margin-left:8px}
  .lvl.n5{background:#2d7d8c}
  .lvl.n4{background:#3f8f5c}
  .lvl.n3{background:var(--green)}
  .lvl.n1{background:#7a1f3d}
  h2 .kanji{color:#fff}
  h2 .lvl{background:#fff; color:var(--navy)}
  h2 .lvl.n5{background:#bfe3ea; color:#0b3a44}
  h2 .lvl.n4{background:#c7e6cf; color:#1e4023}
  h2 .lvl.n3{background:#a8e6c4; color:#0d3d26}
  h2 .lvl.n1{background:#f0c9d6; color:#4a0f24}
  .ans{font-family:inherit; font-size:16px; padding:6px 10px; width:220px;
       border:1px solid var(--border); border-radius:4px; background:#fff}
  .ans:focus{outline:2px solid var(--navy); outline-offset:1px}
  .res{margin-left:10px; font-size:15px}
  .btn{font-family:inherit; font-size:16px; font-weight:700; color:#fff;
       background:var(--navy); border:0; border-radius:5px; padding:10px 22px;
       cursor:pointer; margin:16px 0 4px}
  .btn.sub2{background:var(--grey); margin-left:10px}
  .qline{margin-top:6px}
  .anim{display:flex; align-items:center; gap:14px; margin:10px 0 14px; flex-wrap:wrap}
  .anim .cap{font-size:14px; color:var(--grey)}
  .anim .cap b{color:var(--navy)}
  .player{position:relative; width:170px; height:170px; border:2px solid var(--navy);
          border-radius:6px; background:#fff; overflow:hidden}
  .player canvas{display:block; width:170px; height:170px}
  .player img{display:block; width:170px; height:170px}
  .player .wait{font-size:13px; color:var(--grey); text-align:center; padding-top:72px}
  .ctl{display:inline-flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px}
  .ctl input[type=range]{width:130px; vertical-align:middle}
  .ctl label{font-size:13px; color:var(--grey)}
  .replay{font-family:inherit; font-size:13px; color:#fff; background:var(--navy);
          border:0; border-radius:4px; padding:5px 12px; cursor:pointer}
  .spdv{color:var(--navy); font-size:13px; min-width:44px; display:inline-block}
  .grid{display:flex; flex-wrap:wrap; gap:14px; margin-top:12px}
  .cell{position:relative; width:112px}
  .cell .label{font-size:13px; color:var(--grey); text-align:center; margin-bottom:2px}
  .pad{position:relative; width:112px; height:112px; border:2px solid var(--navy);
       border-radius:4px; background:#fff;
       background-image:
         linear-gradient(to right, transparent 49.6%, var(--border) 49.6%, var(--border) 50.4%, transparent 50.4%),
         linear-gradient(to bottom, transparent 49.6%, var(--border) 49.6%, var(--border) 50.4%, transparent 50.4%);
  }
  .pad .model{position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
       font-size:84px; color:#d9dfe7; pointer-events:none; user-select:none; line-height:1}
  .pad.hide .model{display:none}
  .pad canvas{position:relative; display:block; width:112px; height:112px;
              touch-action:none; cursor:crosshair}
  ol{padding-left:26px; margin:0}
  ol li{margin-bottom:14px}
  ol li::marker{color:var(--navy); font-weight:700}
  table{border-collapse:collapse; width:100%; margin-top:6px; font-size:15px}
  th{background:var(--navy); color:#fff; text-align:left; padding:10px 12px;
     border:1px solid var(--navy)}
  td{border:1px solid var(--border); padding:10px 12px; vertical-align:top}
  tbody tr:nth-child(even){background:var(--light)}
  .word{white-space:nowrap; color:var(--navy)}
  details{margin-top:10px; background:var(--light); border-radius:6px; padding:4px 16px}
  summary{cursor:pointer; color:var(--navy); font-weight:700; padding:10px 0;
          list-style:none; user-select:none}
  summary::-webkit-details-marker{display:none}
  summary::before{content:"▶ "; font-size:12px}
  details[open] summary::before{content:"▼ "}
  details > *:last-child{padding-bottom:12px}
  .foot{color:var(--grey); font-size:14px; margin-top:34px; padding-top:16px;
        border-top:1px solid var(--border)}
  .chatta{width:100%; font-family:inherit; font-size:13px; line-height:1.5; color:#1a1a1a;
          padding:8px 10px; border:1px solid var(--border); border-radius:4px;
          resize:vertical; margin-bottom:6px; box-sizing:border-box}
  .chatta:focus{outline:2px solid var(--navy); outline-offset:1px}
  .summarize-box{position:fixed; bottom:24px; right:24px; z-index:50;
                 display:flex; flex-direction:column; align-items:flex-end; gap:8px}
  .summarize-btn{font-family:inherit; font-size:14px; font-weight:700; color:#fff;
                 background:var(--accent); border:0; border-radius:24px; padding:12px 22px;
                 cursor:pointer; box-shadow:0 2px 8px rgba(0,0,0,.2)}
  .summarize-btn:hover{background:#963f13}
  .summarize-btn:disabled{opacity:.6; cursor:default}
  .summarize-msg{font-size:13px; color:var(--navy); background:#fff; padding:8px 12px;
                 border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.15); max-width:260px;
                 text-align:right}
  .summarize-msg a{color:var(--navy)}
  @media (max-width:900px){
    .page{flex-direction:column}
    .chatbox{position:static; width:100%; max-height:none; flex:none}
    .chatlog{min-height:160px; max-height:320px}
  }
  @media (max-width:600px){
    body{padding:16px 10px 40px}
    .wrap{padding:24px 18px 32px}
    h1{font-size:23px}
    .summarize-box{bottom:14px; right:14px}
  }
  @media print{
    body{background:#fff; padding:0}
    .page{display:block}
    .chatbox{display:none}
    .summarize-box{display:none}
    .wrap{box-shadow:none; padding:0; max-width:none}
    details{display:block}
    details > *{display:block !important}
  }
"""


# --------------------------------------------------------------------------- #
# GIF
# --------------------------------------------------------------------------- #

def make_gif(char: str, gifdir: Path, maker: Path, size: int, conda_env: str) -> str | None:
    """Generate (or reuse) the stroke-order GIF and return it base64-encoded.

    kanji_gif.py needs svgpathtools/Pillow/requests, which only live in the
    `conda_env` conda environment. We always shell out via `conda run -n
    <conda_env>` instead of reusing sys.executable, because build_page.py
    itself may get invoked with a different interpreter (e.g. someone runs
    `python build_page.py` directly) that lacks those packages — that
    mismatch used to fail every single kanji silently (caught exception,
    printed only to stderr, page still written with placeholders).
    """
    cp = "%05x" % ord(char)
    out = gifdir / f"{cp}.gif"
    if not out.exists():
        gifdir.mkdir(parents=True, exist_ok=True)
        cmd = ["conda", "run", "-n", conda_env, "python", str(maker), KVG_RAW.format(cp=cp),
               "--outdir", str(gifdir), "--size", str(size), "--grid",
               "--cache-dir", str(DEFAULT_SVG_CACHE)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or b"").decode(errors="replace").strip().splitlines()
            tail = detail[-1] if detail else "(詳細なし)"
            print(f"  ! {char} ({cp}): GIF生成に失敗 — {tail}", file=sys.stderr)
            return None
        except Exception as e:                       # noqa: BLE001
            print(f"  ! {char} ({cp}): GIF生成に失敗 — {e}", file=sys.stderr)
            return None
    if not out.exists():
        return None
    return base64.b64encode(out.read_bytes()).decode()


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def attr_esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def chatbox_html() -> str:
    """A general-purpose chat sidebar: no kanji/page context is injected —
    Pedro can ask about anything, the same as talking to Claude Code
    directly. The local ask_server keeps each browser tab's messages as one
    ongoing conversation (via `claude -p --resume`), so this renders a
    running transcript rather than a single one-off answer."""
    return (
        '<aside class="chatbox">'
        '<div class="chatbox-title">Claudeに質問する</div>'
        '<p class="en">今日の練習に限らず、何でも自由に質問できます。</p>'
        '<div class="chatlog" id="chatlog"></div>'
        '<div class="chatinput">'
        '<textarea class="chatta" id="chatta" rows="2" '
        'placeholder="質問を入力してください（Shift+Enterで送信）"></textarea>'
        '<div class="chatctl"><button class="chatsend" id="chatsend" type="button">送信</button>'
        '<span class="chat-msg" id="chatmsg"></span></div>'
        '</div></aside>'
    )


def summarize_button_html() -> str:
    """Fixed bottom-right button that asks the local server to write a
    separate まとめ_<date>.html recapping today's kanji, quiz answers (read
    live from the quiz inputs), and the chat conversation so far."""
    return (
        '<div class="summarize-box">'
        '<span class="summarize-msg" id="summarizeMsg"></span>'
        '<button class="summarize-btn" id="summarizeBtn" type="button">まとめて</button>'
        '</div>'
    )


def furigana_toggle_html() -> str:
    """Fixed top-left toggle. Every kanji in examples/quiz questions is
    authored with <ruby>...<rt>reading</rt></ruby>; the <rt> is hidden by
    CSS by default and this button flips a body class to reveal it."""
    return (
        '<div class="furigana-toggle-box">'
        '<button class="furigana-toggle" id="furiganaToggle" type="button">ふりがな</button>'
        '</div>'
    )


def kanji_section(i: int, k: dict, gif_b64: str | None) -> str:
    level = k.get("level", "N3").upper()
    lvl_cls = f"lvl n{level[1:]}" if level.startswith("N") and level[1:].isdigit() else "lvl"
    out = [
        f'<h2>{i}. <span class="kanji">{esc(k["char"])}</span>'
        f'<span class="{lvl_cls}">{esc(level)}</span></h2>',
        f'<p><b>訓読み</b>：{esc(k.get("kun", "—"))}　／　<b>音読み</b>：{esc(k.get("on", "—"))}</p>',
        f'<p class="en">意味：{esc(k.get("meaning", ""))}</p>',
    ]
    if k.get("strokes") or k.get("radical") or k.get("order_note"):
        bits = []
        if k.get("strokes"):
            bits.append(f'{esc(k["strokes"])}画')
        if k.get("radical"):
            bits.append(f'部首：{esc(k["radical"])}')
        if k.get("order_note"):
            bits.append(f'書き順：{esc(k["order_note"])}')
        out.append(f'<p class="en"><b>書き方</b>：{"　".join(bits)}</p>')

    n = k.get("strokes", "")
    body = (f'<img src="data:image/gif;base64,{gif_b64}" alt="「{esc(k["char"])}」の筆順">'
            if gif_b64 else '<div class="wait">GIFを作れませんでした</div>')
    out.append(
        '<div class="anim">'
        f'<div class="player">{body}</div>'
        f'<span class="cap"><b>「{esc(k["char"])}」の筆順'
        f'{f"（{esc(n)}画）" if n else ""}</b><br>'
        '<span class="ctl"><button class="replay" type="button">もう一度見る</button>'
        '<label>速さ <input type="range" class="spd" min="0.25" max="3" step="0.25" value="1"></label>'
        '<b class="spdv">×1.0</b></span></span></div>'
    )

    rows = "\n".join(
        f'    <tr><td class="word">{esc(w["w"])}</td><td>{esc(w["r"])}</td><td>{esc(w.get("m", ""))}</td></tr>'
        for w in k.get("words", [])
    )
    out.append('<table>\n  <thead><tr><th>単語</th><th>読み</th><th>意味</th></tr></thead>\n'
               f'  <tbody>\n{rows}\n  </tbody>\n</table>')
    for ex in k.get("examples", []):
        out.append(f'<p class="ex">・{ex}</p>')      # 例文は <b> を含むので escape しない
    return "\n".join(out)


def practice_section(kanji: list[dict]) -> str:
    items = ", ".join(
        '["%s","%s画"]' % (k["char"], k.get("strokes", "")) for k in kanji
    )
    return f"""
<h2>書き取り練習(マウス・指で書いてみましょう)</h2>
<p class="en">うすいお手本の上をなぞってから、「お手本を隠す」を押して、何も見ないで書いてみてください。
印刷すると、紙のマス目としても使えます。</p>
<p>
  <button class="btn" id="toggleModel">お手本を隠す</button>
  <button class="btn sub2" id="undoStroke">一画戻す</button>
  <button class="btn sub2" id="clearAll">全部消す</button>
</p>
<div class="grid" id="padGrid"></div>
<div class="box">
  <p class="en">書き順の基本ルール：①上から下へ　②左から右へ　③横画→縦画　④外側の囲み→中身→最後にふた　⑤左のへんを先に書く。</p>
</div>
<script>
(function(){{
  var list = [{items}];
  var grid = document.getElementById("padGrid"), pads = [];
  list.forEach(function(item){{
    var cell = document.createElement("div"); cell.className = "cell";
    var lab = document.createElement("div"); lab.className = "label";
    lab.textContent = item[0] + "（" + item[1] + "）";
    var pad = document.createElement("div"); pad.className = "pad";
    var mdl = document.createElement("div"); mdl.className = "model"; mdl.textContent = item[0];
    var cv = document.createElement("canvas");
    var dpr = window.devicePixelRatio || 1;
    cv.width = 112 * dpr; cv.height = 112 * dpr;
    var ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#1a1a1a";
    var drawing = false;
    var history = [];
    var entry = {{pad:pad, ctx:ctx, cv:cv, history:history}};
    function pos(e){{ var r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }}
    cv.addEventListener("pointerdown", function(e){{
      drawing = true; cv.setPointerCapture(e.pointerId);
      activePad = entry;
      history.push(ctx.getImageData(0, 0, cv.width, cv.height));
      if (history.length > 40) history.shift();
      var p = pos(e); ctx.beginPath(); ctx.moveTo(p[0], p[1]);
    }});
    cv.addEventListener("pointermove", function(e){{
      if(!drawing) return; var p = pos(e); ctx.lineTo(p[0], p[1]); ctx.stroke();
    }});
    ["pointerup","pointercancel","pointerleave"].forEach(function(t){{
      cv.addEventListener(t, function(){{ drawing = false; }});
    }});
    pad.appendChild(mdl); pad.appendChild(cv);
    cell.appendChild(lab); cell.appendChild(pad); grid.appendChild(cell);
    pads.push(entry);
  }});
  var hidden = false;
  var activePad = null;   // 直前に線を描いたマス（「一画戻す」が対象にする）
  document.getElementById("toggleModel").addEventListener("click", function(){{
    hidden = !hidden;
    pads.forEach(function(p){{ p.pad.classList.toggle("hide", hidden); }});
    this.textContent = hidden ? "お手本を表示" : "お手本を隠す";
  }});
  document.getElementById("undoStroke").addEventListener("click", function(){{
    if (!activePad || !activePad.history.length) return;
    var img = activePad.history.pop();
    activePad.ctx.putImageData(img, 0, 0);
  }});
  document.getElementById("clearAll").addEventListener("click", function(){{
    pads.forEach(function(p){{ p.ctx.clearRect(0, 0, p.cv.width, p.cv.height); p.history.length = 0; }});
  }});
}})();
</script>
"""


def quiz_section(quiz: list[dict]) -> str:
    qs, answers, data = [], [], []
    for i, q in enumerate(quiz):
        qs.append(f'  <li>{q["q"]}\n'
                  f'    <div class="qline"><input class="ans" data-q="{i}" placeholder="ひらがなで入力">'
                  f'<span class="res" data-r="{i}"></span></div></li>')
        note = q.get("note", "")
        answers.append(f'    <li>{esc(q["a"])}{("（" + esc(note) + "）") if note else ""}</li>')
        alts = json.dumps(q.get("alt", []), ensure_ascii=False)
        data.append('    {ok:["%s"], alt:%s, exp:"%s"}' % (q["a"], alts, esc(note)))
    return """
<h2>復習クイズ(読み方をひらがなで書いてください)</h2>
<p class="en">下の欄に入力して、「答え合わせ」ボタンを押すと、正しいか正しくないかを説明します。</p>
<ol id="quiz">
%s
</ol>

<p>
  <button class="btn" id="check">答え合わせ</button>
  <button class="btn sub2" id="reset">やり直す</button>
  <span class="res" id="score"></span>
</p>

<details>
  <summary>解答を表示</summary>
  <ol>
%s
  </ol>
</details>

<script>
(function(){
  var data = [
%s
  ];
  window.__quizKey = data;   // まとめ機能が採点済みの結果を送るのに使う
  function norm(s){
    return (s||"").trim()
      .replace(/[ァ-ヶ]/g, function(c){return String.fromCharCode(c.charCodeAt(0)-0x60);})
      .replace(/[\\s　・ー]/g,"");
  }
  document.getElementById("check").addEventListener("click", function(){
    var n=0;
    data.forEach(function(d,i){
      var inp=document.querySelector('input[data-q="'+i+'"]');
      var out=document.querySelector('span[data-r="'+i+'"]');
      var v=norm(inp.value);
      if(!v){ out.innerHTML='<span style="color:#666">まだ答えていません。</span>'; return; }
      if(d.ok.indexOf(v)>=0){ n++; out.innerHTML='<span class="ok">〇 正しいです。</span> '+d.exp; }
      else if(d.alt.indexOf(v)>=0){ out.innerHTML='<span class="ng">△ おしい！</span> 正しい答えは <b>'+d.ok[0]+'</b> です。'+d.exp; }
      else { out.innerHTML='<span class="ng">✕ 正しくありません。</span> 正しい答えは <b>'+d.ok[0]+'</b> です。'+d.exp; }
    });
    document.getElementById("score").innerHTML='　<b>'+n+' ／ '+data.length+' 問正解</b>';
  });
  document.getElementById("reset").addEventListener("click", function(){
    document.querySelectorAll("input.ans").forEach(function(i){i.value="";});
    document.querySelectorAll("span.res").forEach(function(s){s.innerHTML="";});
  });
})();
</script>
""" % ("\n".join(qs), "\n".join(answers), ",\n".join(data))


PLAYER_JS = """
<script>
/* 埋め込みGIFを解析して canvas で再生する（速さ調整・もう一度・最後で停止） */
%s
(function(){
  document.querySelectorAll(".player img").forEach(function(img){
    fetch(img.src).then(function(r){ return r.arrayBuffer(); }).then(function(buf){
      var g = composeFrames(decodeGIF(new Uint8Array(buf)));
      if (!g.frames.length) return;
      var box = img.parentNode, cv = document.createElement("canvas");
      cv.width = g.width; cv.height = g.height;
      box.innerHTML = ""; box.appendChild(cv);
      var ctx = cv.getContext("2d");
      var frames = g.frames.map(function(f){
        var im = ctx.createImageData(g.width, g.height);
        im.data.set(f.data); im.delay = f.delay; return im;
      });
      var speed = 1, timer = null, idx = 0;
      function draw(){
        ctx.putImageData(frames[idx], 0, 0);
        if (idx >= frames.length - 1) return;      // 最後で停止
        timer = setTimeout(function(){ idx++; draw(); },
                           Math.max(20, frames[idx].delay / speed));
      }
      function start(){ clearTimeout(timer); idx = 0; draw(); }
      start();
      var cap = box.parentNode;
      var btn = cap.querySelector(".replay"), sld = cap.querySelector(".spd"),
          lab = cap.querySelector(".spdv");
      if (btn) btn.onclick = start;
      if (sld) sld.oninput = function(){
        speed = parseFloat(this.value);
        if (lab) lab.textContent = "×" + speed.toFixed(1);
      };
    }).catch(function(){});
  });
})();
</script>
"""

CHAT_JS = """
<script>
/* サイドバーのチャット：ローカルの会話サーバー（127.0.0.1:8765）に毎回fetchし、Claudeの
   答えをその場のログに追加していく。conversation_idはページを開くたびに新しく作り、
   サーバー側でclaude -p --resumeに使うことで、同じページを開いている間は会話が続く。
   サーバーに繋がらない場合は、質問文をクリップボードにコピーするフォールバックに切り替える。 */
(function(){
  var ASK_URL = "http://127.0.0.1:8765/ask";
  var convId = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2);

  var log = document.getElementById("chatlog");
  var ta = document.getElementById("chatta");
  var btn = document.getElementById("chatsend");
  var msg = document.getElementById("chatmsg");
  if (!log || !ta || !btn) return;

  function addMsg(who, text){
    var div = document.createElement("div");
    div.className = "chatmsg " + who;
    var label = document.createElement("span");
    label.className = "who";
    label.textContent = who === "you" ? "あなた" : "Claude";
    div.appendChild(label);
    div.appendChild(document.createTextNode(text));
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function copyText(text){
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function(resolve, reject){
      var t = document.createElement("textarea");
      t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.focus(); t.select();
      var okFlag = false;
      try { okFlag = document.execCommand("copy"); } catch (e) { okFlag = false; }
      document.body.removeChild(t);
      if (okFlag) resolve(); else reject(new Error("copy failed"));
    });
  }

  function send(){
    if (btn.disabled) return;
    var q = ta.value.trim();
    if (!q) { if (msg) msg.textContent = "質問を書いてから押してください。"; return; }

    addMsg("you", q);
    ta.value = "";
    ta.focus();
    btn.disabled = true;
    if (msg) msg.textContent = "考え中…";
    var thinking = addMsg("bot", "…");

    fetch(ASK_URL, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: q, conversation_id: convId})
    }).then(function(r){
      if (!r.ok) throw new Error("server status " + r.status);
      return r.json();
    }).then(function(data){
      btn.disabled = false;
      if (msg) msg.textContent = "";
      thinking.lastChild.textContent = data.error || data.answer || "(応答なし)";
    }).catch(function(){
      btn.disabled = false;
      thinking.remove();
      copyText(q).then(function(){
        if (msg) msg.textContent = "質問サーバーに繋がりませんでした。質問をコピーしたので、"
          + "ターミナルのClaude Codeに貼り付けてください。";
      }).catch(function(){
        if (msg) msg.textContent = "質問サーバーに繋がらず、コピーもできませんでした。";
      });
    });
  }

  btn.addEventListener("click", send);
  ta.addEventListener("keydown", function(e){
    if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); send(); }
  });
})();
</script>
"""

FURIGANA_JS = """
<script>
/* 「ふりがな」トグル：文中の<ruby><rt>を表示/非表示にするだけ（CSSクラスの切り替え）。 */
(function(){
  var btn = document.getElementById("furiganaToggle");
  if (!btn) return;
  btn.addEventListener("click", function(){
    document.body.classList.toggle("furigana-on");
  });
})();
</script>
"""

SUMMARIZE_JS = """
<script>
/* 「まとめて」ボタン：今日のcontent JSONのパス・クイズの採点結果（ページ自身の答え合わせ
   ロジックを再利用して、ここで採点する）・チャットの会話をローカルサーバーに送り、
   まとめ_<date>.html を別ファイルとして作らせる。 */
(function(){
  var SUMMARIZE_URL = "http://127.0.0.1:8765/summarize";
  var btn = document.getElementById("summarizeBtn");
  var msg = document.getElementById("summarizeMsg");
  if (!btn) return;

  function normReading(s){
    return (s||"").trim()
      .replace(/[ァ-ヶ]/g, function(c){return String.fromCharCode(c.charCodeAt(0)-0x60);})
      .replace(/[\\s　・ー]/g,"");
  }

  function questionText(li){
    var clone = li.cloneNode(true);
    var qline = clone.querySelector(".qline");
    if (qline) qline.remove();
    return clone.textContent.trim();
  }

  function gradeQuiz(){
    var key = window.__quizKey || [];
    var lis = document.querySelectorAll("#quiz li");
    return Array.prototype.map.call(
      document.querySelectorAll("input.ans"), function(inp, i){
        var given = inp.value.trim();
        var k = key[i] || {ok: [], alt: []};
        var gn = normReading(given);
        var verdict = !gn ? "未回答"
          : (k.ok.indexOf(gn) >= 0 ? "正解" : (k.alt.indexOf(gn) >= 0 ? "惜しい" : "不正解"));
        return {q: lis[i] ? questionText(lis[i]) : "", given: given, verdict: verdict};
      }
    );
  }

  btn.addEventListener("click", function(){
    if (btn.disabled) return;
    var contentFile = document.body.getAttribute("data-content-file") || "";
    var quizResults = gradeQuiz();
    var chat = Array.prototype.map.call(
      document.querySelectorAll("#chatlog .chatmsg"), function(el){
        var who = el.classList.contains("you") ? "you" : "bot";
        var whoLabel = el.querySelector(".who");
        var text = el.textContent;
        if (whoLabel) text = text.slice(whoLabel.textContent.length);
        return {who: who, text: text.trim()};
      }
    ).filter(function(m){ return m.text && m.text !== "…"; });

    btn.disabled = true;
    if (msg) msg.textContent = "まとめを作成中…（数十秒かかります）";

    fetch(SUMMARIZE_URL, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({content_file: contentFile, quiz_results: quizResults, chat: chat})
    }).then(function(r){ return r.json().then(function(d){ return {ok: r.ok, data: d}; }); })
      .then(function(res){
        btn.disabled = false;
        if (!res.ok || res.data.error) {
          if (msg) msg.textContent = res.data.error || "まとめの作成に失敗しました。";
          return;
        }
        if (msg) {
          msg.innerHTML = "";
          var a = document.createElement("a");
          a.href = "file://" + res.data.file;
          a.textContent = "まとめを作成しました（開く）";
          a.target = "_blank";
          msg.appendChild(a);
        }
      }).catch(function(){
        btn.disabled = false;
        if (msg) msg.textContent = "質問サーバーに繋がりませんでした。";
      });
  });
})();
</script>
"""


def build(content: dict, gifs: dict[str, str | None], out_path: str, content_file: str = "") -> str:
    d = content.get("date") or date.today().isoformat()
    theme = content.get("theme", "")
    chars = "・".join(k["char"] for k in content["kanji"])
    counts: dict[str, int] = {}
    for k in content["kanji"]:
        lvl = k.get("level", "N3").upper()
        counts[lvl] = counts.get(lvl, 0) + 1
    level_order = ["N5", "N4", "N3", "N2", "N1"]
    ordered = [lvl for lvl in level_order if lvl in counts] + [lvl for lvl in counts if lvl not in level_order]
    level_str = "／".join(f"{lvl} {counts[lvl]}字" for lvl in ordered)

    chatbox = chatbox_html()
    summarize = summarize_button_html()
    furigana_toggle = furigana_toggle_html()
    parts = [
        "<!DOCTYPE html>", '<html lang="ja">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>漢字練習 {d}</title>", "<style>", CSS, "</style>", "</head>",
        f'<body data-content-file="{attr_esc(content_file)}">',
        furigana_toggle,
        '<div class="page">', '<div class="wrap">', "", "<h1>漢字練習</h1>",
        f'<p class="sub">{d}　テーマ：<b>{esc(theme)}</b>　({level_str})</p>', "",
        '<div class="box warm">',
        f"  <p>今日のテーマ：{esc(theme)}　／　今日の{len(content['kanji'])}字：{chars}</p>",
        '  <p class="en">まず読み方と単語を確認してから、最後の復習クイズに挑戦してください。</p>',
        '  <p class="en">※筆順アニメーションは KanjiVG（CC BY-SA 3.0）のデータから作成しています。'
        '赤ではなく画ごとに色が変わり、数字が何画目かを示します。</p>',
        '  <p class="en">※左上の「ふりがな」ボタンで、例文とクイズの読みを表示/非表示にできます。</p>',
        "</div>", "",
    ]
    for i, k in enumerate(content["kanji"], 1):
        parts.append(kanji_section(i, k, gifs.get(k["char"])))
        parts.append("")
    parts.append(practice_section(content["kanji"]))
    parts.append(quiz_section(content["quiz"]))
    parts.append(f'<p class="foot">JLPT漢字練習 ／ {d} ／ '
                 f'筆順データ：KanjiVG (CC BY-SA 3.0)</p>')
    parts.append(PLAYER_JS % (HERE / "gifdec.js").read_text(encoding="utf-8"))
    parts += ["</div>", chatbox, "</div>", summarize]
    parts.append(CHAT_JS)
    parts.append(SUMMARIZE_JS)
    parts.append(FURIGANA_JS)
    parts += ["</body>", "</html>"]
    return "\n".join(parts)


def main() -> int:
    p = argparse.ArgumentParser(description="漢字練習HTMLを組み立てる")
    p.add_argument("content", type=Path, help="内容を書いたJSONファイル")
    p.add_argument("--out", type=Path, help="出力HTML（既定：~/Documents/Claude-JP/漢字/漢字練習_<date>.html）")
    p.add_argument("--gifdir", type=Path, default=Path.home() / "Documents" / "Claude-JP" / "漢字" / "gif")
    p.add_argument("--maker", type=Path, default=DEFAULT_MAKER, help="kanji_gif.py のパス")
    p.add_argument("--size", type=int, default=240, help="GIFの大きさ（px）")
    p.add_argument("--skip-gif", action="store_true", help="GIFを作らない（テスト用）")
    p.add_argument("--conda-env", default="kanji", help="kanji_gif.py を実行するconda環境名")
    p.add_argument("--kind", choices=["daily", "extra", "review"], default="daily",
                   help="daily=通常の1回、extra=同じ日の追加クラス（既存ファイルに合流する）、review=金曜の週次復習")
    p.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="履歴JSONのパス")
    p.add_argument("--content-out", type=Path, help="コンテンツJSONの保存先（既定：out横のcontent_<date>.json）")
    p.add_argument("--append", action="store_true",
                   help="同じ日付のcontent_<date>.jsonが既にあれば、上書きせずそこに合流させる"
                        "（同じ日の追加クラス用。ファイルがなければ通常通り新規作成）")
    a = p.parse_args()

    content = json.loads(a.content.read_text(encoding="utf-8"))
    d = content.get("date") or date.today().isoformat()
    out = a.out or (Path.home() / "Documents" / "Claude-JP" / "漢字" / f"漢字練習_{d}.html")
    stem = out.stem.replace("漢字練習", "content", 1) if "漢字練習" in out.stem else f"content_{out.stem}"
    content_out = a.content_out or (out.parent / f"{stem}.json")

    if a.append and content_out.exists():
        prev = json.loads(content_out.read_text(encoding="utf-8"))
        seen = {k["char"] for k in prev.get("kanji", [])}
        for k in content["kanji"]:
            if k["char"] not in seen:
                prev.setdefault("kanji", []).append(k)
                seen.add(k["char"])
        prev.setdefault("quiz", []).extend(content.get("quiz", []))
        new_theme, prev_theme = content.get("theme", ""), prev.get("theme", "")
        if new_theme and new_theme not in prev_theme.split("＋"):
            prev["theme"] = f"{prev_theme}＋{new_theme}" if prev_theme else new_theme
        content = prev
        print(f"  合流先: {content_out}（既存 {len(seen)} 字に合流）")

    gifs: dict[str, str | None] = {}
    failed: list[str] = []
    if not a.skip_gif:
        for k in content["kanji"]:
            print(f"  … {k['char']} のGIFを作成中")
            gifs[k["char"]] = make_gif(k["char"], a.gifdir, a.maker, a.size, a.conda_env)
            if not gifs[k["char"]]:
                failed.append(k["char"])

    out.parent.mkdir(parents=True, exist_ok=True)
    content_out.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    out.write_text(build(content, gifs, str(out), str(content_out)), encoding="utf-8")

    hist = load_history(a.history)
    dupes = update_history(hist, content, a.kind, d, str(content_out))
    save_history(a.history, hist)

    ok = sum(1 for v in gifs.values() if v)
    print(f"完了：{out}（GIF {ok}/{len(content['kanji'])}）")
    if dupes:
        print(f"警告: 履歴上すでに使用済みの字が含まれています — {'・'.join(dupes)}", file=sys.stderr)
        print("   選定時にkanji_history.jsonを確認し損ねた可能性があります。", file=sys.stderr)
    if failed:
        print(f"警告: GIFを作れなかった字があります — {'・'.join(failed)}", file=sys.stderr)
        print("   conda環境やネットワークを確認し、必要なら再実行してください。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
