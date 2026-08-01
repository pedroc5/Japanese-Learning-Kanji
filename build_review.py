#!/usr/bin/env python3
"""Build the Friday weekly-revision page from this week's daily content JSONs.

Pure aggregation, no new content is written by a model here: it reads
kanji_history.json for this week's day records (kind daily/extra, skipping
kind=review to avoid recursion), loads each day's saved content_<date>.json,
merges all kanji (deduped) and all quiz questions, and reuses build_page's
make_gif()/build() to assemble the same kind of self-contained HTML page.

    python build_review.py --date 2026-08-01

See SKILL.md for how/when this gets invoked (Fridays, after the daily run).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_page as bp  # noqa: E402


def week_range(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def main() -> int:
    p = argparse.ArgumentParser(description="今週分の漢字練習をまとめた復習ページを作る")
    p.add_argument("--date", default=date.today().isoformat(), help="この日を含む週を復習する（既定：今日）")
    p.add_argument("--history", type=Path, default=bp.DEFAULT_HISTORY)
    p.add_argument("--out", type=Path, help="出力HTML（既定：<historyの親>/復習_<date>.html）")
    p.add_argument("--gifdir", type=Path, default=Path.home() / "Documents" / "Claude-JP" / "漢字" / "gif")
    p.add_argument("--maker", type=Path, default=bp.DEFAULT_MAKER)
    p.add_argument("--size", type=int, default=240)
    p.add_argument("--skip-gif", action="store_true")
    p.add_argument("--conda-env", default="kanji")
    a = p.parse_args()

    d = date.fromisoformat(a.date)
    monday, sunday = week_range(d)

    hist = bp.load_history(a.history)
    days = [
        day for day in hist["days"]
        if day.get("kind") != "review" and day.get("content_file")
        and monday <= date.fromisoformat(day["date"]) <= sunday
    ]
    days.sort(key=lambda x: x["date"])

    seen_chars: set[str] = set()
    merged_kanji: list[dict] = []
    merged_quiz: list[dict] = []
    themes: list[str] = []
    used_dates: list[str] = []

    for day in days:
        cf = Path(day["content_file"])
        if not cf.exists():
            print(f"  ! {cf} が見つからないためスキップします", file=sys.stderr)
            continue
        import json
        day_content = json.loads(cf.read_text(encoding="utf-8"))
        for k in day_content.get("kanji", []):
            if k["char"] not in seen_chars:
                seen_chars.add(k["char"])
                merged_kanji.append(k)
        merged_quiz.extend(day_content.get("quiz", []))
        theme = day.get("theme") or day_content.get("theme", "")
        if theme and theme not in themes:
            themes.append(theme)
        if day["date"] not in used_dates:
            used_dates.append(day["date"])

    if not merged_kanji:
        print(f"今週（{monday.isoformat()}〜{sunday.isoformat()}）分の記録が見つからないため、"
              "復習ページは作成しませんでした。")
        return 0

    theme_str = "・".join(themes) if themes else "今週のまとめ"
    merged_content = {
        "date": a.date,
        "theme": f"今週の復習（{monday.isoformat()}〜{sunday.isoformat()}）：{theme_str}",
        "kanji": merged_kanji,
        "quiz": merged_quiz,
    }

    gifs: dict[str, str | None] = {}
    failed: list[str] = []
    if not a.skip_gif:
        for k in merged_kanji:
            print(f"  … {k['char']} のGIFを作成中")
            gifs[k["char"]] = bp.make_gif(k["char"], a.gifdir, a.maker, a.size, a.conda_env)
            if not gifs[k["char"]]:
                failed.append(k["char"])

    out = a.out or (a.history.parent / f"復習_{a.date}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(bp.build(merged_content, gifs, str(out)), encoding="utf-8")

    bp.update_history(hist, merged_content, "review", a.date, str(out))
    bp.save_history(a.history, hist)

    ok = sum(1 for v in gifs.values() if v)
    print(f"完了：{out}（{len(used_dates)}日分・{len(merged_kanji)}字・GIF {ok}/{len(merged_kanji)}）")
    if failed:
        print(f"警告: GIFを作れなかった字があります — {'・'.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
