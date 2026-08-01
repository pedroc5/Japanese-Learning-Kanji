// 最小限のGIFデコーダ（GIF87a/89a、LZW、透明色、廃棄方法に対応）
function lzwDecode(minCodeSize, data, pixelCount) {
  var out = new Uint8Array(pixelCount);
  var clear = 1 << minCodeSize, eoi = clear + 1;
  var codeSize = minCodeSize + 1, mask = (1 << codeSize) - 1;
  var dictP = [], dictS = [];
  function reset() {
    dictP.length = 0; dictS.length = 0;
    for (var i = 0; i < clear; i++) { dictP.push(-1); dictS.push(i); }
    dictP.push(-1); dictS.push(0);   // clear
    dictP.push(-1); dictS.push(0);   // eoi
    codeSize = minCodeSize + 1; mask = (1 << codeSize) - 1;
  }
  reset();
  var bit = 0, pos = 0, prev = -1, stack = [];
  while (pos < pixelCount) {
    var byteIdx = bit >> 3;
    if (byteIdx >= data.length) break;
    var v = data[byteIdx] | (data[byteIdx + 1] << 8) | (data[byteIdx + 2] << 16);
    var code = (v >> (bit & 7)) & mask;
    bit += codeSize;
    if (code === clear) { reset(); prev = -1; continue; }
    if (code === eoi) break;
    var cur = code;
    if (code >= dictP.length) { cur = prev; stack.push(firstOf(prev)); }
    // コードを展開
    var chain = [];
    var c = cur;
    while (c >= 0) { chain.push(dictS[c]); c = dictP[c]; }
    for (var i = chain.length - 1; i >= 0; i--) {
      if (pos < pixelCount) out[pos++] = chain[i];
    }
    while (stack.length) { if (pos < pixelCount) out[pos++] = stack.pop(); }
    if (prev >= 0 && dictP.length < 4096) {
      dictP.push(prev); dictS.push(chain[chain.length - 1]);
      if (dictP.length === (1 << codeSize) && codeSize < 12) {
        codeSize++; mask = (1 << codeSize) - 1;
      }
    }
    prev = code < dictP.length ? code : dictP.length - 1;
  }
  function firstOf(c) { while (dictP[c] >= 0) c = dictP[c]; return dictS[c]; }
  return out;
}

function decodeGIF(buf) {
  var p = 6;
  function r8() { return buf[p++]; }
  function r16() { var v = buf[p] | (buf[p + 1] << 8); p += 2; return v; }
  var W = r16(), H = r16(), pk = r8(); p += 2;
  var gct = null;
  if (pk & 0x80) { var n = 2 << (pk & 7); gct = buf.subarray(p, p + n * 3); p += n * 3; }
  var frames = [], delay = 10, disposal = 0, transp = -1;
  while (p < buf.length) {
    var b = r8();
    if (b === 0x21) {
      var label = r8();
      if (label === 0xF9) {
        r8(); var f = r8(); delay = r16(); var ti = r8(); r8();
        disposal = (f >> 2) & 7; transp = (f & 1) ? ti : -1;
      } else { var s; while ((s = r8())) p += s; }
    } else if (b === 0x2C) {
      var x = r16(), y = r16(), w = r16(), h = r16(), ipk = r8(), lct = null;
      if (ipk & 0x80) { var m = 2 << (ipk & 7); lct = buf.subarray(p, p + m * 3); p += m * 3; }
      var interlace = !!(ipk & 0x40);
      var minCode = r8(), parts = [], total = 0, sz;
      while ((sz = r8())) { parts.push(buf.subarray(p, p + sz)); total += sz; p += sz; }
      var data = new Uint8Array(total + 3), o = 0;
      parts.forEach(function (a) { data.set(a, o); o += a.length; });
      var px = lzwDecode(minCode, data, w * h);
      if (interlace) px = deinterlace(px, w, h);
      frames.push({ x: x, y: y, w: w, h: h, px: px, pal: lct || gct,
                    transp: transp, delay: delay * 10, disposal: disposal });
      delay = 10; disposal = 0; transp = -1;
    } else break;
  }
  return { width: W, height: H, frames: frames };
}

function deinterlace(px, w, h) {
  var out = new Uint8Array(px.length), rows = [], y;
  for (y = 0; y < h; y += 8) rows.push(y);
  for (y = 4; y < h; y += 8) rows.push(y);
  for (y = 2; y < h; y += 4) rows.push(y);
  for (y = 1; y < h; y += 2) rows.push(y);
  for (var i = 0; i < rows.length; i++) out.set(px.subarray(i * w, i * w + w), rows[i] * w);
  return out;
}

// 各コマを「画面全体のRGBA」に合成する
function composeFrames(gif) {
  var W = gif.width, H = gif.height;
  var canvas = new Uint8ClampedArray(W * H * 4);
  var out = [];
  for (var i = 0; i < gif.frames.length; i++) {
    var f = gif.frames[i];
    var before = f.disposal === 3 ? canvas.slice(0) : null;
    for (var yy = 0; yy < f.h; yy++) {
      for (var xx = 0; xx < f.w; xx++) {
        var ci = f.px[yy * f.w + xx];
        if (ci === f.transp) continue;
        var di = ((f.y + yy) * W + (f.x + xx)) * 4;
        if (di < 0 || di + 3 >= canvas.length) continue;
        canvas[di] = f.pal[ci * 3];
        canvas[di + 1] = f.pal[ci * 3 + 1];
        canvas[di + 2] = f.pal[ci * 3 + 2];
        canvas[di + 3] = 255;
      }
    }
    out.push({ data: canvas.slice(0), delay: f.delay || 100 });
    if (f.disposal === 2) {
      for (var yy2 = 0; yy2 < f.h; yy2++) {
        for (var xx2 = 0; xx2 < f.w; xx2++) {
          var d2 = ((f.y + yy2) * W + (f.x + xx2)) * 4;
          canvas[d2] = canvas[d2 + 1] = canvas[d2 + 2] = canvas[d2 + 3] = 0;
        }
      }
    } else if (f.disposal === 3 && before) { canvas = before; }
  }
  return { width: W, height: H, frames: out };
}

if (typeof module !== "undefined") module.exports = { decodeGIF: decodeGIF, composeFrames: composeFrames };
