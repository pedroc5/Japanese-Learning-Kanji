#!/usr/bin/env python3
"""Smoke tests for the page builder — no GIFs, no network, no conda.

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
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_page  # noqa: E402


def sample_content() -> dict:
    """Two kanji and three questions, including the characters that used to
    break the page: a double quote and a literal closing script tag."""
    return {
        "date": "2026-08-02",
        "theme": "天気",
        "kanji": [
            {"char": "雷", "level": "N1", "on": "ライ", "kun": "かみなり",
             "meaning": "thunder", "strokes": "13", "radical": "雨",
             "words": [{"w": "雷雨", "r": "らいう", "m": "雷をともなう雨"}],
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

    gifdec.js and friends talk *about* tags in their comments, so assertions
    about what the document contains have to stop before them.
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
        self.gifs = {"雷": "R0lGODlhAQABAAAAACw=", "虹": None}
        self.page = build_page.build(self.content, self.gifs,
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
        # 2 kanji + 書き取り練習 + 復習クイズ
        self.assertEqual(self.page.count('<details class="sec" open>'), 4)
        self.assertNotIn('<details class="sec">', self.page)

    def test_answer_list_stays_collapsed(self):
        answers = self.page.split("<summary>解答を表示</summary>", 1)[0]
        self.assertTrue(answers.rstrip().endswith("<details>"))

    # --- the GIF island -------------------------------------------------- #

    def test_gifs_live_at_the_end_not_in_the_body(self):
        self.assertNotIn("<img", markup(self.page))
        self.assertNotIn("data:image/gif", self.page)
        self.assertIn('<div class="player" data-gif="雷">', self.page)
        # 虹 failed to render, so it gets a placeholder and no data-gif hook
        self.assertIn('<div class="player"><div class="wait">', self.page)

    def test_gif_data_is_valid_json_and_only_has_rendered_gifs(self):
        block = self.page.split('id="gif-data">\n', 1)[1].split("\n</script>", 1)[0]
        self.assertEqual(json.loads(block), {"雷": "R0lGODlhAQABAAAAACw="})

    # --- quiz -> kanji attribution --------------------------------------- #

    def test_questions_are_attributed_to_their_kanji(self):
        key = quiz_key(self.page)
        self.assertEqual([entry["char"] for entry in key], ["雷", "虹", ""])

    def test_an_explicit_char_wins_over_inference(self):
        quiz = [{"q": "<b>雷雨</b>", "a": "らいう", "char": "虹"}]
        self.assertEqual(build_page.quiz_chars(quiz, self.content["kanji"]), ["虹"])

    # --- drafts ----------------------------------------------------------- #

    def test_page_id_scopes_the_saved_drafts(self):
        self.assertIn('data-page-id="daily:2026-08-02"', self.page)
        review = build_page.build(self.content, {}, page_id="review:2026-08-02")
        self.assertIn('data-page-id="review:2026-08-02"', review)

    def test_store_is_defined_before_the_quiz_script_uses_it(self):
        self.assertLess(self.page.index("window.__kanjiStore ="),
                        self.page.index("var store = window.__kanjiStore;"))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
