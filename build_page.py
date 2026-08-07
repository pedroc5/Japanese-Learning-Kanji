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
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
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
# Everything below is copied verbatim into the generated HTML. Edit with care:
# the pages are self-contained, so a browser only ever sees this copy.
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
  .player .wait{font-size:13px; color:var(--grey); text-align:center; padding-top:72px}
  /* 筆順のSVG。JSが無くても完成した字がそのまま見える（下の html.js が効かないため）。
     JSがあるときは各画をいったん隠して、順番に引いて見せる。 */
  .strokes{display:block; width:170px; height:170px}
  .strokes .grid line{stroke:#f3d3d3; stroke-width:.6; stroke-dasharray:4 3}
  .strokes .grid rect{fill:none; stroke:#f3d3d3; stroke-width:.8}
  .strokes .ghost path{fill:none; stroke:#ececec; stroke-linecap:round; stroke-linejoin:round}
  .strokes .ink path{fill:none; stroke-linecap:round; stroke-linejoin:round}
  .strokes .nums text{font-size:6px; fill:#555; font-family:"Helvetica Neue",Arial,sans-serif}
  /* pathLength="1" なので「1」は画の全長。ギャップを2にするのは、dasharrayが
     周期的に繰り返されるため：ギャップが1だと周期2でちょうど画の終点で模様が
     一巡し、長さ0の破線が round のキャップで「点」として描かれてしまう
     （まだ引いていない画の端に色の点が残る）。ギャップを画より長くすれば
     繰り返しは画の中に入ってこない。 */
  html.js .strokes .ink path{stroke-dasharray:1 2; stroke-dashoffset:1}
  html.js .strokes .nums text{opacity:0}
  .ctl{display:inline-flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px}
  .ctl input[type=range]{width:130px; vertical-align:middle}
  .ctl label{font-size:13px; color:var(--grey)}
  .replay{font-family:inherit; font-size:13px; color:#fff; background:var(--navy);
          border:0; border-radius:4px; padding:5px 12px; cursor:pointer}
  .spdv{color:var(--navy); font-size:13px; min-width:44px; display:inline-block}
  .grid{display:flex; flex-wrap:wrap; gap:14px; margin-top:12px}
  .cell{position:relative; width:112px}
  .cell .padres{font-size:11px; line-height:1.4; text-align:center; margin-top:3px; min-height:15px}
  .cell .padres.ok{color:var(--green)}
  .cell .padres.ng{color:var(--accent)}
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
  /* 復習ページの一覧表（1字1行）。字の欄だけ大きく、その下にレベルと取りこぼしを出す。 */
  .sum{font-size:14px}
  .sum td{padding:8px 10px}
  .sum{table-layout:fixed}
  .sum .ch{text-align:center; white-space:nowrap; width:66px}
  .sum .ch b{font-size:26px; color:var(--navy); line-height:1.2}
  .sum .ch .lvl{display:block; width:fit-content; margin:3px auto 0}
  .sum .miss{display:block; font-size:11px; font-weight:700; color:var(--accent); margin-top:3px}
  .sum td:nth-child(2){width:24%}
  .sum td:nth-child(3){width:30%}
  .sum .yomi{color:var(--navy)}
  .sum .yomi, .sum td:nth-child(4){word-break:break-word}
  /* 練習問題の「読み」「書き」ラベル */
  .qtag{display:inline-block; min-width:2.4em; text-align:center; font-size:11px;
        font-weight:700; color:#fff; background:var(--grey); border-radius:3px;
        padding:1px 6px; margin-right:8px; vertical-align:2px; user-select:none}
  .qtag.write{background:var(--accent)}
  /* 書き取り問題の「ヒント」。問題文の中に置くので、節の <details> とは別の見た目にする。 */
  .hint{display:inline-block; margin:0 0 0 8px; padding:0 10px; font-size:13px;
        background:#fff; border:1px solid var(--border); border-radius:4px}
  .hint summary{padding:4px 0; font-size:12px; color:var(--grey)}
  .hint[open]{background:var(--light); border-color:var(--navy)}
  .hint[open] summary{color:var(--navy)}
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


STROKE_JS = """
<script>
/* 筆順アニメーション：SVGの各画を stroke-dashoffset で引いて見せる。

   画の描き終わりからではなく、パスの弧長そのものに沿って進むので、曲線の
   パラメータ化が不揃いでも筆の速さは一定になる（ブラウザの dash 計算が弧長
   基準であるため、こちらで長さ表を作る必要がない）。
   各パスには pathLength="1" が付けてあるので、dashoffset は
   「まだ引いていない割合」そのものとして 1→0 で扱える。 */
(function(){
  var SPEED = 130;     // ×1のときの筆の速さ（viewBoxの単位／秒）
  var PAUSE = 110;     // 画と画のあいだの間（ミリ秒）
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  document.querySelectorAll(".strokes").forEach(function(svg){
    var inks = Array.prototype.slice.call(svg.querySelectorAll(".ink path"));
    var nums = Array.prototype.slice.call(svg.querySelectorAll(".nums text"));
    if (!inks.length) return;

    function show(i, frac){
      inks[i].style.strokeDashoffset = String(1 - frac);
      if (nums[i]) nums[i].style.opacity = frac > 0 ? "1" : "0";
    }
    function finish(){ inks.forEach(function(_, i){ show(i, 1); }); }

    if (still) { finish(); return; }     // 動きを減らす設定なら完成形のまま

    var lens = inks.map(function(p){
      try { return p.getTotalLength() || 1; } catch (e) { return 40; }
    });
    var speed = 1, raf = null, vt = 0, last = 0;

    function frame(now){
      vt += (now - last) * speed;        // 速さを変えても飛ばずに続くよう、経過を積む
      last = now;
      var acc = 0, running = false;
      for (var i = 0; i < inks.length; i++) {
        var dur = lens[i] / SPEED * 1000;
        var local = vt - acc;
        var frac = local <= 0 ? 0 : (local >= dur ? 1 : local / dur);
        show(i, frac);
        if (frac < 1) running = true;
        acc += dur + PAUSE;
      }
      raf = running ? requestAnimationFrame(frame) : null;   // 最後で停止
    }
    function start(){
      if (raf) cancelAnimationFrame(raf);
      vt = 0; last = performance.now();
      raf = requestAnimationFrame(frame);
    }

    var cap = svg.parentNode.parentNode;
    var btn = cap.querySelector(".replay"), sld = cap.querySelector(".spd"),
        lab = cap.querySelector(".spdv");
    if (btn) btn.onclick = start;
    if (sld) sld.oninput = function(){
      speed = parseFloat(this.value);
      if (lab) lab.textContent = "×" + speed.toFixed(1);
    };
    start();
  });
})();
</script>
"""


PRACTICE_JS = """
<script>
/* 書き取り練習：各字のマスに、押した点を線として記録して描く。

   線は点の列として保存する（ピクセルではなく）。開き直しても消えず、undo・
   保存・簡易チェックがすべて同じ表現の上で完結する。

   簡易チェックは、このブロックのすぐ上にある筆順SVG（.strokes[data-kanji]）に
   既に埋め込まれているKanjiVGのパスを読み直して比べるので、別データは要らない。
*/
(function(){
  var SIZE = 112;
  var store = window.__kanjiStore;
  var saved = store ? store.load("pads", {}) : {};

  function modelStrokes(char){
    var svg = document.querySelector('.strokes[data-kanji="' + char + '"]');
    if (!svg) return null;
    var vb = svg.viewBox.baseVal;
    return Array.prototype.map.call(svg.querySelectorAll(".ink path"), function(p){
      var len = p.getTotalLength();
      var a = p.getPointAtLength(0), b = p.getPointAtLength(len);
      return {ax: (a.x - vb.x) / vb.width, ay: (a.y - vb.y) / vb.height,
              bx: (b.x - vb.x) / vb.width, by: (b.y - vb.y) / vb.height};
    });
  }

  function redraw(entry){
    var ctx = entry.ctx;
    ctx.clearRect(0, 0, SIZE, SIZE);
    entry.strokes.forEach(function(stroke){
      if (stroke.length < 2) return;
      ctx.beginPath();
      ctx.moveTo(stroke[0][0], stroke[0][1]);
      for (var i = 1; i < stroke.length; i++) ctx.lineTo(stroke[i][0], stroke[i][1]);
      ctx.stroke();
    });
  }

  function grade(entry, model){
    var user = entry.strokes;
    if (!model || !model.length || !user.length) return {cls: "", text: ""};

    var notes = [];
    if (user.length !== model.length) {
      notes.push(model.length + "画のところを" + user.length + "画で書いています");
    }
    var n = Math.min(user.length, model.length);
    for (var i = 0; i < n; i++) {
      var s = user[i], a = s[0], b = s[s.length - 1];
      var ux = (b[0] - a[0]) / SIZE, uy = (b[1] - a[1]) / SIZE;
      var mx = model[i].bx - model[i].ax, my = model[i].by - model[i].ay;
      var lu = Math.sqrt(ux * ux + uy * uy), lm = Math.sqrt(mx * mx + my * my);
      if (lu < 0.04 || lm < 0.04) continue;        // 点のような画は向きを判定しない
      var cos = (ux * mx + uy * my) / (lu * lm);
      var dx = a[0] / SIZE - model[i].ax, dy = a[1] / SIZE - model[i].ay;
      if (cos < 0.3) notes.push((i + 1) + "画目の向きが違うようです");
      else if (Math.sqrt(dx * dx + dy * dy) > 0.28) notes.push((i + 1) + "画目の書き始めの位置");
      if (notes.length >= 3) break;                // 指摘しすぎない
    }
    return notes.length ? {cls: "ng", text: "△ " + notes.join("／") + "。"}
                        : {cls: "ok", text: "〇 画数も向きも合っています。"};
  }

  document.querySelectorAll(".practice-block").forEach(function(block){
    var char = block.dataset.char;
    var boxes = parseInt(block.dataset.boxes, 10) || 5;
    var grid = block.querySelector(".grid");
    var model = modelStrokes(char);
    var savedBoxes = saved[char] || [];
    var pads = [], activePad = null, hidden = false;

    function persist(){
      if (!store) return;
      saved[char] = pads.map(function(p){ return p.strokes; });
      store.save("pads", saved);
    }
    function pos(cv, e){
      var r = cv.getBoundingClientRect();
      return [(e.clientX - r.left) * SIZE / r.width, (e.clientY - r.top) * SIZE / r.height];
    }

    var _loop = function(boxIndex){
      var cell = document.createElement("div"); cell.className = "cell";
      var pad = document.createElement("div"); pad.className = "pad";
      var mdl = document.createElement("div"); mdl.className = "model"; mdl.textContent = char;
      var cv = document.createElement("canvas");
      var dpr = window.devicePixelRatio || 1;
      cv.width = SIZE * dpr; cv.height = SIZE * dpr;
      var ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
      ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#1a1a1a";
      var res = document.createElement("div"); res.className = "padres";
      var entry = {pad: pad, ctx: ctx, cv: cv, res: res,
                   strokes: (savedBoxes[boxIndex] || []).slice()};
      var drawing = false;

      cv.addEventListener("pointerdown", function(e){
        drawing = true;
        // マスの外まで一気に引いても線が切れないように。合成イベントでは
        // 捕捉できない（有効なpointerIdが無い）ので、失敗しても続行する。
        try { cv.setPointerCapture(e.pointerId); } catch (err) {}
        activePad = entry;
        entry.strokes.push([pos(cv, e)]);
        res.textContent = ""; res.className = "padres";
      });
      cv.addEventListener("pointermove", function(e){
        if (!drawing) return;
        entry.strokes[entry.strokes.length - 1].push(pos(cv, e));
        redraw(entry);
      });
      ["pointerup","pointercancel","pointerleave"].forEach(function(t){
        cv.addEventListener(t, function(){
          if (!drawing) return;
          drawing = false;
          persist();
        });
      });

      pad.appendChild(mdl); pad.appendChild(cv);
      cell.appendChild(pad); cell.appendChild(res);
      grid.appendChild(cell);
      pads.push(entry);
      redraw(entry);                                 // 前回の続きから
    };
    for (var b = 0; b < boxes; b++) _loop(b);

    var checkBtn = block.querySelector(".checkPads");
    var toggleBtn = block.querySelector(".toggleModel");
    var undoBtn = block.querySelector(".undoStroke");
    var clearBtn = block.querySelector(".clearAll");

    if (toggleBtn) toggleBtn.addEventListener("click", function(){
      hidden = !hidden;
      pads.forEach(function(p){ p.pad.classList.toggle("hide", hidden); });
      this.textContent = hidden ? "お手本を表示" : "お手本を隠す";
    });
    if (undoBtn) undoBtn.addEventListener("click", function(){
      if (!activePad || !activePad.strokes.length) return;
      activePad.strokes.pop();
      redraw(activePad);
      persist();
    });
    if (clearBtn) clearBtn.addEventListener("click", function(){
      pads.forEach(function(p){
        p.strokes.length = 0; redraw(p);
        p.res.textContent = ""; p.res.className = "padres";
      });
      persist();
    });
    if (checkBtn) checkBtn.addEventListener("click", function(){
      var written = 0;
      pads.forEach(function(p){
        var verdict = grade(p, model);
        p.res.textContent = verdict.text;
        p.res.className = "padres " + verdict.cls;
        if (p.strokes.length) written++;
      });
      if (!written) pads[0].res.textContent = "まずマスに書いてみてください。";
    });
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
  // 筆順SVGは既定で「完成した字」を表示している。JSが動くときだけ各画を隠して
  // アニメーションの出発点に戻す — このクラスがそのスイッチ。
  document.documentElement.classList.add("js");

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
# KanjiVG の筆順データ
#
# ページは筆順をGIFではなくインラインSVGで見せるので、ここで必要なのは
# KanjiVGの生データ（各画のパス・画数ラベルの位置・viewBox）だけになった。
# 読み取りは標準ライブラリだけで済むため、このスクリプトはPillowもsvgpathtoolsも
# conda環境も要らない（GIFを作る kanji_gif.py は単体のツールとして残してある）。
# --------------------------------------------------------------------------- #

SVG_NS = "{http://www.w3.org/2000/svg}"

# 画ごとの色。kanji_gif.py の PALETTE と同じ並びにしてあるので、
# 生成したGIFと見比べても同じ配色になる。
STROKE_PALETTE = [
    "#e8453c", "#4a90d9", "#f5a623", "#3aa93a", "#9b7fd4", "#e8194b",
    "#2c3e50", "#16b79b", "#7b5b3f", "#d45fb0", "#8fb701", "#00a0c6",
]


def _group_by_id_prefix(root: ET.Element, prefix: str) -> ET.Element | None:
    """The first <g> whose id starts with `prefix`.

    KanjiVG suffixes its group ids with the character's codepoint, so an exact
    match won't do.
    """
    for group in root.iter(SVG_NS + "g"):
        if (group.get("id") or "").startswith(prefix):
            return group
    return None


def parse_strokes(svg_text: str) -> dict:
    """The bits of a KanjiVG file the page needs, as plain data.

    Returns {"box": [minx, miny, w, h], "width": stroke width, "d": [path, ...],
    "labels": [[x, y], ...]}. Document order in KanjiVG *is* stroke order.

    `labels` may come back empty (or the wrong length) for files without a
    usable kvg:StrokeNumbers group; the page then places the numbers itself
    from the path geometry, which the browser can measure exactly.
    """
    root = ET.fromstring(svg_text)

    box = root.get("viewBox")
    if box:
        minx, miny, width, height = (float(v) for v in re.split(r"[,\s]+", box.strip()))
    else:
        minx = miny = 0.0
        width = float(root.get("width", 109))
        height = float(root.get("height", 109))

    paths_group = _group_by_id_prefix(root, "kvg:StrokePaths") or root
    stroke_width = 3.0
    declared = re.search(r"(?:^|;)\s*stroke-width\s*:\s*([^;]+)",
                         paths_group.get("style", "") or "")
    if declared:
        try:
            stroke_width = float(re.sub(r"[^0-9.]", "", declared.group(1)))
        except ValueError:
            pass                                      # keep the 3.0 default

    paths = [p.get("d") for p in paths_group.iter(SVG_NS + "path") if p.get("d")]
    if not paths:
        raise ValueError("no <path> elements found — is this a KanjiVG file?")

    labels: list[list[float]] = []
    numbers = _group_by_id_prefix(root, "kvg:StrokeNumbers")
    if numbers is not None:
        for text in numbers.iter(SVG_NS + "text"):
            # Usually positioned by transform="matrix(a b c d e f)", where
            # (e, f) is the translation; fall back to plain x/y attributes.
            match = re.search(r"matrix\(([^)]*)\)", text.get("transform", ""))
            nums = ([float(v) for v in re.split(r"[,\s]+", match.group(1).strip()) if v]
                    if match else [])
            if len(nums) == 6:
                labels.append([nums[4], nums[5]])
            else:
                labels.append([float(text.get("x", 0)), float(text.get("y", 0))])

    return {"box": [minx, miny, width, height], "width": stroke_width,
            "d": paths, "labels": labels if len(labels) == len(paths) else []}


def fetch_kanjivg(char: str, cache_dir: Path) -> str | None:
    """One kanji's KanjiVG source, from the cache or from GitHub.

    Downloads are cached by codepoint, so a repeated character (a review page
    re-presenting the week) costs nothing and the daily build works offline
    once a character has been seen.
    """
    codepoint = "%05x" % ord(char)                    # KanjiVG names files by codepoint
    cached = cache_dir / f"{codepoint}.svg"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    try:
        with urllib.request.urlopen(KVG_RAW.format(cp=codepoint), timeout=30) as response:
            svg_text = response.read().decode("utf-8")
    except Exception as e:                            # noqa: BLE001
        print(f"  ! {char} ({codepoint}): KanjiVGを取得できません — {e}", file=sys.stderr)
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(svg_text, encoding="utf-8")
    return svg_text


def load_strokes(kanji: list[dict],
                 cache_dir: Path = DEFAULT_SVG_CACHE) -> tuple[dict[str, dict | None], list[str]]:
    """Stroke data for every kanji. Returns (char -> data or None, failures).

    A character that can't be loaded gets None and a placeholder on the page,
    rather than failing the whole build. Shared by the daily page and the
    weekly review page.
    """
    strokes: dict[str, dict | None] = {}
    failed: list[str] = []
    for entry in kanji:
        char = entry["char"]
        if char in strokes:
            continue
        svg_text = fetch_kanjivg(char, cache_dir)
        try:
            strokes[char] = parse_strokes(svg_text) if svg_text else None
        except Exception as e:                        # noqa: BLE001
            print(f"  ! {char}: 筆順データを読めません — {e}", file=sys.stderr)
            strokes[char] = None
        if not strokes[char]:
            failed.append(char)
    return strokes, failed


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


def intro_box_html(theme: str, count: int, chars: str, note: str = "",
                   strokes: bool = True, period: str = "今日") -> str:
    """The orange "how to use this page" box under the title.

    `note` is an optional page-specific line — the weekly review uses it to say
    that the kanji are ordered by what was missed.
    `strokes` says whether this page actually draws stroke order and tracing
    boxes. The weekly review doesn't, and a box explaining buttons that aren't
    on the page is worse than no box.
    `period` is what this page covers — 今日 for a daily page, 今週 for the
    weekly review.
    """
    stroke_help = [
        '  <p class="en">※筆順アニメーションは KanjiVG（CC BY-SA 3.0）のデータから作成しています。'
        "赤ではなく画ごとに色が変わり、数字が何画目かを示します。</p>",
        '  <p class="en">※各字の書き取り練習では「書き順を確認する」で、画数と各画の向きを見てもらえます。'
        "書き順の基本：①上から下へ　②左から右へ　③横画→縦画　④外側の囲み→中身→ふたは最後。</p>",
    ]
    # 何を読む順に案内するかはページの種類（今日／今週）で決まる。筆順の有無で
    # 決めると、通信に失敗して筆順が読めなかった日々のページが週次ページの案内文に
    # なってしまう。
    lead = ("上の一覧で読み方と意味を思い出してから、下の練習問題を解いてください。"
            "字ごとの筆順・書き取り・例文は、その日の練習ページにあります。" if period == "今週" else
            "まず読み方と単語を確認してから、最後の復習クイズに挑戦してください。")
    return "\n".join([
        '<div class="box warm">',
        f"  <p>{period}のテーマ：{esc(theme)}　／　{period}の{count}字：{chars}</p>",
        *([f'  <p class="en"><b>{esc(note)}</b></p>'] if note else []),
        f'  <p class="en">{lead}</p>',
        *(stroke_help if strokes else []),
        '  <p class="en">※左上の「ふりがな」ボタンで、例文とクイズの読みを表示/非表示にできます。</p>',
        '  <p class="en">※青い見出しをクリックすると、その節をたたんだり開いたりできます。</p>',
        '  <p class="en">※チャット欄は左端をドラッグするか「⤢」を押すと広げられます。</p>',
        "</div>",
    ])


def stroke_svg_html(char: str, data: dict) -> str:
    """The stroke-order drawing as inline SVG: grid, ghost, ink, numbers.

    Every stroke is drawn in full here, so a browser with no JavaScript (and a
    printout) still shows the finished character. STROKE_JS hides them again —
    via the html.js class — and draws them back in order. pathLength="1" makes
    each path's dash offset mean "fraction still unwritten", so the animation
    needs no length table of its own.

    Numbers are only emitted when KanjiVG gave us anchors for them; otherwise
    the page places them from the path geometry, which the browser can measure
    exactly and we cannot without a curve library.
    """
    minx, miny, width, height = data["box"]
    ink_width = data["width"]
    paths, labels = data["d"], data["labels"]
    cx, cy = minx + width / 2, miny + height / 2

    lines = [
        f'<svg class="strokes" viewBox="{minx:g} {miny:g} {width:g} {height:g}"',
        f'     data-kanji="{attr_esc(char)}" role="img"'
        f' aria-label="「{attr_esc(char)}」の筆順（{len(paths)}画）">',
        '  <g class="grid">',
        f'    <rect x="{minx:g}" y="{miny:g}" width="{width:g}" height="{height:g}"/>',
        f'    <line x1="{cx:g}" y1="{miny:g}" x2="{cx:g}" y2="{miny + height:g}"/>',
        f'    <line x1="{minx:g}" y1="{cy:g}" x2="{minx + width:g}" y2="{cy:g}"/>',
        "  </g>",
        f'  <g class="ghost" stroke-width="{ink_width:g}">',
    ]
    lines += [f'    <path d="{attr_esc(d)}"/>' for d in paths]
    lines += ["  </g>", f'  <g class="ink" stroke-width="{ink_width:g}">']
    lines += [
        f'    <path pathLength="1" stroke="{STROKE_PALETTE[i % len(STROKE_PALETTE)]}"'
        f' d="{attr_esc(d)}"/>'
        for i, d in enumerate(paths)
    ]
    lines += ["  </g>"]
    if labels:
        lines.append('  <g class="nums">')
        lines += [f'    <text x="{x:g}" y="{y:g}">{i + 1}</text>'
                  for i, (x, y) in enumerate(labels)]
        lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines)


def stroke_player_html(char: str, strokes: str, data: dict | None) -> str:
    """The stroke-order box plus its replay/speed controls."""
    if data:
        frame = "\n".join(['<div class="player">',
                           indent(stroke_svg_html(char, data), 1),
                           "</div>"])
    else:
        frame = '<div class="player"><div class="wait">筆順データを読めませんでした</div></div>'
    count = f"（{esc(strokes)}画）" if strokes else ""
    return "\n".join([
        '<div class="anim">',
        indent(frame, 1),
        f'  <span class="cap"><b>「{esc(char)}」の筆順{count}</b><br>',
        '    <span class="ctl">',
        '      <button class="replay" type="button">もう一度見る</button>',
        '      <label>速さ <input type="range" class="spd" min="0.25" max="3" step="0.25" value="1"></label>',
        '      <b class="spdv">×1.0</b>',
        "    </span></span>",
        "</div>",
    ])


def writing_practice_html(char: str, strokes: str, boxes: int = 5) -> str:
    """A row of blank tracing boxes for one kanji, dropped right under its stroke animation.

    Only the shell is emitted here — PRACTICE_JS fills in `.grid` at load time,
    since each canvas needs the live devicePixelRatio to size itself. Its
    grading reuses the KanjiVG paths already inlined in this same kanji's
    stroke-order SVG just above (`.strokes[data-kanji]`), so no extra data is
    needed here beyond the character and its stroke count.
    """
    return "\n".join([
        f'<div class="practice-block" data-char="{attr_esc(char)}"'
        f' data-strokes="{attr_esc(strokes)}" data-boxes="{boxes}">',
        '  <p class="en"><b>書き取り練習</b>：うすいお手本をなぞってから、'
        '「お手本を隠す」を押して書いてみましょう。</p>',
        '  <div class="grid"></div>',
        '  <p>',
        '    <button class="btn checkPads" type="button">書き順を確認する</button>',
        '    <button class="btn sub2 toggleModel" type="button">お手本を隠す</button>',
        '    <button class="btn sub2 undoStroke" type="button">一画戻す</button>',
        '    <button class="btn sub2 clearAll" type="button">全部消す</button>',
        '  </p>',
        '</div>',
    ])


def rich(text: str) -> str:
    """Text from the content file that may already carry <ruby> furigana.

    The word tables were plain text before the ふりがな toggle reached them, so
    anything without ruby markup is still escaped as ordinary text; a cell the
    model wrote with furigana is passed through as markup instead.
    """
    text = str(text)
    return text if "<ruby>" in text else esc(text)


def _is_kana(ch: str) -> bool:
    """True for hiragana, katakana and the long vowel mark — anything that
    already reads itself and so needs no furigana above it."""
    return "぀" <= ch <= "ヿ" or ch == "ー"


def _to_hira(text: str) -> str:
    """Katakana → hiragana, character for character, so a word written in
    katakana can be lined up against a reading written in hiragana."""
    return "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text
    )


def word_ruby(word: str, reading: str) -> str:
    """<ruby> markup for a word in the vocabulary table, built from its 読み.

    The reading covers the whole word, so the furigana has to be split back
    over the kanji runs only: 泊まる + 「と(まる)」 gives ruby(泊 → と) followed
    by a bare まる, not とまる floating over the whole word. The kana already in
    the word are what anchors the split — each kana run is located in the
    reading, and whatever sits between two anchors belongs to the kanji run
    between them.

    Anything that doesn't line up (an irregular reading, a 「・」 listing two of
    them) falls back to one ruby over the whole word, which is still correct,
    just less pretty. Words with no kanji at all get no ruby.
    """
    full = re.sub(r"[()（）]", "", str(reading)).strip()
    word = str(word)
    if not full or all(_is_kana(ch) for ch in word):
        return esc(word)

    # Split the word into alternating kana / non-kana runs.
    runs: list[tuple[bool, str]] = []
    for ch in word:
        kana = _is_kana(ch)
        if runs and runs[-1][0] == kana:
            runs[-1] = (kana, runs[-1][1] + ch)
        else:
            runs.append((kana, ch))

    fallback = f"<ruby>{esc(word)}<rt>{esc(full)}</rt></ruby>"
    hira = _to_hira(full)                      # index-for-index with `full`
    out, pos, pending = [], 0, ""
    for kana, text in runs:
        if not kana:
            pending = text
            continue
        # A kanji run must take at least one character of the reading.
        found = hira.find(_to_hira(text), pos + 1 if pending else pos)
        if found < 0 or (not pending and found != pos):
            return fallback
        if pending:
            out.append(f'<ruby>{esc(pending)}<rt>{esc(full[pos:found])}</rt></ruby>')
            pending = ""
        out.append(esc(text))
        pos = found + len(text)
    if pending:
        if pos >= len(full):
            return fallback
        out.append(f'<ruby>{esc(pending)}<rt>{esc(full[pos:])}</rt></ruby>')
    elif pos != len(full):
        return fallback                        # reading left over past the word
    return "".join(out)


def word_table_html(words: list[dict]) -> str:
    """The 単語/読み/意味/English table.

    Entries are {"w": word, "r": reading, "m": Japanese meaning, "e": English}.
    `e` is optional: content files written before the column existed simply get
    an empty last cell rather than failing to build.

    The 単語 column carries furigana of its own (word_ruby), so the ふりがな
    toggle works on the table too and not only on the sentences; the 意味
    column shows whatever furigana the content file wrote into it.
    """
    rows = [
        f'    <tr><td class="word">{word_ruby(word["w"], word.get("r", ""))}</td>'
        f'<td>{esc(word["r"])}</td>'
        f'<td>{rich(word.get("m", ""))}</td>'
        f'<td class="en">{esc(word.get("e", ""))}</td></tr>'
        for word in words
    ]
    return "\n".join([
        "<table>",
        "  <thead>",
        "    <tr><th>単語</th><th>読み</th><th>意味</th><th>English</th></tr>",
        "  </thead>",
        "  <tbody>",
        *rows,
        "  </tbody>",
        "</table>",
    ])


def kanji_section(i: int, kanji: dict, stroke_data: dict | None) -> str:
    """One kanji's block: heading, readings, stroke-order drawing, words, examples.

    `i` is the 1-based position used in the heading. `stroke_data` is this
    character's KanjiVG paths (or None when they couldn't be loaded).
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
    parts.append(stroke_player_html(char, kanji.get("strokes", ""), stroke_data))
    parts.append("")
    parts.append(writing_practice_html(char, kanji.get("strokes", "")))
    parts.append("")
    parts.append(word_table_html(kanji.get("words", [])))
    parts.append("")

    for example in kanji.get("examples", []):
        parts.append(f'<p class="ex">・{example}</p>')  # 例文は <b> や <ruby> を含むので escape しない
    return collapsible(heading, "\n".join(parts).rstrip())


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


def plain_sentence(text: str) -> str:
    """A sentence stripped down to what is actually being read: no markup, no
    furigana, no spacing or closing punctuation. Two sentences that differ only
    in those come out identical."""
    text = re.sub(r"<rt>.*?</rt>", "", str(text), flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip("。．.！!？?、,")


def reused_examples(content: dict) -> list[str]:
    """Quiz questions that are just one of the day's example sentences again.

    The quiz is meant to test the reading in a *new* sentence — a question
    copied from the 例文 a few lines above only tests whether Pedro remembers
    the page. Substrings count too, since trimming an example down to its first
    clause is the same sentence for this purpose.

    Returns the offending question text (stripped) for the warning in main().
    """
    examples = [plain_sentence(ex)
                for entry in content.get("kanji", [])
                for ex in entry.get("examples", [])]
    hits = []
    for question in content.get("quiz", []):
        sentence = plain_sentence(question.get("q", ""))
        if len(sentence) < 6:
            continue
        if any(sentence == ex or (len(ex) >= 6 and (sentence in ex or ex in sentence))
               for ex in examples):
            hits.append(sentence)
    return hits


def quiz_section(quiz: list[dict], chars: list[str],
                 heading: str = "復習クイズ(読み方をひらがなで書いてください)") -> str:
    """The review quiz: input per question, a collapsible answer list, and the
    answer key as JS data for the in-page "answer check" button.

    Each quiz entry is {"q": question HTML, "a": answer, "alt": [near misses],
    "note": explanation, "char": the kanji it tests}. Question text is left
    unescaped because it carries <ruby> markup for the furigana toggle.

    An entry may also carry "ph", the input's placeholder. It defaults to
    ひらがなで入力 because nearly every question asks for a reading; the weekly
    page's 書き取り questions want 漢字で入力 instead.

    `heading` names the section — the weekly page's list is not only readings,
    so it says so. The section is returned already wrapped in its foldable
    heading; callers place it in the page, they don't wrap it again.

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
            f'    <div class="qline"><input class="ans" data-q="{i}"'
            f' placeholder="{attr_esc(question.get("ph", "ひらがなで入力"))}">'
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
    return collapsible(f"<h2>{esc(heading)}</h2>", body)


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


def build(content: dict, strokes: dict[str, dict | None], content_file: str = "",
          page_id: str = "", sections: list[str] | None = None,
          heading: str = "漢字練習", period: str = "今日") -> str:
    """Assemble the whole self-contained practice page and return it as HTML.

    `strokes` maps character -> KanjiVG stroke data (or None when it couldn't
    be loaded); see load_strokes().
    `content_file` is written onto <body data-content-file> so the "まとめて"
    button can tell the local server which content JSON today's page came from.
    `page_id` namespaces the page's saved quiz answers and chat log; it has to
    separate a day's practice page from that day's review page, which would
    otherwise share a date and overwrite each other's drafts.
    `sections` replaces the body — one block of HTML per section. The daily page
    leaves it None and gets the standard "a section per kanji, then the quiz";
    build_review.py passes its own (a summary table, then the exercises) so the
    weekly page can be shaped differently while sharing this shell: same CSS,
    same furigana toggle, same drafts, same chat and まとめ wiring.
    `heading` is the <h1> and the browser title, `period` the word the intro
    box uses for what the page covers (今日 / 今週).

    The page is emitted indented and commented, so it can be read (and diffed)
    as HTML rather than only viewed in a browser.
    """
    day = content.get("date") or date.today().isoformat()
    theme = content.get("theme", "")
    chars = "・".join(entry["char"] for entry in content["kanji"])

    if sections is None:
        sections = []
        for i, entry in enumerate(content["kanji"], 1):
            level = entry.get("level", "N3").upper()
            sections += [banner(f'{i}. {entry["char"]}（{level}）'),
                         kanji_section(i, entry, strokes.get(entry["char"])), ""]
        sections += [banner("復習クイズ"),
                     quiz_section(content["quiz"],
                                  quiz_chars(content["quiz"], content["kanji"])), ""]

    # KanjiVG is credited where its data is actually embedded, so the review
    # page — which draws no strokes — doesn't claim to use it.
    credit = " ／ 筆順データ：KanjiVG (CC BY-SA 3.0)" if any(strokes.values()) else ""
    wrap = "\n".join([
        f"<h1>{esc(heading)}</h1>",
        f'<p class="sub">{day}　テーマ：<b>{esc(theme)}</b>　'
        f'({level_summary(content["kanji"])})</p>',
        "",
        intro_box_html(theme, len(content["kanji"]), chars, content.get("note", ""),
                       strokes=bool(any(strokes.values())), period=period),
        "",
        "\n".join(sections).rstrip(),
        "",
        f'<p class="foot">JLPT漢字練習 ／ {day}{credit}</p>',
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
        f"<title>{esc(heading)} {day}</title>",
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
        banner("スクリプト"),
        STROKE_JS,
        PRACTICE_JS,
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
    parser.add_argument("--svg-cache", type=Path, default=DEFAULT_SVG_CACHE,
                        help="KanjiVGのSVGを置くキャッシュ（既定：スキルフォルダの.kanjivg_cache）")
    parser.add_argument("--skip-strokes", action="store_true",
                        help="筆順データを読み込まない（テスト用）")
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

    strokes: dict[str, dict | None] = {}
    failed: list[str] = []
    if not args.skip_strokes:
        strokes, failed = load_strokes(content["kanji"], args.svg_cache)

    out.parent.mkdir(parents=True, exist_ok=True)
    content_out.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    out.write_text(build(content, strokes, str(content_out), f"{args.kind}:{day}"),
                   encoding="utf-8")

    history = load_history(args.history)
    dupes = update_history(history, content, args.kind, day, str(content_out))
    # Earlier days' quiz scores are only reachable from here: the chat server
    # that collects them can't write into ~/Documents/Claude-JP itself.
    graded = ingest_quiz_results(history)
    save_history(args.history, history)

    loaded = sum(1 for data in strokes.values() if data)
    print(f"完了：{out}（筆順 {loaded}/{len(content['kanji'])}字）")
    if graded:
        print(f"  クイズの成績 {graded} 問分を履歴に記録しました")
    if dupes:
        print(f"警告: 履歴上すでに使用済みの字が含まれています — {'・'.join(dupes)}", file=sys.stderr)
        print("   選定時にkanji_history.jsonを確認し損ねた可能性があります。", file=sys.stderr)
    recycled = reused_examples(content)
    if recycled:
        print(f"警告: 例文の使い回しがクイズに {len(recycled)} 問あります — "
              f"{'／'.join(recycled[:3])}{' …' if len(recycled) > 3 else ''}", file=sys.stderr)
        print("   クイズは同じ単語を使った別の文で出してください。", file=sys.stderr)
    if failed:
        print(f"警告: 筆順データを読めなかった字があります — {'・'.join(failed)}", file=sys.stderr)
        print("   ネットワークを確認し、必要なら再実行してください。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
