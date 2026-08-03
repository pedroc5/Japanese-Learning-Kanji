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
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MAKER = HERE / "kanji_gif.py"
DEFAULT_SVG_CACHE = HERE / ".kanjivg_cache"
KVG_RAW = "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/{cp}.svg"
DEFAULT_HISTORY = Path.home() / "Documents" / "Claude-JP" / "漢字" / "kanji_history.json"

# Quiz results dropped here by ask_server.py when まとめて is pressed, and folded
# into kanji_history.json by the next daily build. They take this detour because
# ask_server.py runs under launchd without a Files-and-Folders grant for
# ~/Documents/Claude-JP and cannot write the history file itself (see the long
# note in ask_server._handle_summarize); the skill folder it can always write.
QUIZ_RESULTS_DIR = HERE / ".quiz_results"


# --------------------------------------------------------------------------- #
# Embedded page assets
#
# Everything below is copied verbatim into the generated HTML. Only PLAYER_JS
# is templated (its %s takes the gifdec.js source). Edit with care: the pages
# are self-contained, so a browser only ever sees this copy.
# --------------------------------------------------------------------------- #


CSS = """
  :root{
    --navy:#1c3f66; --accent:#b3541e; --green:#1c6640;
    --light:#eef2f7; --peach:#fdf1e7; --grey:#666; --border:#c3cdd9;
    --chat-w:260px;   /* チャット欄の幅。ドラッグや「⤢」でJSが書き換える */
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
  /* ページ全体はチャット欄の幅に合わせて広がる（本文の幅は820pxのまま）。 */
  .page{display:flex; align-items:flex-start; gap:24px; margin:0 auto;
        max-width:calc(820px + 24px + var(--chat-w))}
  .wrap{flex:1 1 auto; min-width:0; max-width:820px; background:#fff; padding:40px 44px 48px;
        border-radius:10px; box-shadow:0 1px 4px rgba(0,0,0,.08)}
  .chatbox{flex:0 0 var(--chat-w); width:var(--chat-w); position:sticky; top:20px;
           align-self:flex-start; background:#fff; padding:16px 18px; border-radius:10px;
           box-shadow:0 1px 4px rgba(0,0,0,.08); max-height:calc(100vh - 40px);
           display:flex; flex-direction:column}
  .chatbox-head{display:flex; align-items:flex-start; justify-content:space-between; gap:8px}
  .chatbox-title{font-size:17px; font-weight:700; color:var(--navy); margin-bottom:4px}
  /* 左端のつまみ：横にドラッグしてチャット欄の幅を変える */
  .chat-resize{position:absolute; top:0; left:-12px; width:14px; height:100%;
               cursor:col-resize; z-index:2}
  .chat-resize::before{content:""; position:absolute; top:50%; left:5px; margin-top:-22px;
                       width:4px; height:44px; border-radius:2px; background:var(--border)}
  .chat-resize:hover::before{background:var(--navy)}
  body.chat-resizing{cursor:col-resize; user-select:none}
  .chat-expand{font-family:inherit; font-size:14px; line-height:1; color:var(--navy);
               background:#fff; border:1px solid var(--border); border-radius:4px;
               padding:5px 8px; cursor:pointer; flex:0 0 auto}
  .chat-expand:hover{background:var(--light); border-color:var(--navy)}
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
  /* details.sec — 見出しごとのたたみ込み。既定はすべて開いた状態で、
     見出し(h2)そのものがクリック領域になる。上の details の見た目（解答欄など）は
     打ち消して、h2の帯だけが見えるようにする。 */
  details.sec{margin:0; padding:0; background:none; border-radius:0}
  details.sec > summary{padding:0; font-weight:inherit; color:inherit}
  details.sec > summary::before{content:none}
  details.sec > summary h2{position:relative; padding-right:36px}
  details.sec > summary h2::after{content:"▼"; position:absolute; right:14px; top:50%;
                                  transform:translateY(-50%); font-size:12px; opacity:.8}
  details.sec:not([open]) > summary h2::after{content:"▶"}
  details.sec > summary:hover h2{background:#25517f}
  details.sec > *:last-child{padding-bottom:0}
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
  /* 縦並びになる幅では、チャットは本文と同じ幅いっぱいなので、幅の調整はできなくする。 */
  @media (max-width:900px){
    .page{flex-direction:column; max-width:820px}
    .chatbox{position:static; width:100%; max-height:none; flex:none}
    .chatlog{min-height:160px; max-height:320px}
    .chat-resize, .chat-expand{display:none}
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


PLAYER_JS = """
<script>
/* 筆順GIFを解析して canvas で再生する（速さ調整・もう一度・最後で停止）。
   GIF本体は本文ではなくページ末尾の <script id="gif-data"> に字ごとにまとめてあり
   （本文のHTMLを人が読めるようにするため）、ここで字を鍵に取り出して復号する。
   1字ずつ setTimeout に分けるのは、10字を一度に復号してページを固まらせないため。 */
%s
(function(){
  var holder = document.getElementById("gif-data");
  if (!holder) return;
  var data;
  try { data = JSON.parse(holder.textContent); } catch (e) { return; }

  function toBytes(b64){
    var bin = atob(b64), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function mount(box, b64){
    var g = composeFrames(decodeGIF(toBytes(b64)));
    if (!g.frames.length) return;
    var cv = document.createElement("canvas");
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
  }

  document.querySelectorAll(".player[data-gif]").forEach(function(box){
    var b64 = data[box.getAttribute("data-gif")];
    if (!b64) return;
    setTimeout(function(){
      try { mount(box, b64); } catch (e) {}
    }, 0);
  });
})();
</script>
"""


CHAT_RESIZE_JS = """
<script>
/* チャット欄の幅：左端のつまみを横にドラッグするか、「⤢」で広い幅に切り替える。
   幅は --chat-w というCSS変数ひとつで決まり（.chatbox の幅と .page の最大幅の両方が
   これを見ている）、localStorageに覚えるので次に開くページでも同じ幅になる。 */
(function(){
  var MIN = 220, MAX = 760, WIDE = 520, DEFAULT = 260, KEY = "kanjiChatWidth";
  var handle = document.getElementById("chatResize");
  var expand = document.getElementById("chatExpand");
  var width = DEFAULT, narrow = DEFAULT;

  function apply(px){
    width = Math.max(MIN, Math.min(MAX, Math.round(px)));
    document.documentElement.style.setProperty("--chat-w", width + "px");
    if (expand) {
      var wide = width >= WIDE;
      expand.textContent = wide ? "⤡" : "⤢";
      expand.title = wide ? "チャットを元の幅に戻す" : "チャットを広げる";
    }
    return width;
  }
  function remember(){ try { localStorage.setItem(KEY, width); } catch (e) {} }

  var saved = 0;
  try { saved = parseInt(localStorage.getItem(KEY), 10) || 0; } catch (e) { saved = 0; }
  apply(saved || DEFAULT);
  if (width < WIDE) narrow = width;

  if (handle) handle.addEventListener("pointerdown", function(e){
    e.preventDefault();
    var startX = e.clientX, startW = width;
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("chat-resizing");
    function move(ev){ apply(startW + (startX - ev.clientX)); }  // 左へ引くほど広がる
    function up(){
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      document.body.classList.remove("chat-resizing");
      if (width < WIDE) narrow = width;
      remember();
    }
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });

  if (expand) expand.addEventListener("click", function(){
    if (width < WIDE) { narrow = width; apply(WIDE); }
    else { apply(narrow || DEFAULT); }
    remember();
  });
})();
</script>
"""


STORE_JS = """
<script>
/* ページごとの下書き保存。クイズの答えとチャットの会話はDOMの中にしか無く、まとめ機能も
   そこから読むので、保存しないとリロードやタブを閉じた時点でその日の記録がまるごと消える。
   保存先はページごと（data-page-id）に分けるので、日付が違えば混ざらない。
   このブロックは本文より前に置く必要がある — クイズのスクリプトが本文の中で使うため。 */
(function(){
  var id = document.body.getAttribute("data-page-id") || location.pathname;
  var PREFIX = "kanji:" + id + ":";
  window.__kanjiStore = {
    load: function(key, fallback){
      try {
        var raw = localStorage.getItem(PREFIX + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    save: function(key, value){
      try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch (e) {}
    }
  };
})();
</script>
"""


CHAT_JS = """
<script>
/* サイドバーのチャット：ローカルの会話サーバー（127.0.0.1:8765）に毎回fetchし、Claudeの
   答えをその場のログに追加していく。conversation_idはページごとに作って保存し、
   サーバー側でclaude -p --resumeに使うことで、リロードしても会話が続く。
   サーバーに繋がらない場合は、質問文をクリップボードにコピーするフォールバックに切り替える。 */
(function(){
  var ASK_URL = "http://127.0.0.1:8765/ask";
  var store = window.__kanjiStore;
  var convId = (store && store.load("conv", "")) ||
               ("c" + Date.now().toString(36) + Math.random().toString(36).slice(2));
  if (store) store.save("conv", convId);

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

  /* ログを {who, text} の配列にする。保存にも、まとめ機能が会話を送るのにも使う
     （SUMMARIZE_JS からは window.__chatSnapshot として呼ぶ）。 */
  function chatSnapshot(){
    return Array.prototype.map.call(log.querySelectorAll(".chatmsg"), function(el){
      var whoLabel = el.querySelector(".who");
      var text = el.textContent;
      if (whoLabel) text = text.slice(whoLabel.textContent.length);
      return {who: el.classList.contains("you") ? "you" : "bot", text: text.trim()};
    }).filter(function(m){ return m.text && m.text !== "…"; });
  }
  window.__chatSnapshot = chatSnapshot;
  function persistChat(){ if (store) store.save("chat", chatSnapshot()); }

  if (store) store.load("chat", []).forEach(function(m){ addMsg(m.who, m.text); });

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
    persistChat();            // 答えを待っている間に閉じても質問は残る
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
      persistChat();
    }).catch(function(){
      btn.disabled = false;
      thinking.remove();
      persistChat();
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
        // char は「どの字の問題か」。サーバー側が字ごとの成績として記録する。
        return {q: lis[i] ? questionText(lis[i]) : "", given: given,
                verdict: verdict, char: k.char || ""};
      }
    );
  }

  btn.addEventListener("click", function(){
    if (btn.disabled) return;
    var contentFile = document.body.getAttribute("data-content-file") || "";
    var day = document.body.getAttribute("data-day") || "";
    var quizResults = gradeQuiz();
    var chat = window.__chatSnapshot ? window.__chatSnapshot() : [];

    btn.disabled = true;
    if (msg) msg.textContent = "まとめを作成中…（数十秒かかります）";

    fetch(SUMMARIZE_URL, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({content_file: contentFile, day: day,
                            quiz_results: quizResults, chat: chat})
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


# --------------------------------------------------------------------------- #
# 履歴（過去に使った字・テーマを二度と繰り返さないための記録）
# --------------------------------------------------------------------------- #


def load_history(path: Path) -> dict:
    """Read the history file, or return an empty one if it doesn't exist yet.

    Shape: {"kanji": {char: {date, level, theme}}, "days": [day records]}.
    """
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"kanji": {}, "days": []}


def save_history(path: Path, history: dict) -> None:
    """Write the history back out, creating its folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def update_history(
    history: dict, content: dict, kind: str, day: str, content_file: str
) -> list[str]:
    """Record this day's kanji and theme, and report any accidental repeats.

    Returns the characters that were already recorded under a DIFFERENT date.
    Those are real repeats: the selection step is supposed to rule them out by
    reading the history first, so they're surfaced as a warning rather than
    being silently accepted.

    Two cases deliberately do NOT count as repeats:

    - Same date. A same-day extra class merges into the existing page and the
      whole merged content is saved again, so every kanji from the earlier
      class would otherwise show up as a duplicate of itself.
    - `kind == "review"`. Review pages re-present kanji that are already known,
      so they never touch the per-kanji dict at all.
    """
    dupes: list[str] = []
    chars = [kanji["char"] for kanji in content["kanji"]]

    if kind != "review":
        for kanji in content["kanji"]:
            char = kanji["char"]
            existing = history["kanji"].get(char)
            if existing and existing["date"] != day:
                dupes.append(char)
            else:
                history["kanji"][char] = {
                    "date": day, "level": kanji.get("level", "?"),
                    "theme": content.get("theme", ""),
                }

    # One record per (date, content file), so re-running a day updates its
    # existing entry instead of appending a second one.
    record = next((r for r in history["days"]
                   if r.get("date") == day and r.get("content_file") == content_file), None)
    if record:
        record["kind"], record["theme"], record["chars"] = kind, content.get("theme", ""), chars
    else:
        history["days"].append({
            "date": day, "kind": kind, "theme": content.get("theme", ""),
            "chars": chars, "content_file": content_file,
        })
    return dupes


def record_quiz_results(history: dict, results: list[dict], day: str) -> int:
    """Fold one day's quiz verdicts into the per-kanji record. Returns kanji touched.

    Each result is {"char": kanji, "verdict": 正解/惜しい/不正解/未回答}. Only
    attempted questions count: 未回答 says nothing about whether the reading is
    known, and counting it as a miss would push every skipped question to the
    front of the review. 惜しい counts as a miss — a near-hit is still a reading
    Pedro hasn't got yet.

    Kanji with no history entry (a character that never appeared in a daily
    page) are skipped rather than invented, so the file stays a record of what
    was actually taught.
    """
    touched = 0
    for result in results:
        char = result.get("char", "")
        verdict = result.get("verdict", "")
        entry = history.get("kanji", {}).get(char)
        if not char or not entry or verdict not in ("正解", "惜しい", "不正解"):
            continue
        stats = entry.setdefault("quiz", {"asked": 0, "wrong": 0, "last": ""})
        stats["asked"] += 1
        if verdict != "正解":
            stats["wrong"] += 1
        stats["last"] = day
        touched += 1
    return touched


def ingest_quiz_results(history: dict, results_dir: Path = QUIZ_RESULTS_DIR) -> int:
    """Apply every pending quiz-result file to `history`, then delete it.

    Files are consumed rather than kept so a re-run can't double-count a day.
    A file that fails to parse is left in place and reported, so a bug here
    doesn't silently throw away the record.
    """
    if not results_dir.exists():
        return 0
    applied = 0
    for path in sorted(results_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            applied += record_quiz_results(history, payload.get("results", []),
                                           payload.get("date", path.stem))
        except Exception as e:                       # noqa: BLE001
            print(f"  ! {path.name} を読めませんでした（残しておきます） — {e}", file=sys.stderr)
            continue
        path.unlink()
    return applied


def quiz_priority(history: dict, char: str) -> tuple[int, float]:
    """Sort key for review order: most-missed first, then worst hit rate.

    Kanji never quizzed sort after anything with a miss but before anything
    answered correctly every time — they're unproven rather than known.
    """
    stats = history.get("kanji", {}).get(char, {}).get("quiz")
    if not stats or not stats.get("asked"):
        return (0, -0.5)     # 0点の「全問正解」(0, 0.0) より前、取りこぼしより後ろ
    return (-stats["wrong"], -stats["wrong"] / stats["asked"])


# --------------------------------------------------------------------------- #
# Stroke-order GIFs
# --------------------------------------------------------------------------- #

def make_gif(char: str, gifdir: Path, maker: Path, size: int, conda_env: str) -> str | None:
    """Generate (or reuse) one kanji's stroke-order GIF, base64-encoded.

    Returns None if the GIF could not be produced; the caller renders a
    placeholder in its place rather than failing the whole page.

    kanji_gif.py needs svgpathtools/Pillow/requests, which only live in the
    `conda_env` conda environment. We always shell out via `conda run -n
    <conda_env>` instead of reusing sys.executable, because build_page.py
    itself may get invoked with a different interpreter (e.g. someone runs
    `python build_page.py` directly) that lacks those packages — that
    mismatch used to fail every single kanji silently (caught exception,
    printed only to stderr, page still written with placeholders).
    """
    codepoint = "%05x" % ord(char)          # KanjiVG names its files by codepoint
    gif_path = gifdir / f"{codepoint}.gif"

    if not gif_path.exists():
        gifdir.mkdir(parents=True, exist_ok=True)
        cmd = ["conda", "run", "-n", conda_env, "python", str(maker),
               KVG_RAW.format(cp=codepoint),
               "--outdir", str(gifdir), "--size", str(size), "--grid",
               "--cache-dir", str(DEFAULT_SVG_CACHE)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or b"").decode(errors="replace").strip().splitlines()
            tail = detail[-1] if detail else "(詳細なし)"
            print(f"  ! {char} ({codepoint}): GIF生成に失敗 — {tail}", file=sys.stderr)
            return None
        except Exception as e:                       # noqa: BLE001
            print(f"  ! {char} ({codepoint}): GIF生成に失敗 — {e}", file=sys.stderr)
            return None

    if not gif_path.exists():
        return None
    return base64.b64encode(gif_path.read_bytes()).decode()


def make_gifs(kanji: list[dict], gifdir: Path, maker: Path, size: int,
              conda_env: str) -> tuple[dict[str, str | None], list[str]]:
    """Render every kanji's GIF, reporting progress as it goes.

    Returns (char -> base64 GIF or None, list of chars that failed). Shared by
    the daily page and the weekly review page.
    """
    gifs: dict[str, str | None] = {}
    failed: list[str] = []
    for entry in kanji:
        char = entry["char"]
        print(f"  … {char} のGIFを作成中")
        gifs[char] = make_gif(char, gifdir, maker, size, conda_env)
        if not gifs[char]:
            failed.append(char)
    return gifs, failed


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

def esc(s: str) -> str:
    """Escape text for HTML body content (quotes left alone — see attr_esc)."""
    return html.escape(str(s), quote=False)


def attr_esc(s: str) -> str:
    """Escape text destined for an HTML attribute value."""
    return html.escape(str(s), quote=True)


def js_str(value) -> str:
    """A JS literal for `value`, safe to drop into an inline <script>.

    json.dumps handles the quoting and backslashes — which plain %s did not:
    a single " in a quiz note used to produce exp:""雷"は…" and take the whole
    quiz script down with it (no 答え合わせ, and window.__quizKey never set, so
    まとめ then graded every question as unanswered). The extra "</" guard stops
    a closing tag inside the data from ending the <script> element early.
    """
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def indent(block: str, level: int) -> str:
    """Indent every non-blank line of `block` by `level` steps of two spaces.

    Each builder below writes its markup as if it were at the top level; the
    caller pushes it to its real depth with this. That is what keeps the
    generated page indented like hand-written HTML instead of one flat wall.
    """
    pad = "  " * level
    return "\n".join(pad + line if line.strip() else line for line in block.split("\n"))


def banner(text: str) -> str:
    """A comment marking off a top-level part of the page, for eyeball navigation."""
    return f"<!-- ===== {text} ===== -->"


def collapsible(heading: str, body: str) -> str:
    """Wrap a section in a fold-away <details>, open to start with.

    Same mechanism as the quiz's 解答 fold, but the section's own <h2> is the
    <summary>, so the navy heading bar itself is the click target.
    """
    return "\n".join([
        '<details class="sec" open>',
        f"  <summary>{heading}</summary>",
        '  <div class="sec-body">',
        indent(body, 2),
        "  </div>",
        "</details>",
    ])


def chatbox_html() -> str:
    """A general-purpose chat sidebar: no kanji/page context is injected —
    Pedro can ask about anything, not just today's practice. The local
    ask_server answers in a Japanese-teacher voice (see ASK_SYSTEM_PROMPT
    there) and keeps each browser tab's messages as one ongoing conversation
    (via `claude -p --resume`), so this renders a running transcript rather
    than a single one-off answer.

    The grip on the left edge and the ⤢ button both feed CHAT_RESIZE_JS, which
    widens the sidebar (and the page around it) for longer answers.
    """
    return "\n".join([
        '<aside class="chatbox" id="chatbox">',
        '  <div class="chat-resize" id="chatResize" title="ドラッグして幅を変える"></div>',
        '  <div class="chatbox-head">',
        '    <div class="chatbox-title">Claudeに質問する</div>',
        '    <button class="chat-expand" id="chatExpand" type="button"',
        '            title="チャットを広げる" aria-label="チャットの幅を変える">⤢</button>',
        "  </div>",
        '  <p class="en">今日の練習に限らず、日本語のことなら何でも質問できます。</p>',
        '  <div class="chatlog" id="chatlog"></div>',
        '  <div class="chatinput">',
        '    <textarea class="chatta" id="chatta" rows="2"',
        '              placeholder="質問を入力してください（Shift+Enterで送信）"></textarea>',
        '    <div class="chatctl">',
        '      <button class="chatsend" id="chatsend" type="button">送信</button>',
        '      <span class="chat-msg" id="chatmsg"></span>',
        "    </div>",
        "  </div>",
        "</aside>",
    ])


def summarize_button_html() -> str:
    """Fixed bottom-right button that asks the local server to write a
    separate まとめ_<date>.html recapping today's kanji, quiz answers (read
    live from the quiz inputs), and the chat conversation so far."""
    return "\n".join([
        '<div class="summarize-box">',
        '  <span class="summarize-msg" id="summarizeMsg"></span>',
        '  <button class="summarize-btn" id="summarizeBtn" type="button">まとめて</button>',
        "</div>",
    ])


def furigana_toggle_html() -> str:
    """Fixed top-left toggle. Every kanji in examples/quiz questions is
    authored with <ruby>...<rt>reading</rt></ruby>; the <rt> is hidden by
    CSS by default and this button flips a body class to reveal it."""
    return "\n".join([
        '<div class="furigana-toggle-box">',
        '  <button class="furigana-toggle" id="furiganaToggle" type="button">ふりがな</button>',
        "</div>",
    ])


def intro_box_html(theme: str, count: int, chars: str, note: str = "") -> str:
    """The orange "how to use this page" box under the title.

    `note` is an optional page-specific line — the weekly review uses it to say
    that the kanji are ordered by what was missed.
    """
    return "\n".join([
        '<div class="box warm">',
        f"  <p>今日のテーマ：{esc(theme)}　／　今日の{count}字：{chars}</p>",
        *([f'  <p class="en"><b>{esc(note)}</b></p>'] if note else []),
        '  <p class="en">まず読み方と単語を確認してから、最後の復習クイズに挑戦してください。</p>',
        '  <p class="en">※筆順アニメーションは KanjiVG（CC BY-SA 3.0）のデータから作成しています。'
        "赤ではなく画ごとに色が変わり、数字が何画目かを示します。</p>",
        '  <p class="en">※左上の「ふりがな」ボタンで、例文とクイズの読みを表示/非表示にできます。</p>',
        '  <p class="en">※青い見出しをクリックすると、その節をたたんだり開いたりできます。</p>',
        '  <p class="en">※チャット欄は左端をドラッグするか「⤢」を押すと広げられます。</p>',
        "</div>",
    ])


def stroke_player_html(char: str, strokes: str, has_gif: bool) -> str:
    """The stroke-order player: an empty frame that PLAYER_JS fills with a canvas.

    The GIF is *not* inlined here. It lives in the gif-data block at the end of
    the page and is looked up by `data-gif`, so this stays a readable handful of
    lines rather than a quarter-megabyte of base64 sitting in the middle of the
    content.
    """
    frame = (f'<div class="player" data-gif="{attr_esc(char)}">'
             '<div class="wait">筆順を読み込み中…</div></div>'
             if has_gif else
             '<div class="player"><div class="wait">GIFを作れませんでした</div></div>')
    count = f"（{esc(strokes)}画）" if strokes else ""
    return "\n".join([
        '<div class="anim">',
        f"  {frame}",
        f'  <span class="cap"><b>「{esc(char)}」の筆順{count}</b><br>',
        '    <span class="ctl">',
        '      <button class="replay" type="button">もう一度見る</button>',
        '      <label>速さ <input type="range" class="spd" min="0.25" max="3" step="0.25" value="1"></label>',
        '      <b class="spdv">×1.0</b>',
        "    </span></span>",
        "</div>",
    ])


def word_table_html(words: list[dict]) -> str:
    """The 単語/読み/意味 table. Entries are {"w": word, "r": reading, "m": meaning}."""
    rows = [
        f'    <tr><td class="word">{esc(word["w"])}</td>'
        f'<td>{esc(word["r"])}</td><td>{esc(word.get("m", ""))}</td></tr>'
        for word in words
    ]
    return "\n".join([
        "<table>",
        "  <thead>",
        "    <tr><th>単語</th><th>読み</th><th>意味</th></tr>",
        "  </thead>",
        "  <tbody>",
        *rows,
        "  </tbody>",
        "</table>",
    ])


def kanji_section(i: int, kanji: dict, has_gif: bool) -> str:
    """One kanji's block: heading, readings, stroke-order player, words, examples.

    `i` is the 1-based position used in the heading. `has_gif` says whether a
    stroke-order GIF was rendered for this character — the data itself goes in
    the page's gif-data block, not here.
    """
    char = kanji["char"]
    level = kanji.get("level", "N3").upper()
    # "N2" -> "lvl n2" so the CSS can colour-code the badge; anything unexpected
    # falls back to the plain badge.
    level_class = f"lvl n{level[1:]}" if level.startswith("N") and level[1:].isdigit() else "lvl"
    heading = (f'<h2>{i}. <span class="kanji">{esc(char)}</span>'
               f'<span class="{level_class}">{esc(level)}</span></h2>')

    parts = [
        f'<p><b>訓読み</b>：{esc(kanji.get("kun", "—"))}　／　'
        f'<b>音読み</b>：{esc(kanji.get("on", "—"))}</p>',
        f'<p class="en">意味：{esc(kanji.get("meaning", ""))}</p>',
    ]

    # 書き方 line — only rendered if at least one of its pieces is present.
    writing_bits = []
    if kanji.get("strokes"):
        writing_bits.append(f'{esc(kanji["strokes"])}画')
    if kanji.get("radical"):
        writing_bits.append(f'部首：{esc(kanji["radical"])}')
    if kanji.get("order_note"):
        writing_bits.append(f'書き順：{esc(kanji["order_note"])}')
    if writing_bits:
        parts.append(f'<p class="en"><b>書き方</b>：{"　".join(writing_bits)}</p>')

    parts.append("")
    parts.append(stroke_player_html(char, kanji.get("strokes", ""), has_gif))
    parts.append("")
    parts.append(word_table_html(kanji.get("words", [])))
    parts.append("")

    for example in kanji.get("examples", []):
        parts.append(f'<p class="ex">・{example}</p>')  # 例文は <b> や <ruby> を含むので escape しない
    return collapsible(heading, "\n".join(parts).rstrip())


def practice_section(kanji: list[dict]) -> str:
    """The handwriting grid: one canvas per kanji, with a faint model to trace.

    Note this is an f-string, so every literal brace in the JS below is doubled.
    """
    # A JS array literal of [character, "N画"] pairs, e.g. ["雷","13画"].
    items = ", ".join(
        '["%s","%s画"]' % (entry["char"], entry.get("strokes", "")) for entry in kanji
    )
    body = f"""<p class="en">うすいお手本の上をなぞってから、「お手本を隠す」を押して、何も見ないで書いてみてください。
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
</script>"""
    return collapsible("<h2>書き取り練習(マウス・指で書いてみましょう)</h2>", body)


def quiz_chars(quiz: list[dict], kanji: list[dict]) -> list[str]:
    """Which kanji each quiz question is really about, one per question.

    Prefers an explicit "char" on the entry (see SKILL.md). Content files
    written before that field existed fall back to reading the question's
    bolded target word — every question bolds the word it asks about — and
    matching it against today's set. Returns "" when neither works, which
    only costs that question its per-kanji score.
    """
    day_chars = [entry["char"] for entry in kanji]
    resolved = []
    for question in quiz:
        char = question.get("char", "")
        if not char:
            bolded = "".join(re.findall(r"<b>(.*?)</b>", question.get("q", ""), re.S))
            target = re.sub(r"<rt>.*?</rt>", "", bolded)      # ふりがなは字ではない
            target = re.sub(r"<[^>]+>", "", target)
            char = next((c for c in day_chars if c in target), "")
        resolved.append(char)
    return resolved


def quiz_section(quiz: list[dict], chars: list[str]) -> str:
    """The review quiz: input per question, a collapsible answer list, and the
    answer key as JS data for the in-page "answer check" button.

    Each quiz entry is {"q": question HTML, "a": answer, "alt": [near misses],
    "note": explanation, "char": the kanji it tests}. Question text is left
    unescaped because it carries <ruby> markup for the furigana toggle.

    `chars` is the per-question kanji from quiz_chars(); it rides along in the
    answer key so the browser can report which character each result belongs
    to when まとめて is pressed.

    The key is emitted as strict JSON (quoted keys, js_str values) so it is
    both valid JS and parseable by the test suite.

    Built with %-formatting rather than an f-string: the JS below is full of
    literal braces that an f-string would need doubled.
    """
    question_items, answer_items, answer_key = [], [], []
    for i, question in enumerate(quiz):
        question_items.append(
            f'  <li>{question["q"]}\n'
            f'    <div class="qline"><input class="ans" data-q="{i}" placeholder="ひらがなで入力">'
            f'<span class="res" data-r="{i}"></span></div></li>')
        note = question.get("note", "")
        answer_items.append(
            f'    <li>{esc(question["a"])}{("（" + esc(note) + "）") if note else ""}</li>')
        answer_key.append('    {"ok": %s, "alt": %s, "exp": %s, "char": %s}' % (
            js_str([question["a"]]),
            js_str(question.get("alt", [])),
            js_str(esc(note)),                       # innerHTML に入るのでHTMLエスケープしてから
            js_str(chars[i] if i < len(chars) else ""),
        ))
    body = """<p class="en">下の欄に入力して、「答え合わせ」ボタンを押すと、正しいか正しくないかを説明します。</p>

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

  // 入力中の答えを保存し、開き直したときに書き戻す。まとめ機能は入力欄をそのまま
  // 読んで採点するので、保存しないとリロードした時点でその日の記録ごと消える。
  var store = window.__kanjiStore;
  var inputs = document.querySelectorAll("input.ans");
  if (store) {
    var saved = store.load("quiz", []);
    inputs.forEach(function(inp, i){ if (saved[i]) inp.value = saved[i]; });
    var persist = function(){
      store.save("quiz", Array.prototype.map.call(inputs, function(i){ return i.value; }));
    };
    inputs.forEach(function(inp){ inp.addEventListener("input", persist); });
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
    document.getElementById("score").innerHTML="";
    if (store) store.save("quiz", []);
  });
})();
</script>""" % ("\n".join(question_items), "\n".join(answer_items), ",\n".join(answer_key))
    return collapsible("<h2>復習クイズ(読み方をひらがなで書いてください)</h2>", body)


def level_summary(kanji: list[dict]) -> str:
    """"N2 6字／N1 4字" — the level breakdown shown under the title.

    Ordered easiest-first; any level outside N5–N1 is kept and listed last
    rather than dropped.
    """
    counts: dict[str, int] = {}
    for entry in kanji:
        level = entry.get("level", "N3").upper()
        counts[level] = counts.get(level, 0) + 1
    known_order = ["N5", "N4", "N3", "N2", "N1"]
    ordered = ([lvl for lvl in known_order if lvl in counts]
               + [lvl for lvl in counts if lvl not in known_order])
    return "／".join(f"{lvl} {counts[lvl]}字" for lvl in ordered)


def gif_data_html(gifs: dict[str, str | None]) -> str:
    """Every stroke-order GIF as base64, one line per kanji, in a JSON island.

    Parking them here at the end of the page is what keeps the markup above
    readable: each GIF is a couple of hundred kilobytes of base64 that would
    otherwise sit inside an <img src> in the middle of the content. PLAYER_JS
    reads this block and looks each one up by the `data-gif` attribute.
    """
    entries = ",\n".join(
        f"  {json.dumps(char, ensure_ascii=False)}: {json.dumps(b64)}"
        for char, b64 in gifs.items() if b64
    )
    return "\n".join([
        '<script type="application/json" id="gif-data">',
        "{",
        entries,
        "}",
        "</script>",
    ])


def build(content: dict, gifs: dict[str, str | None], content_file: str = "",
          page_id: str = "") -> str:
    """Assemble the whole self-contained practice page and return it as HTML.

    `gifs` maps character -> base64 GIF (or None when rendering failed).
    `content_file` is written onto <body data-content-file> so the "まとめて"
    button can tell the local server which content JSON today's page came from.
    `page_id` namespaces the page's saved quiz answers and chat log; it has to
    separate a day's practice page from that day's review page, which would
    otherwise share a date and overwrite each other's drafts.

    The page is emitted indented and commented, so it can be read (and diffed)
    as HTML rather than only viewed in a browser.
    """
    day = content.get("date") or date.today().isoformat()
    theme = content.get("theme", "")
    chars = "・".join(entry["char"] for entry in content["kanji"])

    sections = []
    for i, entry in enumerate(content["kanji"], 1):
        level = entry.get("level", "N3").upper()
        sections += [banner(f'{i}. {entry["char"]}（{level}）'),
                     kanji_section(i, entry, bool(gifs.get(entry["char"]))), ""]
    sections += [banner("書き取り練習"), practice_section(content["kanji"]), ""]
    sections += [banner("復習クイズ"),
                 quiz_section(content["quiz"],
                              quiz_chars(content["quiz"], content["kanji"])), ""]

    wrap = "\n".join([
        "<h1>漢字練習</h1>",
        f'<p class="sub">{day}　テーマ：<b>{esc(theme)}</b>　'
        f'({level_summary(content["kanji"])})</p>',
        "",
        intro_box_html(theme, len(content["kanji"]), chars, content.get("note", "")),
        "",
        "\n".join(sections).rstrip(),
        "",
        f'<p class="foot">JLPT漢字練習 ／ {day} ／ 筆順データ：KanjiVG (CC BY-SA 3.0)</p>',
    ])

    page = "\n".join([
        banner("本文"),
        '<div class="wrap">',
        indent(wrap, 1),
        "</div>",
        "",
        banner("チャット（サイドバー）"),
        chatbox_html(),
    ])

    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="ja">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>漢字練習 {day}</title>",
        "<style>",
        CSS.strip("\n"),
        "</style>",
        "</head>",
        "",
        f'<body data-content-file="{attr_esc(content_file)}" data-day="{attr_esc(day)}"'
        f' data-page-id="{attr_esc(page_id or day)}">',
        "",
        banner("下書き保存（クイズの答え・チャット。本文より前に読み込む必要がある）"),
        STORE_JS.strip("\n"),
        "",
        banner("ふりがなトグル（左上に固定）"),
        furigana_toggle_html(),
        "",
        '<div class="page">',
        indent(page, 1),
        "</div>",
        "",
        banner("まとめてボタン（右下に固定）"),
        summarize_button_html(),
        "",
        banner("筆順GIFのデータ（本文を読みやすく保つため、まとめてここに置いている）"),
        gif_data_html(gifs),
        "",
        banner("スクリプト"),
        # gifdec.js is inlined so the page plays its GIFs on a canvas (speed
        # control, replay, stop-at-end) instead of as a plain looping <img>.
        PLAYER_JS % (HERE / "gifdec.js").read_text(encoding="utf-8"),
        CHAT_JS,
        CHAT_RESIZE_JS,
        SUMMARIZE_JS,
        FURIGANA_JS,
        "</body>",
        "</html>",
        "",
    ])


def content_json_path(out: Path) -> Path:
    """Where to save the content JSON beside a given output page.

    漢字練習_2026-08-02.html -> content_2026-08-02.json. Any other name is
    just prefixed, so the pairing stays obvious whatever --out is given.
    """
    stem = (out.stem.replace("漢字練習", "content", 1) if "漢字練習" in out.stem
            else f"content_{out.stem}")
    return out.parent / f"{stem}.json"


def merge_content(previous: dict, addition: dict) -> dict:
    """Fold an extra class's content into the same day's existing content.

    Kanji already present are left alone (first class wins), quiz questions are
    appended, and themes are joined with ＋ unless the theme is already there.
    Mutates and returns `previous`.
    """
    seen = {entry["char"] for entry in previous.get("kanji", [])}
    for entry in addition["kanji"]:
        if entry["char"] not in seen:
            previous.setdefault("kanji", []).append(entry)
            seen.add(entry["char"])
    previous.setdefault("quiz", []).extend(addition.get("quiz", []))

    new_theme, prev_theme = addition.get("theme", ""), previous.get("theme", "")
    if new_theme and new_theme not in prev_theme.split("＋"):
        previous["theme"] = f"{prev_theme}＋{new_theme}" if prev_theme else new_theme
    return previous


def main() -> int:
    parser = argparse.ArgumentParser(description="漢字練習HTMLを組み立てる")
    parser.add_argument("content", type=Path, help="内容を書いたJSONファイル")
    parser.add_argument("--out", type=Path,
                        help="出力HTML（既定：~/Documents/Claude-JP/漢字/漢字練習_<date>.html）")
    parser.add_argument("--gifdir", type=Path,
                        default=Path.home() / "Documents" / "Claude-JP" / "漢字" / "gif")
    parser.add_argument("--maker", type=Path, default=DEFAULT_MAKER, help="kanji_gif.py のパス")
    parser.add_argument("--size", type=int, default=240, help="GIFの大きさ（px）")
    parser.add_argument("--skip-gif", action="store_true", help="GIFを作らない（テスト用）")
    parser.add_argument("--conda-env", default="kanji", help="kanji_gif.py を実行するconda環境名")
    parser.add_argument("--kind", choices=["daily", "extra", "review"], default="daily",
                        help="daily=通常の1回、extra=同じ日の追加クラス（既存ファイルに合流する）、"
                             "review=金曜の週次復習")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="履歴JSONのパス")
    parser.add_argument("--content-out", type=Path,
                        help="コンテンツJSONの保存先（既定：out横のcontent_<date>.json）")
    parser.add_argument("--append", action="store_true",
                        help="同じ日付のcontent_<date>.jsonが既にあれば、上書きせずそこに合流させる"
                             "（同じ日の追加クラス用。ファイルがなければ通常通り新規作成）")
    args = parser.parse_args()

    content = json.loads(args.content.read_text(encoding="utf-8"))
    day = content.get("date") or date.today().isoformat()
    out = args.out or (Path.home() / "Documents" / "Claude-JP" / "漢字" / f"漢字練習_{day}.html")
    content_out = args.content_out or content_json_path(out)

    # An extra class on a day that already has a page merges into it rather
    # than overwriting, so the day ends up with one combined page.
    if args.append and content_out.exists():
        content = merge_content(json.loads(content_out.read_text(encoding="utf-8")), content)
        merged_chars = {entry["char"] for entry in content["kanji"]}
        print(f"  合流先: {content_out}（既存 {len(merged_chars)} 字に合流）")

    gifs: dict[str, str | None] = {}
    failed: list[str] = []
    if not args.skip_gif:
        gifs, failed = make_gifs(content["kanji"], args.gifdir, args.maker,
                                 args.size, args.conda_env)

    out.parent.mkdir(parents=True, exist_ok=True)
    content_out.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    out.write_text(build(content, gifs, str(content_out), f"{args.kind}:{day}"),
                   encoding="utf-8")

    history = load_history(args.history)
    dupes = update_history(history, content, args.kind, day, str(content_out))
    # Earlier days' quiz scores are only reachable from here: the chat server
    # that collects them can't write into ~/Documents/Claude-JP itself.
    graded = ingest_quiz_results(history)
    save_history(args.history, history)

    made = sum(1 for gif in gifs.values() if gif)
    print(f"完了：{out}（GIF {made}/{len(content['kanji'])}）")
    if graded:
        print(f"  クイズの成績 {graded} 問分を履歴に記録しました")
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
