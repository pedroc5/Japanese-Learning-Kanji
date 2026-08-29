#!/usr/bin/env python3
"""Smoke tests for the page builder — no network, no conda.

    python3 test_build_page.py

Deliberately plain unittest against the stdlib so it runs with whatever python
is on PATH, not just the `kanji` conda environment: the point is to be cheap
enough to run before every commit.

What these actually guard is the part of the page that fails silently. A broken
inline <script> still renders a page that looks completely normal — the quiz
just stops grading — so the tests parse the emitted JS data back out and check
it, rather than only checking that some HTML was produced.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_page  # noqa: E402
import build_review  # noqa: E402
import tts  # noqa: E402


def sample_content() -> dict:
    """Two kanji and three questions, including the characters that used to
    break the page: a double quote and a literal closing script tag."""
    return {
        "date": "2026-08-02",
        "theme": "天気",
        "kanji": [
            {"char": "雷", "level": "N1", "on": "ライ", "kun": "かみなり",
             "meaning": "thunder", "strokes": "13", "radical": "雨",
             "words": [{"w": "雷雨", "r": "らいう", "e": "thunderstorm",
                        "m": "<ruby>雷<rt>かみなり</rt></ruby>をともなう<ruby>雨<rt>あめ</rt></ruby>"},
                       {"w": "雷が鳴る", "r": "かみなりがなる", "m": "音がする", "e": "to thunder"},
                       {"w": "落雷", "r": "らくらい", "m": "かみなりが落ちること",
                        "e": "lightning strike"},
                       {"w": "雷鳴", "r": "らいめい", "m": "かみなりの音", "e": "thunderclap"}],
             "examples": ["<ruby>雷<rt>かみなり</rt></ruby>が<b>鳴</b>る。"]},
            {"char": "虹", "level": "N1", "on": "コウ", "kun": "にじ",
             "meaning": "rainbow", "strokes": "9", "radical": "虫",
             "words": [{"w": "虹", "r": "にじ", "m": "rainbow"}],
             "examples": ["<b><ruby>虹<rt>にじ</rt></ruby></b>が出た。"]},
        ],
        "quiz": [
            {"q": "<b><ruby>雷雨<rt>らいう</rt></ruby></b>になる。", "a": "らいう",
             "alt": ["らいあめ"], "note": '「"雷"」は音読みで「ライ」。'},
            {"q": "<b><ruby>虹<rt>にじ</rt></ruby></b>が出た。", "a": "にじ",
             "alt": [], "note": "訓読みです。</script> と書いても壊れないこと。"},
            {"q": "どの字でもない問題。", "a": "なし", "alt": [], "note": ""},
        ],
    }


SAMPLE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 109 109">
  <g id="kvg:StrokePaths_096f7" style="fill:none;stroke:#000000;stroke-width:3">
    <path id="a" d="M10,10 L90,10"/>
    <path id="b" d="M50,20 L50,95"/>
    <path id="c" d="M20,60 L80,60"/>
  </g>
  <g id="kvg:StrokeNumbers_096f7">
    <text transform="matrix(1 0 0 1 6 8)">1</text>
    <text transform="matrix(1 0 0 1 44 18)">2</text>
    <text transform="matrix(1 0 0 1 16 58)">3</text>
  </g>
</svg>"""

SAMPLE_STROKES = {
    "box": [0.0, 0.0, 109.0, 109.0],
    "width": 3.0,
    "d": ["M10,10 L90,10", "M50,20 L50,95", "M20,60 L80,60"],
    "labels": [[6.0, 8.0], [44.0, 18.0], [16.0, 58.0]],
}


def quiz_key(page: str) -> list[dict]:
    """Pull window.__quizKey's array back out of the page and parse it.

    quiz_section() emits it as strict JSON precisely so this is possible. The
    indentation is whatever depth the section landed at, so don't pin it.
    """
    match = re.search(r"var data = \[\n(.*?)\n\s*\];", page, re.S)
    assert match, "answer key block not found"
    return json.loads("[" + match.group(1).replace("<\\/", "</") + "]")


def markup(page: str) -> str:
    """The page without its trailing script blocks.

    The inline scripts talk *about* tags in their comments, so assertions about
    what the document contains have to stop before them.
    """
    return page.split(build_page.banner("スクリプト"), 1)[0]


class TagBalance(HTMLParser):
    """Just enough parsing to catch an unclosed element in the emitted markup."""

    VOID = {"meta", "br", "img", "input", "hr", "link", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"</{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes <{self.stack[-1]}>")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


class BuildPageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.content = sample_content()
        self.strokes = {"雷": SAMPLE_STROKES, "虹": None}
        self.page = build_page.build(self.content, self.strokes,
                                     "/tmp/content_2026-08-02.json", "daily:2026-08-02")

    # --- the failure that motivated the suite ---------------------------- #

    def test_quotes_in_a_note_do_not_break_the_answer_key(self):
        """A " in a note used to end the JS string and kill the whole quiz."""
        key = quiz_key(self.page)
        self.assertEqual(len(key), 3)
        self.assertIn('"雷"', key[0]["exp"])
        self.assertEqual(key[0]["ok"], ["らいう"])
        self.assertEqual(key[0]["alt"], ["らいあめ"])

    def test_closing_script_tag_in_a_note_stays_inside_the_script(self):
        """A literal </script> in data would otherwise end the element early."""
        body = self.page.split("<h2>復習クイズ", 1)[1]
        script = body.split("<script>", 1)[1].split("</script>", 1)[0]
        self.assertIn("window.__quizKey", script)
        self.assertIn("addEventListener", script)   # 早期終了していたら消える

    # --- structure ------------------------------------------------------- #

    def test_markup_is_balanced(self):
        parser = TagBalance()
        parser.feed(self.page)
        self.assertEqual(parser.errors, [])
        self.assertEqual(parser.stack, [])

    def test_every_section_is_foldable_and_open(self):
        # 2 kanji（各字に書き取り練習を内蔵） + 復習クイズ
        self.assertEqual(self.page.count('<details class="sec" open>'), 3)
        self.assertNotIn('<details class="sec">', self.page)

    def test_answer_list_stays_collapsed(self):
        answers = self.page.split("<summary>解答を表示</summary>", 1)[0]
        self.assertTrue(answers.rstrip().endswith("<details>"))

    # --- stroke-order SVG ------------------------------------------------ #

    def test_strokes_are_inline_svg_not_a_raster(self):
        self.assertNotIn("<img", markup(self.page))
        self.assertNotIn("data:image/gif", self.page)
        self.assertIn('<svg class="strokes" viewBox="0 0 109 109"', self.page)
        # 虹 has no data, so it gets a placeholder instead of an empty drawing
        self.assertIn('<div class="player"><div class="wait">', self.page)

    def test_every_stroke_is_drawn_and_numbered(self):
        svg = self.page.split('<svg class="strokes"', 1)[1].split("</svg>", 1)[0]
        self.assertEqual(svg.count('<path pathLength="1"'), 3)   # ink
        self.assertEqual(svg.count("<path d="), 3)               # ghost
        self.assertEqual(svg.count("<text "), 3)                 # numbers

    def test_the_drawing_is_complete_without_javascript(self):
        """No dash offsets in the markup: html.js is what hides the strokes."""
        svg = self.page.split('<svg class="strokes"', 1)[1].split("</svg>", 1)[0]
        self.assertNotIn("stroke-dashoffset", svg)
        self.assertIn("html.js .strokes .ink path{stroke-dasharray:1", self.page)

    def test_the_hidden_dash_gap_outruns_the_path(self):
        """A gap of 1 makes the pattern repeat at the path's end, and the
        zero-length dash there is painted as a dot by round linecaps — a
        coloured pip on every stroke that hasn't been drawn yet."""
        rule = re.search(r"html\.js \.strokes \.ink path\{stroke-dasharray:([^;]+);", self.page)
        dash, gap = (float(v) for v in rule.group(1).split())
        self.assertEqual(dash, 1)          # pathLength="1" なので画の全長
        self.assertGreater(gap, dash)

    def test_numbers_are_dropped_when_kanjivg_has_no_anchors(self):
        data = dict(SAMPLE_STROKES, labels=[])
        page = build_page.build(self.content, {"雷": data, "虹": None})
        svg = page.split('<svg class="strokes"', 1)[1].split("</svg>", 1)[0]
        self.assertNotIn('<g class="nums">', svg)

    # --- KanjiVG parsing -------------------------------------------------- #

    def test_parse_strokes_reads_paths_labels_and_box(self):
        data = build_page.parse_strokes(SAMPLE_SVG)
        self.assertEqual(data["box"], [0.0, 0.0, 109.0, 109.0])
        self.assertEqual(data["d"], ["M10,10 L90,10", "M50,20 L50,95", "M20,60 L80,60"])
        self.assertEqual(data["labels"], [[6.0, 8.0], [44.0, 18.0], [16.0, 58.0]])
        self.assertEqual(data["width"], 3.0)

    def test_mismatched_label_count_is_discarded(self):
        """A partial StrokeNumbers group would mis-number every stroke after it."""
        svg = SAMPLE_SVG.replace('<text transform="matrix(1 0 0 1 16 58)">3</text>', "")
        self.assertEqual(build_page.parse_strokes(svg)["labels"], [])

    def test_a_file_with_no_paths_is_rejected(self):
        with self.assertRaises(ValueError):
            build_page.parse_strokes('<svg xmlns="http://www.w3.org/2000/svg"></svg>')

    # --- drafts ----------------------------------------------------------- #

    def test_page_id_scopes_the_saved_drafts(self):
        self.assertIn('data-page-id="daily:2026-08-02"', self.page)
        review = build_page.build(self.content, {}, page_id="review:2026-08-02")
        self.assertIn('data-page-id="review:2026-08-02"', review)

    def test_store_is_defined_before_the_quiz_script_uses_it(self):
        self.assertLess(self.page.index("window.__kanjiStore ="),
                        self.page.index("var store = window.__kanjiStore;"))


class WordFuriganaTest(unittest.TestCase):
    """The ふりがな toggle only reaches what carries <ruby>, so the vocabulary
    table has to build its own from the 読み column."""

    def test_okurigana_stays_out_of_the_furigana(self):
        self.assertEqual(build_page.word_ruby("泊まる", "と(まる)"),
                         "<ruby>泊<rt>と</rt></ruby>まる")
        self.assertEqual(build_page.word_ruby("素泊まり", "すどまり"),
                         "<ruby>素泊<rt>すど</rt></ruby>まり")
        self.assertEqual(build_page.word_ruby("話し合う", "はな(し)あ(う)"),
                         "<ruby>話<rt>はな</rt></ruby>し<ruby>合<rt>あ</rt></ruby>う")

    def test_leading_kana_and_katakana_anchor_the_split(self):
        self.assertEqual(build_page.word_ruby("お花見", "おはなみ"),
                         "お<ruby>花見<rt>はなみ</rt></ruby>")
        self.assertEqual(build_page.word_ruby("生ビール", "なまびーる"),
                         "<ruby>生<rt>なま</rt></ruby>ビール")

    def test_a_word_with_no_kanji_gets_no_ruby(self):
        self.assertEqual(build_page.word_ruby("コーヒー", "こーひー"), "コーヒー")

    def test_a_reading_that_does_not_line_up_falls_back_to_one_ruby(self):
        # ふたつの読みを併記した欄。分割はあきらめて語全体に振る。
        self.assertEqual(build_page.word_ruby("泊まる", "と(まる)・は(く)"),
                         "<ruby>泊まる<rt>とまる・はく</rt></ruby>")

    def test_the_table_carries_furigana_in_both_japanese_columns(self):
        table = build_page.word_table_html(sample_content()["kanji"][0]["words"])
        self.assertIn('<td class="word"><ruby>雷雨<rt>らいう</rt></ruby></td>', table)
        self.assertIn("<ruby>雷<rt>かみなり</rt></ruby>をともなう", table)   # 意味の欄
        self.assertIn("<td>らいう</td>", table)                            # 読みの欄はそのまま

    def test_plain_meanings_are_still_escaped(self):
        table = build_page.word_table_html([{"w": "雷", "r": "かみなり", "m": "a < b"}])
        self.assertIn("a &lt; b", table)


class ProseFuriganaTest(unittest.TestCase):
    """意味・書き順・クイズの解説にも、モデルはときどき<ruby>を書いてくる。
    エスケープするとタグが画面にそのまま出てしまうので、通す。"""

    def section(self, **extra) -> str:
        kanji = dict(sample_content()["kanji"][0], **extra)
        return build_page.kanji_section(1, kanji, None)

    def test_furigana_in_the_meaning_line_is_rendered(self):
        section = self.section(meaning="かみなり ／ <ruby>雷<rt>かみなり</rt></ruby>")
        self.assertIn("<ruby>雷<rt>かみなり</rt></ruby>", section)

    def test_furigana_in_the_stroke_order_note_is_rendered(self):
        section = self.section(order_note="<ruby>最後<rt>さいご</rt></ruby>のたて画。")
        self.assertIn("書き順：<ruby>最後<rt>さいご</rt></ruby>のたて画。", section)

    def test_plain_prose_is_still_escaped(self):
        self.assertIn("a &lt; b", self.section(meaning="a < b"))
        self.assertIn("a &lt; b", self.section(order_note="a < b"))

    def test_furigana_in_a_quiz_note_reaches_both_the_list_and_the_key(self):
        note = "<ruby>音<rt>おん</rt></ruby>読みです。"
        section = build_page.quiz_section(
            [{"q": "問題", "a": "らいう", "alt": [], "note": note}], ["雷"])
        self.assertIn(f"（{note}）", section)          # 答えのリスト
        self.assertEqual(quiz_key(section)[0]["exp"], note)            # JSの解説

    def test_a_plain_quiz_note_is_still_escaped(self):
        section = build_page.quiz_section(
            [{"q": "問題", "a": "にじ", "alt": [], "note": "a < b"}], ["虹"])
        self.assertNotIn("a < b", section)
        self.assertIn("a &lt; b", section)


class ExampleReuseTest(unittest.TestCase):
    """クイズは同じ単語の別の文で出す — 例文の使い回しは警告する。"""

    def content(self, question: str) -> dict:
        return {
            "kanji": [{"char": "泊", "examples": [
                "<ruby>京都<rt>きょうと</rt></ruby>に<b>二泊三日</b>で"
                "<ruby>旅行<rt>りょこう</rt></ruby>しました。"]}],
            "quiz": [{"q": question}],
        }

    def test_the_same_sentence_in_different_markup_is_caught(self):
        recycled = build_page.reused_examples(
            self.content("<ruby>京都<rt>きょうと</rt></ruby>に"
                         "<b><ruby>二泊三日<rt>にはくみっか</rt></ruby></b>で旅行しました"))
        self.assertEqual(recycled, ["京都に二泊三日で旅行しました"])

    def test_an_example_trimmed_to_its_first_clause_is_caught(self):
        self.assertTrue(build_page.reused_examples(self.content("京都に二泊三日で旅行")))

    def test_a_new_sentence_with_the_same_word_is_fine(self):
        self.assertEqual(
            build_page.reused_examples(self.content("<b>二泊三日</b>の出張から帰ってきた。")), [])


class WeekFolderTest(unittest.TestCase):
    """Everything a week produces lands in one folder named after that week."""

    def test_a_week_runs_monday_to_sunday(self):
        for day in ["2026-08-03", "2026-08-05", "2026-08-09"]:      # 月・水・日
            self.assertEqual(build_page.week_folder(day), "2026-08-03〜08-09")
        self.assertEqual(build_page.week_folder("2026-08-02"), "2026-07-27〜08-02")
        self.assertEqual(build_page.week_folder("2026-08-10"), "2026-08-10〜08-16")

    def test_folder_names_sort_chronologically(self):
        names = [build_page.week_folder(d) for d in
                 ["2026-12-28", "2027-01-04", "2026-08-31"]]
        self.assertEqual(sorted(names), ["2026-08-31〜09-06", "2026-12-28〜01-03",
                                         "2027-01-04〜01-10"])

    def test_the_content_json_is_named_after_its_page_but_kept_out_of_documents(self):
        """The page goes to ~/Documents; its content JSON does not.

        launchd (the Friday review, the chat server) can create files under
        ~/Documents but cannot read them back, so the JSON those two have to
        re-read lives in the skill folder instead — named after the page.
        """
        page = build_page.day_page_path("2026-08-05", Path("/tmp/漢字"))
        self.assertEqual(page, Path("/tmp/漢字/2026-08-03〜08-09/漢字練習_2026-08-05.html"))
        self.assertEqual(build_page.content_json_path(page).name, "content_2026-08-05.json")
        self.assertEqual(build_page.content_json_path(page).parent,
                         build_page.DEFAULT_CONTENT_DIR)
        self.assertNotIn("Documents", str(build_page.content_json_path(page)))

    def test_a_caller_can_still_redirect_the_content_json(self):
        page = build_page.day_page_path("2026-08-05", Path("/tmp/漢字"))
        self.assertEqual(build_page.content_json_path(page, Path("/tmp/elsewhere")),
                         Path("/tmp/elsewhere/content_2026-08-05.json"))


class WeeklyReviewTest(unittest.TestCase):
    """The Friday page is a summary table plus exercises — not a second copy of
    the week's daily pages."""

    def setUp(self) -> None:
        self.content = sample_content()
        self.kanji = self.content["kanji"]
        self.history = {"kanji": {"雷": {"quiz": {"asked": 6, "wrong": 2, "last": "x"}}},
                        "days": []}

    def review_page(self) -> str:
        quiz = build_review.exercises(self.kanji, shown=1)
        sections = [build_page.collapsible("<h2>今週習った漢字</h2>",
                                           build_review.summary_table(self.kanji, self.history)),
                    build_page.quiz_section(quiz, [q["char"] for q in quiz],
                                            heading="今週の練習問題")]
        return build_page.build(self.content, {}, sections=sections,
                                heading="漢字の復習", period="今週")

    def test_the_table_has_one_row_per_kanji(self):
        table = build_review.summary_table(self.kanji, self.history)
        self.assertEqual(table.count("<tr>"), len(self.kanji) + 1)      # +見出し
        self.assertIn('<td class="ch"><b>雷</b><span class="lvl n1">N1</span>', table)
        self.assertIn("音 ライ<br>訓 かみなり", table)
        self.assertIn("<ruby>雷雨<rt>らいう</rt></ruby>", table)          # よく使う語

    def test_a_missed_kanji_is_flagged_in_the_table(self):
        table = build_review.summary_table(self.kanji, self.history)
        self.assertIn('<span class="miss">✕2／6問</span>', table)
        self.assertEqual(table.count('class="miss"'), 1)                # 虹は無記録

    # --- 出題する語の選び方 ------------------------------------------------ #

    def test_the_table_words_are_not_the_ones_asked_about(self):
        """表に読み付きで出ている語を問題にすると、答えを写すだけになる。"""
        entry = {"char": "笑", "meaning": "わらう", "words": [
            {"w": "笑顔", "r": "えがお"}, {"w": "微笑", "r": "びしょう"},
            {"w": "苦笑", "r": "くしょう"}, {"w": "爆笑", "r": "ばくしょう"},
            {"w": "談笑", "r": "だんしょう"}, {"w": "笑う", "r": "わら(う)"},
            {"w": "笑い声", "r": "わら(いごえ)"}]}
        shown = build_review.shown_words([entry])                # 先頭4語
        reading, writing = build_review.pick_words(entry, shown)
        for word in reading + [writing]:
            self.assertNotIn(word["w"], shown)

    def test_a_kanji_asked_in_both_reading_styles(self):
        entry = {"char": "泊", "words": [
            {"w": "宿泊", "r": "しゅくはく"}, {"w": "一泊", "r": "いっぱく"},
            {"w": "泊まる", "r": "と(まる)"}, {"w": "泊める", "r": "と(める)"}]}
        reading, _ = build_review.pick_words(entry, set())
        self.assertEqual([word["w"] for word in reading], ["宿泊", "泊まる"])

    def test_a_word_with_two_readings_is_never_asked_on_its_own(self):
        """「怒る」だけ見せられても、おこる か いかる か決められない。"""
        entry = {"char": "怒", "words": [
            {"w": "怒る", "r": "おこ(る)"}, {"w": "怒る", "r": "いか(る)"},
            {"w": "怒号", "r": "どごう"}, {"w": "怒り出す", "r": "おこ(りだす)"}]}
        reading, writing = build_review.pick_words(entry, set())
        self.assertEqual([word["w"] for word in reading], ["怒号", "怒り出す"])
        self.assertIsNone(writing)          # 残りは「怒る」だけなので出題しない

    def test_a_short_word_list_falls_back_to_the_table_words(self):
        """語が少ない字は、問題が消えるより表と重なるほうがまし。"""
        entry = {"char": "虹", "words": [{"w": "虹", "r": "にじ"}]}
        reading, writing = build_review.pick_words(entry, {"虹"})
        self.assertEqual([word["w"] for word in reading], ["虹"])
        self.assertIsNone(writing)

    # --- 問題そのもの ------------------------------------------------------ #

    def test_the_questions_are_new_ones_not_the_daily_quiz(self):
        """日々のページのクイズ文は使わない（もう一度答えても記憶を試すだけ）。"""
        quiz = build_review.exercises(self.kanji, shown=1)
        daily = [build_page.plain_sentence(q["q"]) for q in self.content["quiz"]]
        for question in quiz:
            self.assertNotIn(build_page.plain_sentence(question["q"]), daily)

    def test_the_hint_naming_the_kanji_starts_folded(self):
        entry = {"char": "泊", "meaning": "とまる",
                 "words": [{"w": "宿泊", "r": "しゅくはく", "m": "とまること"}]}
        question = build_review.writing_question(entry, entry["words"][0])
        self.assertIn('<details class="hint"><summary>ヒント</summary>'
                      "「泊」を使います。</details>", question["q"])
        self.assertNotIn("open", question["q"])
        # ヒントを開くまで、どの字かは問題文に出ていない
        self.assertNotIn("泊", question["q"].split("<details", 1)[0])
        self.assertEqual(question["ph"], "漢字で入力")

    def test_a_meaning_that_gives_the_answer_away_is_replaced_by_the_english(self):
        word = {"w": "宿泊", "r": "しゅくはく", "m": "宿泊すること", "e": "lodging"}
        question = build_review.writing_question({"char": "泊"}, word)
        self.assertIn("lodging", question["q"])
        self.assertNotIn("宿泊", question["q"])

    def test_each_kanji_is_read_before_it_is_written(self):
        quiz = build_review.exercises(self.kanji, shown=1)
        self.assertEqual([q["char"] for q in quiz], ["雷", "雷", "雷", "虹"])
        self.assertEqual([("ph" in q) for q in quiz], [False, False, True, False])
        self.assertEqual([q["a"] for q in quiz], ["らくらい", "かみなりがなる", "雷鳴", "にじ"])
        self.assertTrue(quiz[0]["q"].startswith('<span class="qtag" data-nospeak>読み</span>'))

    # --- ページ全体 -------------------------------------------------------- #

    def test_the_page_drops_the_strokes_and_the_tracing_boxes(self):
        page = self.review_page()
        body = markup(page)
        self.assertNotIn('<svg class="strokes"', body)
        self.assertNotIn("practice-block", body)
        self.assertNotIn("KanjiVG", body.split('<p class="foot">')[1])   # 使っていない
        self.assertEqual(re.findall(r"<h2>.*?</h2>", body),
                         ["<h2>今週習った漢字</h2>", "<h2>今週の練習問題</h2>"])

    def test_the_page_keeps_the_shared_furniture(self):
        page = self.review_page()
        for piece in ["furiganaToggle", "window.__kanjiStore", "window.__quizKey",
                      'id="check"', "chatbox", "まとめて"]:
            self.assertIn(piece, page)
        parser = TagBalance()
        parser.feed(page)
        self.assertEqual((parser.errors, parser.stack), ([], []))

    def test_the_exercises_are_gradable(self):
        key = quiz_key(self.review_page())
        self.assertEqual([entry["ok"][0] for entry in key],
                         ["らくらい", "かみなりがなる", "雷鳴", "にじ"])
        self.assertEqual([entry["char"] for entry in key], ["雷", "雷", "雷", "虹"])


class QuizHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.history = {"kanji": {"雷": {"date": "2026-08-02", "level": "N1"},
                                  "虹": {"date": "2026-08-02", "level": "N1"}},
                        "days": []}

    def test_only_attempted_questions_are_counted(self):
        build_page.record_quiz_results(self.history, [
            {"char": "雷", "verdict": "正解"},
            {"char": "雷", "verdict": "不正解"},
            {"char": "虹", "verdict": "未回答"},
        ], "2026-08-03")
        self.assertEqual(self.history["kanji"]["雷"]["quiz"],
                         {"asked": 2, "wrong": 1, "last": "2026-08-03"})
        self.assertNotIn("quiz", self.history["kanji"]["虹"])

    def test_a_near_miss_counts_as_wrong(self):
        build_page.record_quiz_results(
            self.history, [{"char": "雷", "verdict": "惜しい"}], "2026-08-03")
        self.assertEqual(self.history["kanji"]["雷"]["quiz"]["wrong"], 1)

    def test_unknown_kanji_are_not_invented(self):
        build_page.record_quiz_results(
            self.history, [{"char": "猫", "verdict": "不正解"}], "2026-08-03")
        self.assertNotIn("猫", self.history["kanji"])

    def test_review_order_is_missed_then_untested_then_known(self):
        self.history["kanji"] = {
            "全問正解": {"quiz": {"asked": 4, "wrong": 0, "last": "x"}},
            "未出題": {},
            "一問落とし": {"quiz": {"asked": 4, "wrong": 1, "last": "x"}},
            "三問落とし": {"quiz": {"asked": 4, "wrong": 3, "last": "x"}},
        }
        order = sorted(self.history["kanji"],
                       key=lambda c: build_page.quiz_priority(self.history, c))
        self.assertEqual(order, ["三問落とし", "一問落とし", "未出題", "全問正解"])

    def test_pending_files_are_applied_then_removed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "2026-08-02.json"
            results.write_text(json.dumps(
                {"date": "2026-08-02", "results": [{"char": "雷", "verdict": "不正解"}]},
                ensure_ascii=False), encoding="utf-8")
            applied = build_page.ingest_quiz_results(self.history, Path(tmp))
            self.assertEqual(applied, 1)
            self.assertFalse(results.exists())        # 二重計上を防ぐため消す

    def test_an_unreadable_file_is_kept_not_dropped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "broken.json"
            bad.write_text("{not json", encoding="utf-8")
            build_page.ingest_quiz_results(self.history, Path(tmp))
            self.assertTrue(bad.exists())


class SummaryTargetTest(unittest.TestCase):
    """まとめ_<date>.html goes beside the day's page, not beside the content JSON.

    These were the same folder until the content JSON moved to the skill folder
    to get out of TCC's way; deriving the まとめ path from the content file then
    silently started writing it into the skill folder instead of ~/Documents.
    """

    def test_the_summary_sits_beside_the_day_page(self):
        import ask_server
        content = str(build_page.DEFAULT_CONTENT_DIR / "content_2026-08-12.json")
        day, target = ask_server.summary_target(content)
        self.assertEqual(day, "2026-08-12")
        self.assertEqual(Path(target).parent,
                         build_page.day_page_path("2026-08-12").parent)
        self.assertEqual(Path(target).name, "まとめ_2026-08-12.html")

    def test_the_summary_does_not_follow_the_content_json(self):
        import ask_server
        content = str(build_page.DEFAULT_CONTENT_DIR / "content_2026-08-12.json")
        _, target = ask_server.summary_target(content)
        self.assertNotEqual(Path(target).parent, build_page.DEFAULT_CONTENT_DIR)
        self.assertIn("Documents", target)

    def test_an_undated_name_falls_back_to_today(self):
        import ask_server
        from datetime import date
        day, target = ask_server.summary_target("/somewhere/content_notadate.json")
        self.assertEqual(day, date.today().isoformat())
        self.assertIn(f"まとめ_{date.today().isoformat()}.html", target)


class SpeechTextTest(unittest.TestCase):
    """What actually gets read aloud. Getting this wrong is expensive twice
    over: a bad clip is wrong in Pedro's ear and already paid for."""

    def test_furigana_is_not_read_a_second_time(self):
        spoken = build_page.speech_text(
            "<b><ruby>宿泊<rt>しゅくはく</rt></ruby></b>する。")
        self.assertEqual(spoken, "宿泊する。")

    def test_punctuation_survives(self):
        """plain_sentence() drops 。and 、 to compare sentences; the voice needs
        them to phrase one."""
        self.assertEqual(build_page.speech_text("行く、そして<b>泊</b>まる。"),
                         "行く、そして泊まる。")
        self.assertNotIn("。", build_page.plain_sentence("行く、そして泊まる。"))

    def test_a_folded_hint_is_never_spoken(self):
        """The weekly 書き question hides which kanji to use. Reading the hint
        out loud would hand over exactly what the fold withholds."""
        question = build_review.writing_question(
            {"char": "泊", "meaning": "とまる"},
            {"w": "宿泊", "r": "しゅくはく", "m": "とまること", "e": "lodging"})
        spoken = build_page.speech_text(question["q"])
        self.assertIn("しゅくはく", spoken)
        self.assertNotIn("泊", spoken)
        self.assertNotIn("ヒント", spoken)

    def test_the_question_label_is_not_read_aloud(self):
        """「読み」/「書き」 are badges on the exercise, not part of the sentence;
        a clip that opens by announcing them is noise."""
        question = build_review.reading_question(
            {"char": "雷", "meaning": "かみなり"},
            {"w": "落雷", "r": "らくらい", "m": "かみなりが落ちること", "e": "lightning strike"})
        self.assertIn("読み", question["q"])                       # 画面には出る
        self.assertTrue(build_page.speech_text(question["q"]).startswith("「落雷」"))

    def test_kana_mode_reads_the_furigana_instead_of_the_kanji(self):
        sentence = "<ruby>京都<rt>きょうと</rt></ruby>に<b><ruby>一泊<rt>いっぱく</rt></ruby></b>した。"
        self.assertEqual(build_page.speech_kana(sentence), "きょうとにいっぱくした。")
        self.assertEqual(build_page.speech_text(sentence), "京都に一泊した。")

    def test_an_empty_reading_keeps_the_word(self):
        """カタカナ語 are written <ruby>ホテル<rt></rt></ruby> in the content JSON."""
        self.assertEqual(build_page.speech_kana("<ruby>ホテル<rt></rt></ruby>に泊まる。"),
                         "ホテルに泊まる。")

    def test_both_modes_agree_on_the_lookup_key(self):
        """audio_button() keys on speech_text() whichever text was spoken, so a
        page built with --tts-text kana still finds its clips."""
        sentence = "<ruby>虹<rt>にじ</rt></ruby>が出た。"
        audio = {build_page.speech_text(sentence): {"src": "x_audio/abc.mp3"}}
        self.assertIn("abc.mp3", build_page.audio_button(sentence, audio))


class AudioButtonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.content = sample_content()
        self.audio = {build_page.speech_text(text):
                      {"src": f"漢字練習_2026-08-02_audio/{i:02d}.mp3",
                       "voice": "Riku" if i % 2 else "Sakura"}
                      for i, text in enumerate(build_page.page_sentences(self.content))}
        self.page = build_page.build(self.content, {"雷": SAMPLE_STROKES, "虹": None},
                                     page_id="daily:2026-08-02", audio=self.audio)

    def test_every_example_and_question_gets_a_button(self):
        self.assertEqual(markup(self.page).count('class="play"'),
                         len(build_page.page_sentences(self.content)))

    def test_the_button_takes_the_bullet_position(self):
        """The play button replaces the ・ rather than being added next to it,
        so a page with audio lines up exactly like one without."""
        example = self.content["kanji"][0]["examples"][0]
        section = build_page.kanji_section(1, self.content["kanji"][0],
                                           SAMPLE_STROKES, self.audio)
        self.assertIn(f'>▶</button>{example}', section)
        self.assertNotIn(f'<p class="ex">・{example}', section)

    def test_the_voice_name_is_shown_beside_the_button(self):
        """Four voices take turns through the page. The name is on the page, in
        front of the ▶ — not only in a tooltip, which Pedro can't read off."""
        section = build_page.kanji_section(1, self.content["kanji"][0],
                                           SAMPLE_STROKES, self.audio)
        self.assertIn('<i class="voice">(Sakura)</i><button', section)
        self.assertNotIn("title=", section)
        self.assertIn('aria-label="音声を聞く（Sakura）"', section)

    def test_a_sentence_without_a_clip_keeps_its_bullet(self):
        """One failed synthesis costs that sentence its button, nothing else."""
        partial = dict(list(self.audio.items())[1:])
        section = build_page.kanji_section(1, self.content["kanji"][0],
                                           SAMPLE_STROKES, partial)
        self.assertIn('<p class="ex">・', section)
        self.assertNotIn('class="play"', section)

    def test_a_page_without_audio_is_unchanged(self):
        silent = build_page.build(self.content, {"雷": SAMPLE_STROKES, "虹": None},
                                  page_id="daily:2026-08-02")
        self.assertNotIn('class="play"', markup(silent))
        self.assertIn('<p class="ex">・', silent)

    def test_the_markup_stays_balanced_with_buttons(self):
        parser = TagBalance()
        parser.feed(markup(self.page))
        self.assertEqual(parser.errors, [])

    def test_the_player_script_is_always_present(self):
        """One shared <audio>, not one per sentence — see AUDIO_JS."""
        self.assertIn("button.play", self.page)
        self.assertNotIn("<audio", markup(self.page))

    def test_the_weekly_exercises_are_playable(self):
        quiz = build_review.exercises(sample_content()["kanji"])
        audio = {build_page.speech_text(q["q"]): {"src": "復習_audio/a.mp3", "voice": "Riku"}
                 for q in quiz}
        section = build_page.quiz_section(quiz, [q["char"] for q in quiz], audio=audio)
        self.assertEqual(section.count('class="play"'), len(quiz))


class TTSCacheTest(unittest.TestCase):
    """The cache is what keeps a rerun free, so it has to key on everything
    that changes the audio and on nothing that doesn't."""

    def setUp(self) -> None:
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_the_key_covers_voice_model_format_and_language(self):
        base = tts.cache_key("こんにちは", "V1", "eleven_v3", "mp3_44100_64")
        self.assertNotEqual(base, tts.cache_key("こんばんは", "V1", "eleven_v3", "mp3_44100_64"))
        self.assertNotEqual(base, tts.cache_key("こんにちは", "V2", "eleven_v3", "mp3_44100_64"))
        self.assertNotEqual(base, tts.cache_key("こんにちは", "V1", "eleven_v2", "mp3_44100_64"))
        self.assertNotEqual(base, tts.cache_key("こんにちは", "V1", "eleven_v3", "mp3_22050_32"))
        self.assertNotEqual(base, tts.cache_key("こんにちは", "V1", "eleven_v3",
                                                "mp3_44100_64", "zh"))
        self.assertEqual(base, tts.cache_key("こんにちは", "V1", "eleven_v3", "mp3_44100_64"))

    def test_the_voices_own_settings_are_left_alone(self):
        """Omitting voice_settings makes the API use the settings stored on the
        voice — the ones elevenlabs.io plays it with. Sending our own generic
        pair overrode them, and that was audible."""
        sent = {}
        def capture(path, key, *, body=None, **kwargs):
            sent.update(body or {})
            return b"ID3"
        original, tts._request = tts._request, capture
        self.addCleanup(lambda: setattr(tts, "_request", original))
        tts.synthesize("文。", "V1", "key", cache_dir=self.dir)
        self.assertNotIn("voice_settings", sent)
        self.assertEqual(sent["language_code"], "ja")   # 日本語だと明示する

    def test_a_cached_sentence_never_reaches_the_network(self):
        name = tts.cache_key("ある文", "V1", "M", "F")
        (self.dir / f"{name}.mp3").write_bytes(b"ID3cached")
        def explode(*args, **kwargs):
            raise AssertionError("キャッシュがあるのにAPIを呼んだ")
        original, tts._request = tts._request, explode
        self.addCleanup(lambda: setattr(tts, "_request", original))
        clips, failed = tts.synthesize_all({"ある文": "V1"}, "key",
                                           model="M", fmt="F", cache_dir=self.dir)
        self.assertEqual(clips, {"ある文": b"ID3cached"})
        self.assertEqual(failed, [])

    def test_one_failure_does_not_lose_the_other_clips(self):
        calls = []
        def flaky(path, key, **kwargs):
            calls.append(path)
            if "だめ" in (kwargs.get("body") or {}).get("text", ""):
                raise tts.TTSError("422")
            return b"ID3ok"
        original, tts._request = tts._request, flaky
        self.addCleanup(lambda: setattr(tts, "_request", original))
        clips, failed = tts.synthesize_all({"よい文": "V1", "だめな文": "V1"}, "key",
                                           cache_dir=self.dir, workers=1)
        self.assertEqual(list(clips), ["よい文"])
        self.assertEqual(failed, ["だめな文"])

    def test_a_bad_key_stops_the_batch_instead_of_repeating(self):
        """A wrong key fails identically on every sentence. One page is ~120 of
        them, so the first authentication error has to end the run."""
        tried = []
        def unauthorized(path, key, **kwargs):
            tried.append(path)
            # 実際に返ってきたもの：無料プランはAPIからライブラリのボイスを使えない。
            raise tts.TTSError('HTTP 402: {"detail":{"code":"paid_plan_required"}}')
        original, tts._request = tts._request, unauthorized
        self.addCleanup(lambda: setattr(tts, "_request", original))
        clips, failed = tts.synthesize_all({f"文{i}。": "V1" for i in range(20)}, "bad",
                                           cache_dir=self.dir, workers=1)
        self.assertEqual(clips, {})
        self.assertEqual(len(failed), 20)          # 全文が音声なしとして扱われる
        self.assertLess(len(tried), 20)            # が、20回は呼ばない

    def test_a_known_voice_needs_no_lookup(self):
        """Riku と Sakura は id が分かっているので、9時のビルドがボイス検索の
        失敗でまるごと無音になることがない。"""
        def explode(*args, **kwargs):
            raise AssertionError("既知のボイスなのにAPIを呼んだ")
        original, tts._request = tts._request, explode
        self.addCleanup(lambda: setattr(tts, "_request", original))
        self.assertEqual(tts.resolve_voice("Riku", "key", self.dir),
                         tts.KNOWN_VOICES["riku"])
        self.assertEqual(tts.resolve_voice("sakura", "key", self.dir),
                         tts.KNOWN_VOICES["sakura"])
        self.assertEqual(tts.resolve_voice(tts.KNOWN_VOICES["riku"], "key", self.dir),
                         tts.KNOWN_VOICES["riku"])

    def test_clips_land_beside_the_page_with_an_encoded_url(self):
        """The week folder has Japanese in its name and the page is opened over
        file://, so the src has to be percent-encoded to resolve."""
        page = self.dir / "2026-08-03〜08-09" / "漢字練習_2026-08-05.html"
        urls = tts.write_clips({"文です。": b"ID3"}, tts.audio_dir_for(page),
                               {"文です。": ("Riku", "V1")}, "M", "F")
        src = urls["文です。"]["src"]
        self.assertNotIn("漢字", src)
        self.assertTrue(src.startswith("%"), src)
        self.assertTrue((page.parent / urllib.parse.unquote(src)).exists())

    def test_the_same_sentence_is_one_file(self):
        page = self.dir / "p.html"
        pairs = {"同じ文。": ("Riku", "V1")}
        urls = tts.write_clips({"同じ文。": b"a"}, tts.audio_dir_for(page), pairs, "M", "F")
        tts.write_clips({"同じ文。": b"a"}, tts.audio_dir_for(page), pairs, "M", "F")
        self.assertEqual(len(list(tts.audio_dir_for(page).glob("*.mp3"))), 1)
        self.assertIn(tts.cache_key("同じ文。", "V1", "M", "F")[:12], urls["同じ文。"]["src"])

    def test_the_voices_split_the_page_about_evenly(self):
        voices = [(name, name[0]) for name in ("Riku", "Sakura", "Kozy", "Shizuka")]
        texts = [f"これは{i}番目の文です。" for i in range(400)]
        picked = tts.assign_voices(texts, voices)
        for name, _ in voices:
            share = sum(1 for v in picked.values() if v[0] == name)
            self.assertGreater(share, 60, f"{name} が少なすぎる")
            self.assertLess(share, 140, f"{name} が多すぎる")

    def test_all_four_voices_are_known_without_a_lookup(self):
        """既定の4声。名前で引けないと、9時のビルドがボイス検索の失敗で無音になる。"""
        def explode(*args, **kwargs):
            raise AssertionError("既知のボイスなのにAPIを呼んだ")
        original, tts._request = tts._request, explode
        self.addCleanup(lambda: setattr(tts, "_request", original))
        names = [n.strip() for n in tts.DEFAULT_VOICE.split(",")]
        self.assertEqual(names, ["Riku", "Sakura", "Kozy", "Shizuka"])
        ids = {tts.resolve_voice(name, "key", self.dir) for name in names}
        self.assertEqual(len(ids), 4)                     # 4声とも別のID

    def test_the_same_sentence_always_gets_the_same_voice(self):
        """random() would reshuffle every rebuild, and a reshuffled page misses
        the cache on all 120 clips — i.e. pays for them again."""
        voices = [("Riku", "R"), ("Sakura", "S")]
        first = tts.assign_voices(["雨が降る。", "虹が出た。"], voices)
        second = tts.assign_voices(["虹が出た。", "雨が降る。"], voices)
        self.assertEqual(first, second)

    def test_one_voice_reads_everything(self):
        picked = tts.assign_voices(["あ。", "い。", "う。"], [("Riku", "R")])
        self.assertEqual({v[0] for v in picked.values()}, {"Riku"})

    def test_no_key_means_a_silent_page_not_a_failed_build(self):
        """9:00 with no key has to still produce a page."""
        urls, note = tts.speak_page(["文。"], self.dir / "p.html",
                                    key_file=self.dir / "missing", cache_dir=self.dir)
        self.assertEqual(urls, {})
        self.assertIn("音声なし", note)

    def test_the_key_file_wins_over_the_environment(self):
        import os
        key_file = self.dir / ".elevenlabs_key"
        key_file.write_text("from-file\n")
        original = os.environ.get(tts.KEY_ENV)
        os.environ[tts.KEY_ENV] = "from-env"
        self.addCleanup(lambda: os.environ.pop(tts.KEY_ENV, None)
                        if original is None else os.environ.__setitem__(tts.KEY_ENV, original))
        self.assertEqual(tts.load_key(key_file), "from-file")
        self.assertEqual(tts.load_key(self.dir / "missing"), "from-env")


if __name__ == "__main__":
    unittest.main(verbosity=2)
