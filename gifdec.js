// 最小限のGIFデコーダ（GIF87a/89a、LZW、透明色、廃棄方法に対応）
//
// 使い方: composeFrames(decodeGIF(bytes)) で、各コマが「画面全体のRGBA」に
// なった配列が返る。漢字練習ページはこれをcanvasに描いて、速さ調整・もう一度・
// 最後で停止ができるようにしている（<img>のままでは制御できないため）。

// GIFのLZW圧縮を展開して、パレット番号の並び（1ピクセル1バイト）を返す。
// 辞書は「親コード＋1バイト」の連結リストで持つ: あるコードの並びは、
// suffixOf[code] を集めながら prefixOf[code] を根までたどると逆順で得られる。
function lzwDecode(minCodeSize, data, pixelCount) {
  var out = new Uint8Array(pixelCount);
  var clearCode = 1 << minCodeSize, endCode = clearCode + 1;
  var codeSize = minCodeSize + 1, mask = (1 << codeSize) - 1;
  var prefixOf = [], suffixOf = [];

  // 辞書を初期状態（1バイトずつの並び＋制御コード2つ）に戻す
  function resetDict() {
    prefixOf.length = 0; suffixOf.length = 0;
    for (var i = 0; i < clearCode; i++) { prefixOf.push(-1); suffixOf.push(i); }
    prefixOf.push(-1); suffixOf.push(0);   // clearCode の分
    prefixOf.push(-1); suffixOf.push(0);   // endCode の分
    codeSize = minCodeSize + 1; mask = (1 << codeSize) - 1;
  }

  // そのコードが表す並びの「最初の1バイト」（根までたどる）
  function firstByteOf(code) {
    while (prefixOf[code] >= 0) code = prefixOf[code];
    return suffixOf[code];
  }

  resetDict();
  var bit = 0, pos = 0, prevCode = -1, pending = [];
  while (pos < pixelCount) {
    // コードはバイト境界をまたぐので、3バイト読んでからビット位置で切り出す
    var byteIdx = bit >> 3;
    if (byteIdx >= data.length) break;
    var window = data[byteIdx] | (data[byteIdx + 1] << 8) | (data[byteIdx + 2] << 16);
    var code = (window >> (bit & 7)) & mask;
    bit += codeSize;

    if (code === clearCode) { resetDict(); prevCode = -1; continue; }
    if (code === endCode) break;

    // 未登録のコード（いわゆるKwKwKの場合）は、直前の並び＋その先頭バイトになる
    var current = code;
    if (code >= prefixOf.length) { current = prevCode; pending.push(firstByteOf(prevCode)); }

    // 連結リストを逆順にたどってから、正順で書き出す
    var chain = [];
    var node = current;
    while (node >= 0) { chain.push(suffixOf[node]); node = prefixOf[node]; }
    for (var i = chain.length - 1; i >= 0; i--) {
      if (pos < pixelCount) out[pos++] = chain[i];
    }
    while (pending.length) { if (pos < pixelCount) out[pos++] = pending.pop(); }

    // 「直前の並び＋今回の先頭バイト」を新しいコードとして登録する
    if (prevCode >= 0 && prefixOf.length < 4096) {
      prefixOf.push(prevCode); suffixOf.push(chain[chain.length - 1]);
      if (prefixOf.length === (1 << codeSize) && codeSize < 12) {
        codeSize++; mask = (1 << codeSize) - 1;   // 辞書が埋まったらコード長を伸ばす
      }
    }
    prevCode = code < prefixOf.length ? code : prefixOf.length - 1;
  }
  return out;
}

// GIFのバイト列を読んで、{width, height, frames} を返す。
// frames の各要素はまだ「差分の小さな矩形」のままなので、画面全体の絵にするには
// composeFrames() を通すこと。
function decodeGIF(buf) {
  var pos = 6;                                  // "GIF89a" の6バイトを読み飛ばす
  function readByte() { return buf[pos++]; }
  function readShort() { var v = buf[pos] | (buf[pos + 1] << 8); pos += 2; return v; }

  var width = readShort(), height = readShort(), flags = readByte();
  pos += 2;                                     // 背景色番号とアスペクト比（未使用）

  var globalPalette = null;
  if (flags & 0x80) {                           // 全体パレットあり
    var globalSize = 2 << (flags & 7);
    globalPalette = buf.subarray(pos, pos + globalSize * 3);
    pos += globalSize * 3;
  }

  // 次のコマに適用する設定。グラフィック制御拡張を読むたびに更新される。
  var frames = [], delay = 10, disposal = 0, transparent = -1;
  while (pos < buf.length) {
    var block = readByte();

    if (block === 0x21) {                       // 拡張ブロック
      var label = readByte();
      if (label === 0xF9) {                     // グラフィック制御拡張
        readByte();                             // ブロックサイズ（常に4）
        var packed = readByte();
        delay = readShort();
        var transparentIndex = readByte();
        readByte();                             // ブロック終端
        disposal = (packed >> 2) & 7;
        transparent = (packed & 1) ? transparentIndex : -1;
      } else {                                  // それ以外（コメント等）は読み飛ばす
        var skip;
        while ((skip = readByte())) pos += skip;
      }

    } else if (block === 0x2C) {                // 画像ブロック
      var x = readShort(), y = readShort(), w = readShort(), h = readShort();
      var imageFlags = readByte(), localPalette = null;
      if (imageFlags & 0x80) {                  // このコマ専用のパレット
        var localSize = 2 << (imageFlags & 7);
        localPalette = buf.subarray(pos, pos + localSize * 3);
        pos += localSize * 3;
      }
      var interlaced = !!(imageFlags & 0x40);

      // 画素データは細切れのサブブロックで届くので、ひと続きに繋ぎ直す。
      // 末尾の+3は、lzwDecodeが常に3バイト先読みするための余白。
      var minCodeSize = readByte(), chunks = [], total = 0, chunkSize;
      while ((chunkSize = readByte())) {
        chunks.push(buf.subarray(pos, pos + chunkSize));
        total += chunkSize;
        pos += chunkSize;
      }
      var data = new Uint8Array(total + 3), offset = 0;
      chunks.forEach(function (chunk) { data.set(chunk, offset); offset += chunk.length; });

      var pixels = lzwDecode(minCodeSize, data, w * h);
      if (interlaced) pixels = deinterlace(pixels, w, h);
      frames.push({ x: x, y: y, w: w, h: h, px: pixels,
                    pal: localPalette || globalPalette,
                    transp: transparent, delay: delay * 10, disposal: disposal });

      delay = 10; disposal = 0; transparent = -1;   // 次のコマ用に初期化
    } else break;                                   // 終端（0x3B）や壊れたデータ
  }
  return { width: width, height: height, frames: frames };
}

// インターレースGIFの行順（1/8, 1/8ずれ, 1/4, 1/2）を通常の並びに戻す
function deinterlace(px, w, h) {
  var out = new Uint8Array(px.length), rows = [], y;
  for (y = 0; y < h; y += 8) rows.push(y);
  for (y = 4; y < h; y += 8) rows.push(y);
  for (y = 2; y < h; y += 4) rows.push(y);
  for (y = 1; y < h; y += 2) rows.push(y);
  for (var i = 0; i < rows.length; i++) out.set(px.subarray(i * w, i * w + w), rows[i] * w);
  return out;
}

// 各コマを「画面全体のRGBA」に合成する。
// GIFのコマは前のコマとの差分なので、1枚のcanvasに順番に重ね、その都度コピーを取る。
function composeFrames(gif) {
  var width = gif.width, height = gif.height;
  var canvas = new Uint8ClampedArray(width * height * 4);
  var out = [];

  for (var i = 0; i < gif.frames.length; i++) {
    var frame = gif.frames[i];
    // 廃棄方法3＝「前の状態に戻す」なので、上書きする前に控えておく
    var restorePoint = frame.disposal === 3 ? canvas.slice(0) : null;

    for (var yy = 0; yy < frame.h; yy++) {
      for (var xx = 0; xx < frame.w; xx++) {
        var colorIndex = frame.px[yy * frame.w + xx];
        if (colorIndex === frame.transp) continue;      // 透明画素は下の絵を残す
        var at = ((frame.y + yy) * width + (frame.x + xx)) * 4;
        if (at < 0 || at + 3 >= canvas.length) continue;
        canvas[at] = frame.pal[colorIndex * 3];
        canvas[at + 1] = frame.pal[colorIndex * 3 + 1];
        canvas[at + 2] = frame.pal[colorIndex * 3 + 2];
        canvas[at + 3] = 255;
      }
    }
    out.push({ data: canvas.slice(0), delay: frame.delay || 100 });

    if (frame.disposal === 2) {                          // 2＝この領域を消す
      for (var cy = 0; cy < frame.h; cy++) {
        for (var cx = 0; cx < frame.w; cx++) {
          var clearAt = ((frame.y + cy) * width + (frame.x + cx)) * 4;
          canvas[clearAt] = canvas[clearAt + 1] = canvas[clearAt + 2] = canvas[clearAt + 3] = 0;
        }
      }
    } else if (frame.disposal === 3 && restorePoint) { canvas = restorePoint; }
  }
  return { width: width, height: height, frames: out };
}

if (typeof module !== "undefined") module.exports = { decodeGIF: decodeGIF, composeFrames: composeFrames };
