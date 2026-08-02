---
name: kanji-practice
description: Build Pedro's daily JLPT kanji practice page — pick 10 kanji sharing a single theme (e.g. food, city, tools), verify each one's real JLPT level on jlptsensei.com (levels can mix), write the readings, words, examples and quiz in Japanese, generate KanjiVG stroke-order GIFs, and assemble a self-contained HTML file. Use when asked for today's kanji practice, /kanji-practice, or when the daily scheduled run fires.
---

# 漢字練習ページを作る

Pedro（JLPT学習者）向けの毎日の漢字練習HTMLを作る。文章はすべて**日本語**で書く。

## 手順

### 1. 履歴を確認する

`~/Documents/Claude-JP/漢字/kanji_history.json` を読む。これが**全期間の履歴**（`build_page.py`/`build_review.py`
が毎回自動更新する）で、直近のファイルだけを見るのではなく、ここに載っている字は**一度でも出したら二度と出さない**。

- `history["kanji"]` … これまでに使った字とレベル・テーマ・使用日の辞書。候補の字はここと必ず突き合わせる。
- `history["days"]` … 日ごとの記録（テーマ・その日の字・kind: daily/extra/review）。直近のテーマ傾向を見るのに使う。
- 万一、選定ミスで既出の字を出してしまっても `build_page.py` が「警告: 履歴上すでに使用済みの字」と教えてくれるが、
  それは事後検知なので、**選ぶ前に必ずこのファイルを読んで避けること**。

### 2. テーマを決める

その日の10字は、できれば**単一のテーマ**でまとめる（例：食べ物、家の道具、街・交通、天気、体・健康、仕事、感情、旅行など）。
テーマ自体は自由に決めてよい。

- `history["days"]` の直近3〜5日分の `theme` を見て、**最近使ったテーマは避ける**。
- 目安として、偶数日は少し難しめ（N2〜N1寄り）、奇数日は基礎寄り（N3寄り）を意識してよいが、
  これは緩い目安であり、テーマに合う字・実際のレベル確認の結果を優先する（無理に比率を合わせない）。
- **例外：** 履歴の「二度と出さない」制約のせいで、そのテーマに合う未出の字が10字集まらない場合は、
  無理にテーマを守らなくてよい。テーマに合う字を集められるだけ集めたら、残りは別テーマ・関連なしの
  日常でよく使う字で埋めてよい（`theme` にはメインテーマ名だけ、または「〇〇＋雑多」のように書けばよい）。

### 3. 漢字を10字選ぶ、レベルをjlptsenseiで確認する

- テーマに合う、日常でよく使う字を10字（候補は12〜15字くらい出しておくと、レベル確認後に調整しやすい）。
- 候補は必ず `kanji_history.json` の `history["kanji"]` に**存在しない字**から選ぶ。
- 音読み・訓読みが両方ある字を選ぶと練習になる。
- **N1も対象に含めてよい。** N2/N3中心という制約はない。テーマに合えばN5〜N1のどのレベルでも構わない。
- **各候補字のJLPTレベルを提案前に https://jlptsensei.com/ で確認する。** 記憶や推測だけで level を決めない。
  - レベル一覧ページ：`https://jlptsensei.com/jlpt-n5-kanji-list/`〜`jlpt-n1-kanji-list/`（N5〜N1）。
    ページ分割されていることが多く、2ページ目以降は `.../page/2/`, `.../page/3/`... で続く。
  - WebFetchで該当ページを開き、「このテーブルに次の字は含まれているか：〇・〇・〇…」と具体的に聞いて、
    含まれていた行の音読み・訓読み・意味も一緒に確認する（表全体を漠然と要約させない）。
  - あるページの取得が繰り返し空振りする場合（サイト側の一時的な不具合）、無理に粘らず、
    その字は他の未確認レベル（N1含む）を試すか、代わりの候補に差し替える。
  - どのレベルか見当がつかない字は、N3→N4→N2→N5→N1の順くらいで総当たりする。
  - レベルが混在しても構わない（例：N4が3字、N3が5字、N1が2字）。ただし各字の `level` は
    実際にjlptsenseiで確認できたレベルを正確に入れる。確認できなかった字は候補から外す。

### 4. 内容をJSONに書く

`/tmp/kanji_content.json` に次の形式で書く。**例文とクイズの問題文の中の漢字は、対象の熟語だけ`<b>`で囲み、
文中の漢字は（対象語も含めて）すべて`<ruby>字<rt>よみ</rt></ruby>`でふりがなを付ける**
（ページ右上「ふりがな」トグルで表示/非表示を切り替えるため。JSONの中でHTMLタグを使ってよいのは
`examples` と `quiz.q` だけ）。

```json
{
  "date": "2026-08-02",
  "theme": "食べ物",
  "kanji": [
    {
      "char": "泊",
      "level": "N3",
      "on": "ハク",
      "kun": "と(まる)・と(める)",
      "meaning": "とまる(宿泊する) ／ to stay overnight",
      "strokes": 8,
      "radical": "さんずい(氵)",
      "order_note": "「氵」(3画)→右の「白」(5画)。",
      "words": [
        {"w": "宿泊", "r": "しゅくはく", "m": "とまること"},
        {"w": "一泊", "r": "いっぱく", "m": "一晩とまること"},
        {"w": "素泊まり", "r": "すどまり", "m": "食事なしの宿泊"},
        {"w": "泊まる", "r": "と(まる)", "m": "とまる"},
        {"w": "泊める", "r": "と(める)", "m": "とめてあげる"}
        /* ... 合計10個くらいまで、音読み・訓読みをバランスよく ... */
      ],
      "examples": [
        "<ruby>京都<rt>きょうと</rt></ruby>に<b><ruby>二泊三日<rt>にはくみっか</rt></ruby></b>で<ruby>旅行<rt>りょこう</rt></ruby>しました。",
        "<ruby>友<rt>とも</rt></ruby>だちを<ruby>一晩<rt>ひとばん</rt></ruby>家に<b><ruby>泊<rt>と</rt></ruby></b>めてあげた。"
        /* ... 合計10文くらいまで ... */
      ]
    }
  ],
  "quiz": [
    {
      "q": "この<ruby>ホテル<rt></rt></ruby>の<b><ruby>宿泊<rt>しゅくはく</rt></ruby></b>料金は<ruby>一人<rt>ひとり</rt></ruby><ruby>八千円<rt>はっせんえん</rt></ruby>です。",
      "a": "しゅくはく",
      "alt": ["しゅくばく"],
      "note": "音読みの熟語です。訓読みの「泊まる」は「とまる」。"
    }
  ]
}
```

守ること:

- `kanji` はちょうど10字、全字が同じテーマ。各字に単語を**4〜5個**（音読みの熟語と訓読みの語をバランスよく）、例文を**2〜3文**。
- `theme` は必須。`level` は10字それぞれについて、jlptsenseiで確認した実際のレベル（N5〜N1のいずれか）を入れる。混在してよい。
- 例文はなるべく単語リストの語を使う。
- **`examples` と `quiz.q` の中の漢字は全て`<ruby>...<rt>よみ</rt></ruby>`で読みを付ける**
  （カタカナ語・ひらがなだけの語には付けない）。対象の熟語には、`<b>`と`<ruby>`を両方使う
  （`<b><ruby>熟語<rt>じゅくご</rt></ruby></b>`のように、`<b>`が外側）。1文字ずつ分解せず、
  熟語はまとめて1つの`<ruby>`にする（例：`<ruby>宿泊<rt>しゅくはく</rt></ruby>`であって
  `<ruby>宿<rt>しゅく</rt></ruby><ruby>泊<rt>はく</rt></ruby>`ではない）。
- `strokes` は必ず正しい画数を入れる（書き取り練習のラベルとGIFの説明に使う）。
- `quiz` は**約20問**（10字 × 音読み1問・訓読み1問が目安）。読み方を答える形式。`a` はひらがな。`alt` にはよくある間違い（あれば）。`note` に音読み/訓読みの短い説明。
- 各字について、音読みの熟語を使った問題と、訓読みの語を使った問題を最低1問ずつ入れる（両方の読み方が練習できるように）。音読み・訓読みが片方しかない字は、その字から2問（違う単語）出す。
- クイズの問題は、その日の単語リストや例文から出す。同じ字の2問が単語リストの並び順で連続しないよう、字ごとに散らして並べる（同じ字の問題が続くと答えを覚えてしまうため）。

### 5. ページを作る

```bash
conda run -n kanji python ~/.claude/skills/kanji-practice/build_page.py /tmp/kanji_content.json
```

これが自動でやること:

- KanjiVGのSVGを取得し、`kanji_gif.py` で筆順GIFを作る（`~/Documents/Claude-JP/漢字/gif/` にキャッシュ）
- GIFをbase64でHTMLに埋め込む（オフラインでも動く）
- 解答は必ず `<details>` の中に入れる（`open` 属性なし）
- クイズの入力欄・答え合わせ、書き取り練習のマス目、GIFの速さスライダーと「もう一度見る」を組み込む
- ページ右側に、テーマに関係なく何でも質問できるチャットのサイドバーを組み込む（`chatbox_html()`）。
  ローカルの質問サーバー（`ask_server.py`、`http://127.0.0.1:8765`、launchd常駐）に毎回fetchし、
  `claude -p --resume` でそのページを開いている間は会話が続く。サーバーに繋がらない場合だけ、
  質問文をクリップボードにコピーするフォールバックになる。
- 右下に「まとめて」ボタンを組み込む（`summarize_button_html()`）。押すと、今日のcontent JSON・
  クイズの入力欄の解答・チャットの会話をローカルサーバー（`/summarize`）に送り、Claudeが
  `~/Documents/Claude-JP/漢字/まとめ_<date>.html` を**別ファイルとして**書く
  （クイズの採点、チャットで聞いた語彙などの質問の振り返りも含める）。
- 内容JSONのコピーを `~/Documents/Claude-JP/漢字/content_<date>.json` に自動保存し、
  `kanji_history.json` に今日の字・テーマを自動追記する（履歴の更新はスクリプトが自動でやるので、
  手作業でこのファイルを編集する必要はない）

出力先は `~/Documents/Claude-JP/漢字/漢字練習_<date>.html`。

オプション:
- `--size 240`（GIFの大きさ）、`--skip-gif`（テスト用）、`--out <path>`
- **同じ日にもう1クラス追加で頼まれたとき** … その日の `漢字練習_<date>.html` が既にあるかを確認し、
  あれば新しいファイルを作るのではなく、その日のページに**合流**させる：
  1. 新しいクラスの分だけ（テーマ・字10個・単語・クイズ）を通常通りJSONに書く（`date` は同じ日付のまま）。
  2. `--kind extra --append` を付けて実行する：
     ```bash
     conda run -n kanji python ~/.claude/skills/kanji-practice/build_page.py /tmp/kanji_content.json --kind extra --append
     ```
  3. 既存の `content_<date>.json` があれば自動でそこに合流し（字は重複除去、クイズは追加、テーマは
     「Aテーマ＋Bテーマ」のように連結）、同じ `漢字練習_<date>.html` を上書きする。既存ファイルが
     なければ通常の新規作成と同じ。**別ファイル（`_2`など）は作らない。**
  4. 履歴（`kanji_history.json`）もこの日1件のまま正しく更新される（重複警告は出ない）。

### 6. 金曜日は週次復習も作る

その日が金曜日なら、日々のページに加えて、その週（月〜日）にやった全クラス分を1ページにまとめた復習ページも作る。
これは新しいコンテンツを考える必要がない**機械的な集計**なので、そのまま実行するだけでよい：

```bash
conda run -n kanji python ~/.claude/skills/kanji-practice/build_review.py
```

`kanji_history.json` の `days` からその週の記録（`kind: daily`/`extra`、`review`は除く）を集め、
各日の `content_<date>.json` を読み込んで、字（重複除去）とクイズを全部まとめた1ページを
`~/Documents/Claude-JP/漢字/復習_<date>.html` に作る。今週分の記録がなければ何も作らずに終わる
（エラーではない）。

### 7. 報告する

**チャットには読み方やクイズの解答を書かない。** 作ったHTMLのパスと、「今日のテーマは食べ物です（N4 3字／N3 5字／N2 2字）」程度の1〜2行だけ。金曜日に復習ページも作った場合はそのパスも一言添える。

コマンドの出力にある `GIF x/10` を必ず確認する。10未満、または stderr に「警告」が出ていたら、GIFが埋め込まれずページに「GIFを作れませんでした」というプレースホルダーが残っている状態なので、成功として報告しない。原因（ネットワークやconda環境）を確認し、直してから再実行する。
「履歴上すでに使用済みの字」という警告が出た場合は、選定ミスで既出の字を使ってしまったということなので、
Pedroにそのまま黙って進めず、次回から気をつける（同じ字を二度と選ばない）。

## 注意

- 筆順データはKanjiVG（CC BY-SA 3.0）。出典表記はページのフッターに入る。
- `kanji_gif.py`はこのスキルフォルダ自身にある（`~/.claude/skills/kanji-practice/kanji_gif.py`）。
  場所が違うときは `--maker <path>`。SVGのダウンロードキャッシュも
  `~/.claude/skills/kanji-practice/.kanjivg_cache/` に置く（`--cache-dir`で変更可）。
- `svgpathtools`, `Pillow`, `requests` が必要（conda環境 `kanji` に入っている）。
- 平日（月〜金）10:00に launchd（`com.pedro.kanji-daily`）が自動実行する。Pedroが日中に追加のクラスを
  頼んできたときは、上記の `--kind extra --append` の手順でその日のページに合流させればよい
  （自動実行や翌日以降のスケジュールを妨げない）。
- 自動実行は `claude -p "/kanji-practice" --permission-mode acceptEdits`（`run_daily.sh`）で行われる。
  カレントディレクトリはこのスキルフォルダ自身（`~/.claude/skills/kanji-practice/`）にしてあり、
  そこにある `.claude/settings.json` で、jlptsensei.comへのWebFetchや`build_page.py`/`build_review.py`
  の実行、`~/Documents/Claude-JP/`配下への書き込みが事前許可されている
  （出力先自体は変わらず `~/Documents/Claude-JP/漢字/` のまま）。
- ページ内のチャット・まとめ機能は `ask_server.py`（`com.pedro.kanji-ask-server`、launchdで常駐、
  `127.0.0.1:8765`）が動いていないと使えない。`launchctl list | grep kanji-ask-server` で起動確認、
  ログは `~/Documents/Claude-JP/漢字/ask_server.log`。サーバー内部の`claude -p`呼び出しも
  同じスキルフォルダの`.claude/settings.json`の許可を使うため、質問の内容によっては
  （未許可のツールを使おうとした場合など）応答がタイムアウトすることがある。
- インタラクティブに`claude`を起動して手動でこのスキルを試すときも、同じ権限を自動適用させたいなら
  `~/Documents/Claude-JP`ではなく`~/.claude/skills/kanji-practice`から起動するとよい
  （`.claude/settings.json`はカレントディレクトリ基準で読み込まれるため）。
