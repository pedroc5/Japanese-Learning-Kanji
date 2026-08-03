#!/usr/bin/env python3
"""Build the Friday weekly-revision page from this week's daily content JSONs.

Pure aggregation, no new content is written by a model here: it reads
kanji_history.json for this week's day records (kind daily/extra, skipping
kind=review to avoid recursion), loads each day's saved content_<date>.json,
merges all kanji (deduped) and all quiz questions, and reuses build_page's
make_gifs()/build() to assemble the same kind of self-contained HTML page.

    python build_review.py --date 2026-08-01

See SKILL.md for how/when this gets invoked (Fridays, after the daily run).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_page  # noqa: E402


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


def collect_week(days: list[dict]) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """Load each day's saved content and merge it.

    Returns (kanji deduped by character, all quiz questions, themes in order,
    the dates that actually contributed). A day whose content file has since
    been deleted is reported and skipped.
    """
    seen_chars: set[str] = set()
    merged_kanji: list[dict] = []
    merged_quiz: list[dict] = []
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
        merged_quiz.extend(day_content.get("quiz", []))

        theme = day.get("theme") or day_content.get("theme", "")
        if theme and theme not in themes:
            themes.append(theme)
        if day["date"] not in used_dates:
            used_dates.append(day["date"])

    return merged_kanji, merged_quiz, themes, used_dates


def main() -> int:
    parser = argparse.ArgumentParser(description="今週分の漢字練習をまとめた復習ページを作る")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="この日を含む週を復習する（既定：今日）")
    parser.add_argument("--history", type=Path, default=build_page.DEFAULT_HISTORY)
    parser.add_argument("--out", type=Path, help="出力HTML（既定：<historyの親>/復習_<date>.html）")
    parser.add_argument("--gifdir", type=Path,
                        default=Path.home() / "Documents" / "Claude-JP" / "漢字" / "gif")
    parser.add_argument("--maker", type=Path, default=build_page.DEFAULT_MAKER)
    parser.add_argument("--size", type=int, default=240)
    parser.add_argument("--skip-gif", action="store_true")
    parser.add_argument("--conda-env", default="kanji")
    args = parser.parse_args()

    monday, sunday = week_range(date.fromisoformat(args.date))

    history = build_page.load_history(args.history)
    merged_kanji, merged_quiz, themes, used_dates = collect_week(
        week_days(history, monday, sunday))

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
    order = {entry["char"]: i for i, entry in enumerate(merged_kanji)}
    quiz_chars = build_page.quiz_chars(merged_quiz, merged_kanji)
    merged_quiz = [question for _, question in sorted(
        enumerate(merged_quiz),
        key=lambda pair: (order.get(quiz_chars[pair[0]], len(order)), pair[0]),
    )]

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
        "quiz": merged_quiz,
    }

    gifs: dict[str, str | None] = {}
    failed: list[str] = []
    if not args.skip_gif:
        gifs, failed = build_page.make_gifs(merged_kanji, args.gifdir, args.maker,
                                            args.size, args.conda_env)

    out = args.out or (args.history.parent / f"復習_{args.date}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_page.build(merged_content, gifs, page_id=f"review:{args.date}"),
                   encoding="utf-8")

    build_page.update_history(history, merged_content, "review", args.date, str(out))
    build_page.save_history(args.history, history)

    made = sum(1 for gif in gifs.values() if gif)
    print(f"完了：{out}（{len(used_dates)}日分・{len(merged_kanji)}字・"
          f"GIF {made}/{len(merged_kanji)}）")
    if graded:
        print(f"  クイズの成績 {graded} 問分を履歴に記録しました")
    if missed:
        print(f"  間違えた字を先頭に並べました — {'・'.join(missed)}")
    if failed:
        print(f"警告: GIFを作れなかった字があります — {'・'.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
