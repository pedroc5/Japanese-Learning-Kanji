#!/usr/bin/env python3
"""Build the Friday weekly-revision page from this week's daily content JSONs.

Pure aggregation, no new content is written by a model here: it reads
kanji_history.json for this week's day records (kind daily/extra, skipping
kind=review to avoid recursion), loads each day's saved content_<date>.json,
merges all kanji (deduped) and all quiz questions, and reuses build_page's
page shell to assemble the same kind of self-contained HTML page.

The weekly page is deliberately NOT a second copy of the daily pages. Those
already hold the stroke animations, tracing boxes, full word tables and example
sentences for every kanji, and repeating all of it for a whole week made a
half-megabyte page nobody scrolls to the end of. This one is:

    1. one table, one row per kanji — readings, meaning, a few words;
    2. the exercises, which is what a review is actually for.

    python build_review.py --date 2026-08-01

See SKILL.md for how/when this gets invoked (Fridays, after the daily run).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_page  # noqa: E402
from build_page import banner, esc, rich, word_ruby  # noqa: E402

# 一覧表に読み付きで載せる語の数。ここに出した語は練習問題では避ける（答えが
# すぐ上の表に書いてあることになるため）。
SHOWN_WORDS = 4


def week_range(d: date) -> tuple[date, date]:
    """The Monday and Sunday of the week containing `d`."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def week_days(history: dict, monday: date, sunday: date) -> list[dict]:
    """This week's day records, oldest first.

    Review days are skipped so a re-run doesn't fold last week's review page
    back into the new one.
    """
    days = [
        day for day in history["days"]
        if day.get("kind") != "review" and day.get("content_file")
        and monday <= date.fromisoformat(day["date"]) <= sunday
    ]
    days.sort(key=lambda day: day["date"])
    return days


def collect_week(days: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Load each day's saved content and merge it.

    Returns (kanji deduped by character, themes in order, the dates that
    actually contributed). A day whose content file has since been deleted is
    reported and skipped.

    The days' own quiz questions are deliberately not collected: the weekly
    exercises are new ones built from the word tables (see exercises()).
    """
    seen_chars: set[str] = set()
    merged_kanji: list[dict] = []
    themes: list[str] = []
    used_dates: list[str] = []

    for day in days:
        content_file = Path(day["content_file"])
        if not content_file.exists():
            print(f"  ! {content_file} が見つからないためスキップします", file=sys.stderr)
            continue
        day_content = json.loads(content_file.read_text(encoding="utf-8"))

        for entry in day_content.get("kanji", []):
            if entry["char"] not in seen_chars:
                seen_chars.add(entry["char"])
                merged_kanji.append(entry)

        theme = day.get("theme") or day_content.get("theme", "")
        if theme and theme not in themes:
            themes.append(theme)
        if day["date"] not in used_dates:
            used_dates.append(day["date"])

    return merged_kanji, themes, used_dates


def miss_badge(history: dict, char: str) -> str:
    """「✕2／6問」— how this kanji has actually been doing in the quizzes.

    Empty for a kanji that has never been quizzed or never been missed: the
    badge is there to make the week's weak spots findable in the table, not to
    decorate every row.
    """
    stats = history.get("kanji", {}).get(char, {}).get("quiz") or {}
    if not stats.get("asked") or not stats.get("wrong"):
        return ""
    return f'<span class="miss">✕{stats["wrong"]}／{stats["asked"]}問</span>'


def summary_table(kanji: list[dict], history: dict, words: int = SHOWN_WORDS) -> str:
    """One row per kanji: the whole week at a glance, in place of 51 sections.

    The daily pages keep the full treatment; what's needed here is enough to
    recognise the character and recall what it means before doing the
    exercises, so each row is the character, its readings, its meaning and the
    first few words it appeared in.
    """
    rows = []
    for entry in kanji:
        char = entry["char"]
        level = entry.get("level", "N3").upper()
        level_class = (f"lvl n{level[1:]}"
                       if level.startswith("N") and level[1:].isdigit() else "lvl")
        readings = []
        if entry.get("on"):
            readings.append(f'音 {esc(entry["on"])}')
        if entry.get("kun"):
            readings.append(f'訓 {esc(entry["kun"])}')
        examples = "・".join(
            word_ruby(word["w"], word.get("r", "")) for word in entry.get("words", [])[:words])
        rows.append(
            f'    <tr>'
            f'<td class="ch"><b>{esc(char)}</b>'
            f'<span class="{level_class}">{esc(level)}</span>{miss_badge(history, char)}</td>'
            f'<td class="yomi">{"<br>".join(readings) or "—"}</td>'
            f'<td>{rich(entry.get("meaning", ""))}</td>'
            f'<td>{examples}</td></tr>')
    return "\n".join([
        '<table class="sum">',
        "  <thead>",
        "    <tr><th>字</th><th>読み</th><th>意味</th><th>よく使う語</th></tr>",
        "  </thead>",
        "  <tbody>",
        *rows,
        "  </tbody>",
        "</table>",
    ])


def compound(word: str) -> bool:
    """True for a 熟語 — two or more kanji and no okurigana."""
    return len(word) >= 2 and all("一" <= ch <= "鿿" or ch == "々" for ch in word)


def gloss_of(word: dict) -> str:
    """The meaning to print in a question, minus the ones that give it away.

    The 意味 column often explains one word with another from the same set, so
    a gloss containing the word being asked about would print the answer right
    above the input box. The English column is the fallback.
    """
    meaning = word.get("m", "")
    if word["w"] in build_page.plain_sentence(meaning):
        return word.get("e", "")
    return meaning


def shown_words(kanji: list[dict], words: int = SHOWN_WORDS) -> set[str]:
    """Every word the summary table prints with its reading.

    A word is off-limits for the exercises wherever it appears in the table,
    not only in its own kanji's row: 感涙 sits in both 感's row and 涙's, so
    asking Pedro to write it would still be a copying exercise.
    """
    return {word["w"] for entry in kanji for word in entry.get("words", [])[:words]
            if word.get("w")}


def pick_words(entry: dict, shown: set[str]) -> tuple[list[dict], dict | None]:
    """Which of a kanji's words to build its exercises from.

    Two go to the reading questions and one to the 書き取り question, all
    different words, and — where the word list is long enough — none of them
    among the `shown` ones the summary table prints with furigana. A quiz whose
    answers are all sitting in the table above it is a reading exercise for the
    table, not for Pedro.

    Words written the same way but read differently (怒る as おこる and いかる)
    are dropped: shown on their own, they have no single right answer. They
    still appear in the table, which prints the reading beside them.

    Returns (reading words, writing word). Either can come up short for a kanji
    with a thin word list; then the exercises for it are just fewer.
    """
    words = [word for word in entry.get("words", []) if word.get("w") and word.get("r")]
    spelling = [word["w"] for word in words]
    words = [word for word in words if spelling.count(word["w"]) == 1]

    def take(pool: list[dict], want: int) -> list[dict]:
        """`want` words out of `pool`: a 熟語 and a word with okurigana first,
        so a kanji gets asked about in both of its reading styles."""
        chosen = [word for word in (next((w for w in pool if compound(w["w"])), None),
                                    next((w for w in pool if not compound(w["w"])), None))
                  if word]
        return (chosen + [word for word in pool if word not in chosen])[:want]

    # 表に出していない語を使い切ってから表の語に戻る。音読み/訓読みの取り合わせより、
    # 答えが上の表に書いてないことを優先する。
    fresh = [word for word in words if word["w"] not in shown]
    stale = [word for word in words if word["w"] in shown]

    reading = take(fresh, 2)
    if len(reading) < 2:
        reading += take([w for w in stale if w not in reading], 2 - len(reading))
    left_fresh = take([w for w in fresh if w not in reading], 1)
    left_stale = take([w for w in stale if w not in reading], 1)
    writing = (left_fresh or left_stale or [None])[0]
    return reading, writing


def reading_question(entry: dict, word: dict) -> dict:
    """読み: the word in kanji, its meaning, and "how is it read?"."""
    gloss = gloss_of(word)
    return {
        "q": f'<span class="qtag">読み</span>「<b>{esc(word["w"])}</b>」'
             f'{f"（{rich(gloss)}）" if gloss else ""}の読み方は？',
        "a": re.sub(r"[()（）]", "", word["r"]),
        "alt": [],
        "note": esc(entry.get("meaning", "")),
        "char": entry["char"],
    }


def writing_question(entry: dict, word: dict) -> dict:
    """書き: the reading and the meaning, write it in kanji.

    Which kanji it uses is the hint, and it stays folded away — given the
    character, most of these answer themselves. Pedro opens it when he's stuck,
    which is also the honest signal that the character needs another look.
    """
    gloss = gloss_of(word)
    return {
        "q": f'<span class="qtag write">書き</span>'
             f'「<b>{esc(re.sub(r"[()（）]", "", word["r"]))}</b>」'
             f'{f"（{rich(gloss)}）" if gloss else ""}を漢字で書きましょう。'
             f'<details class="hint"><summary>ヒント</summary>'
             f'「{esc(entry["char"])}」を使います。</details>',
        "a": word["w"],
        "alt": [],
        "note": esc(entry.get("meaning", "")),
        "char": entry["char"],
        "ph": "漢字で入力",
    }


def exercises(kanji: list[dict], shown: int = SHOWN_WORDS, writing: bool = True) -> list[dict]:
    """The week's practice: per kanji, two 読み questions then one 書き.

    Written here rather than collected from the daily pages on purpose. Those
    questions have already been answered once this week, so re-asking them
    tests what Pedro remembers of Tuesday's page as much as it tests the kanji;
    these are built from the same word tables but ask about different words,
    and the 書き direction (produce the kanji) is one the daily pages never ask
    for at all.

    The kanji arrive in review order, so the exercises come out in it too:
    everything for the hardest character first, its 書き question last so the
    answer isn't sitting in the question above it.
    """
    questions = []
    off_limits = shown_words(kanji, shown)
    for entry in kanji:
        reading_words, writing_word = pick_words(entry, off_limits)
        questions += [reading_question(entry, word) for word in reading_words]
        if writing and writing_word:
            questions.append(writing_question(entry, writing_word))
    return questions


def main() -> int:
    parser = argparse.ArgumentParser(description="今週分の漢字練習をまとめた復習ページを作る")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="この日を含む週を復習する（既定：今日）")
    parser.add_argument("--history", type=Path, default=build_page.DEFAULT_HISTORY)
    parser.add_argument("--root", type=Path, default=build_page.DEFAULT_ROOT,
                        help="出力の置き場（既定：~/Documents/Claude-JP/漢字）")
    parser.add_argument("--out", type=Path,
                        help="出力HTML（既定：<root>/<その週>/復習_<date>.html）")
    parser.add_argument("--no-writing", action="store_true",
                        help="書き取り問題を作らない（読みの問題だけにする）")
    args = parser.parse_args()

    monday, sunday = week_range(date.fromisoformat(args.date))

    history = build_page.load_history(args.history)
    merged_kanji, themes, used_dates = collect_week(week_days(history, monday, sunday))

    if not merged_kanji:
        print(f"今週（{monday.isoformat()}〜{sunday.isoformat()}）分の記録が見つからないため、"
              "復習ページは作成しませんでした。")
        return 0

    # Any scores collected since the last build have to land in the history
    # before it's used for ordering, or this week's own results are invisible.
    graded = build_page.ingest_quiz_results(history)

    # Hardest first: the point of the weekly page is the kanji Pedro actually
    # missed, not another pass in the order they happened to be taught.
    merged_kanji.sort(key=lambda entry: build_page.quiz_priority(history, entry["char"]))

    quiz = exercises(merged_kanji, shown=SHOWN_WORDS, writing=not args.no_writing)
    writing_count = sum(1 for question in quiz if "ph" in question)

    missed = [entry["char"] for entry in merged_kanji
              if build_page.quiz_priority(history, entry["char"])[0] < 0]

    theme_str = "・".join(themes) if themes else "今週のまとめ"
    merged_content = {
        "date": args.date,
        "theme": f"今週の復習（{monday.isoformat()}〜{sunday.isoformat()}）：{theme_str}",
        "note": ("クイズで間違えた字から順に並べています（今週の取りこぼし："
                 + "・".join(missed) + "）。" if missed else
                 "クイズの記録がまだないので、習った順に並べています。"),
        "kanji": merged_kanji,
        "quiz": quiz,
    }

    sections = [
        banner("今週習った漢字（一覧）"),
        build_page.collapsible(
            "<h2>今週習った漢字</h2>",
            "\n".join([
                '<p class="en">字の練習（筆順・書き取り・例文）はその日のページにあります。'
                'ここは思い出すための一覧と、下の練習問題です。</p>',
                summary_table(merged_kanji, history),
            ])),
        "",
        banner("練習問題"),
        build_page.quiz_section(
            quiz, [question["char"] for question in quiz],
            heading=f"今週の練習問題（{len(quiz)}問）"
                    f"／「読み」はひらがな、「書き」は漢字で答えます"),
        "",
    ]

    # 日々のページと同じ、その週のフォルダに置く。
    out = args.out or (args.root / build_page.week_folder(args.date)
                       / f"復習_{args.date}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_page.build(merged_content, {}, page_id=f"review:{args.date}",
                                    sections=sections, heading="漢字の復習", period="今週"),
                   encoding="utf-8")

    build_page.update_history(history, merged_content, "review", args.date, str(out))
    build_page.save_history(args.history, history)

    size = out.stat().st_size // 1024
    print(f"完了：{out}（{len(used_dates)}日分・{len(merged_kanji)}字・"
          f"練習 {len(quiz)}問〔うち書き取り {writing_count}問〕・{size}KB）")
    if graded:
        print(f"  クイズの成績 {graded} 問分を履歴に記録しました")
    if missed:
        print(f"  間違えた字を先頭に並べました — {'・'.join(missed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
