#!/usr/bin/env python3
"""Turn a KanjiVG SVG into an animated stroke-order GIF.

Each stroke is drawn in its own colour, in the order KanjiVG stores them, with
the stroke number appearing as the stroke starts. Strokes still to come sit
underneath in light grey, so the whole character is legible from the first frame.

Examples
--------
    python kanji_gif.py https://github.com/KanjiVG/kanjivg/blob/master/kanji/080fd.svg
    python kanji_gif.py https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/080fd.svg
    python kanji_gif.py ./080fd.svg --size 500 --grid -o noh.gif
"""

from __future__ import annotations

import argparse
import bisect
import colorsys
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont
from svgpathtools import parse_path

SVG_NS = "{http://www.w3.org/2000/svg}"

# Categorical palette, in the spirit of the usual stroke-order charts.
PALETTE = [
    "#e8453c",  # red
    "#4a90d9",  # blue
    "#f5a623",  # orange
    "#3aa93a",  # green
    "#9b7fd4",  # purple
    "#e8194b",  # crimson
    "#2c3e50",  # navy
    "#16b79b",  # teal
    "#7b5b3f",  # brown
    "#d45fb0",  # magenta
    "#8fb701",  # olive
    "#00a0c6",  # cyan
]

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

@dataclass
class Stroke:
    """One KanjiVG stroke: its geometry plus where its number label goes."""

    path: object                 # svgpathtools Path, in SVG user units
    label: str
    label_xy: tuple[float, float]  # left/baseline anchor, SVG user units


@dataclass
class Kanji:
    strokes: list[Stroke]
    view_box: tuple[float, float, float, float]
    stroke_width: float


def _style_value(style: str, prop: str) -> str | None:
    m = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", style or "")
    return m.group(1).strip() if m else None


def _group_by_id_prefix(root: ET.Element, prefix: str) -> ET.Element | None:
    for g in root.iter(SVG_NS + "g"):
        if (g.get("id") or "").startswith(prefix):
            return g
    return None


def _label_positions(root: ET.Element) -> list[tuple[float, float]]:
    """Read the baseline anchors out of the kvg:StrokeNumbers group."""
    group = _group_by_id_prefix(root, "kvg:StrokeNumbers")
    if group is None:
        return []
    out: list[tuple[float, float]] = []
    for text in group.iter(SVG_NS + "text"):
        transform = text.get("transform", "")
        m = re.search(r"matrix\(([^)]*)\)", transform)
        if m:
            nums = [float(v) for v in re.split(r"[,\s]+", m.group(1).strip()) if v]
            if len(nums) == 6:
                out.append((nums[4], nums[5]))
                continue
        out.append((float(text.get("x", 0)), float(text.get("y", 0))))
    return out


def load_kanji(svg_text: str) -> Kanji:
    """Pull the ordered strokes and their number anchors out of a KanjiVG file."""
    root = ET.fromstring(svg_text)

    vb = root.get("viewBox")
    if vb:
        minx, miny, width, height = (float(v) for v in re.split(r"[,\s]+", vb.strip()))
    else:
        width = float(root.get("width", 109))
        height = float(root.get("height", 109))
        minx = miny = 0.0

    paths_group = _group_by_id_prefix(root, "kvg:StrokePaths")
    if paths_group is None:
        paths_group = root
    stroke_width = 3.0
    sw = _style_value(paths_group.get("style", ""), "stroke-width")
    if sw:
        try:
            stroke_width = float(re.sub(r"[^0-9.]", "", sw))
        except ValueError:
            pass

    # Document order in KanjiVG *is* stroke order.
    path_elems = [p for p in paths_group.iter(SVG_NS + "path") if p.get("d")]
    if not path_elems:
        raise ValueError("no <path> elements found — is this a KanjiVG file?")

    geoms = [parse_path(p.get("d")) for p in path_elems]

    labels = _label_positions(root)
    if len(labels) != len(geoms):
        # Fall back to nudging the label away from the stroke's starting point.
        labels = []
        for g in geoms:
            start = g.point(0.0)
            head = g.point(min(0.15, 1.0))
            dx, dy = head.real - start.real, head.imag - start.imag
            norm = math.hypot(dx, dy) or 1.0
            labels.append((start.real - 4.5 * dx / norm, start.imag - 4.5 * dy / norm))

    strokes = [
        Stroke(path=g, label=str(i + 1), label_xy=labels[i])
        for i, g in enumerate(geoms)
    ]
    return Kanji(strokes=strokes, view_box=(minx, miny, width, height), stroke_width=stroke_width)


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def polyline(path, scale: float, offset: tuple[float, float], step_px: float = 1.5):
    """Flatten a path to pixel points plus a cumulative-length table."""
    total_units = path.length()
    n = max(int(total_units * scale / step_px), 24)
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        z = path.point(i / n)
        pts.append(((z.real - offset[0]) * scale, (z.imag - offset[1]) * scale))

    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return pts, cum


def points_upto(pts, cum, frac: float):
    """The leading portion of a polyline, cut at `frac` of its arc length."""
    target = cum[-1] * max(0.0, min(1.0, frac))
    k = bisect.bisect_right(cum, target)
    if k <= 1:
        return [pts[0]]
    head = pts[:k]
    if k < len(pts):
        span = cum[k] - cum[k - 1]
        t = (target - cum[k - 1]) / span if span else 0.0
        a, b = pts[k - 1], pts[k]
        head = head + [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)]
    return head


def draw_stroke(draw: ImageDraw.ImageDraw, pts, color: str, width: float) -> None:
    """Round-capped, round-joined line — the look KanjiVG's own styling asks for.

    Stamping overlapping discs along the path rather than using PIL's wide-line
    modes: both `joint="curve"` and a plain wide line leave hairline notches at
    every vertex, and a flattened path has hundreds of them.
    """
    r = width / 2.0
    if len(pts) >= 2:
        draw.line(pts, fill=color, width=int(round(width)))

    step = max(width / 8.0, 1.0)
    last = None
    for x, y in pts:
        if last is None or math.hypot(x - last[0], y - last[1]) >= step:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
            last = (x, y)
    x, y = pts[-1]  # the moving tip needs its cap on every frame
    draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def dashed_line(draw, p0, p1, color, width, dash: float, gap: float) -> None:
    x0, y0 = p0
    x1, y1 = p1
    length = math.hypot(x1 - x0, y1 - y0)
    if not length:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        draw.line(
            [(x0 + ux * pos, y0 + uy * pos), (x0 + ux * end, y0 + uy * end)],
            fill=color,
            width=int(round(width)),
        )
        pos = end + gap


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def stroke_colors(count: int, palette: str) -> list[str]:
    if palette == "rainbow":
        out = []
        for i in range(count):
            r, g, b = colorsys.hsv_to_rgb(i / max(count, 1), 0.78, 0.88)
            out.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
        return out
    if palette == "mono":
        return ["#1a1a1a"] * count
    return [PALETTE[i % len(PALETTE)] for i in range(count)]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_gif(
    kanji: Kanji,
    out_path: Path,
    size: int = 400,
    fps: int = 30,
    speed: float = 2.2,
    min_frames: int = 6,
    pause: float = 0.12,
    hold: float = 1.6,
    stroke_width: float | None = None,
    palette: str = "categorical",
    bg: str = "#ffffff",
    numbers: bool = True,
    grid: bool = False,
    ghost: bool = True,
    ghost_color: str = "#e6e6e6",
    supersample: int = 3,
) -> Path:
    minx, miny, vw, vh = kanji.view_box
    scale = size / max(vw, vh)
    ss = max(1, supersample)
    canvas = size * ss
    px_scale = scale * ss
    offset = (minx, miny)

    width_px = (stroke_width or kanji.stroke_width) * px_scale
    colors = stroke_colors(len(kanji.strokes), palette)
    font = load_font(max(int(8 * px_scale), 8))
    frame_ms = max(int(round(1000 / fps)), 20)

    flat = [polyline(s.path, px_scale, offset) for s in kanji.strokes]

    base = Image.new("RGB", (canvas, canvas), bg)
    d = ImageDraw.Draw(base)

    if grid:
        gc = "#f3d3d3"
        gw = max(1.0, 0.6 * ss)
        dash, gapl = 9 * ss, 6 * ss
        dashed_line(d, (canvas / 2, 0), (canvas / 2, canvas), gc, gw, dash, gapl)
        dashed_line(d, (0, canvas / 2), (canvas, canvas / 2), gc, gw, dash, gapl)
        d.rectangle((0, 0, canvas - 1, canvas - 1), outline=gc, width=int(gw))

    if ghost:
        # Laid down first, so each coloured stroke paints over its own ghost as
        # it is drawn — the reader sees the whole character from frame one.
        for pts, _ in flat:
            draw_stroke(d, pts, ghost_color, width_px)

    def shrink(img: Image.Image) -> Image.Image:
        return img.resize((size, size), Image.LANCZOS) if ss > 1 else img.copy()

    def put_label(target: ImageDraw.ImageDraw, stroke: Stroke, color: str) -> None:
        if not numbers:
            return
        x = (stroke.label_xy[0] - minx) * px_scale
        y = (stroke.label_xy[1] - miny) * px_scale
        kwargs = dict(fill=color, font=font, stroke_width=max(int(1.2 * ss), 1), stroke_fill=bg)
        try:
            target.text((x, y), stroke.label, anchor="ls", **kwargs)
        except (ValueError, TypeError):  # bitmap fallback font: no anchors
            target.text((x, y - 8 * px_scale), stroke.label, **kwargs)

    frames: list[Image.Image] = []
    durations: list[int] = []

    for stroke, (pts, cum), color in zip(kanji.strokes, flat, colors):
        units = cum[-1] / px_scale
        n_frames = max(min_frames, int(math.ceil(units / max(speed, 0.1))))

        for f in range(1, n_frames + 1):
            frame = base.copy()
            fd = ImageDraw.Draw(frame)
            draw_stroke(fd, points_upto(pts, cum, f / n_frames), color, width_px)
            put_label(fd, stroke, color)
            frames.append(shrink(frame))
            durations.append(frame_ms)

        # Bake the finished stroke into the background for the next round.
        draw_stroke(d, pts, color, width_px)
        put_label(d, stroke, color)
        if pause > 0:
            frames.append(shrink(base))
            durations.append(max(int(pause * 1000), 20))

    frames.append(shrink(base))
    durations.append(max(int(hold * 1000), 20))

    # One shared palette keeps colours from shifting between frames. It has to be
    # sampled from the start and middle as well as the end: the ghost grey is
    # painted over by the last frame, and a palette that never saw it would snap
    # it to whatever unrelated colour happens to be nearest.
    picks = sorted({0, len(frames) // 2, len(frames) - 1})
    strip = Image.new("RGB", (size * len(picks), size))
    for i, f in enumerate(picks):
        strip.paste(frames[f], (i * size, 0))
    shared = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    paletted = [f.quantize(palette=shared, dither=Image.Dither.NONE) for f in frames]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    paletted[0].save(
        out_path,
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        disposal=1,
        optimize=True,
    )
    return out_path


# --------------------------------------------------------------------------- #
# Input resolution
# --------------------------------------------------------------------------- #

def to_raw_url(url: str) -> str:
    """Rewrite a GitHub web URL to the raw file it points at.

    github.com/<owner>/<repo>/blob/<ref>/<path>  ->  raw.githubusercontent.com/...
    The /raw/ variant maps the same way; anything else is passed through.
    """
    parts = urlsplit(url)
    if parts.netloc not in ("github.com", "www.github.com"):
        return url
    m = re.match(r"^/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$", parts.path)
    if not m:
        return url
    owner, repo, rest = m.groups()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"


def resolve_svg(source: str, cache_dir: Path) -> tuple[str, str]:
    """Accept a KanjiVG URL (GitHub web or raw) or a local .svg path."""
    if source.startswith(("http://", "https://")):
        url = to_raw_url(source)
        name = Path(urlsplit(url).path).stem
        if not name:
            raise SystemExit(f"no filename in URL: {source}")
        return fetch(url, cache_dir / f"{name}.svg"), name

    local = Path(source)
    if local.exists():
        return local.read_text(encoding="utf-8"), local.stem

    raise SystemExit(
        f"expected a KanjiVG URL or a local .svg file, got {source!r}\n"
        "  e.g. https://github.com/KanjiVG/kanjivg/blob/master/kanji/080fd.svg"
    )


def fetch(url: str, cache_file: Path) -> str:
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    import requests

    resp = requests.get(url, timeout=30)
    if resp.status_code == 404:
        raise SystemExit(f"not in KanjiVG: {url}")
    resp.raise_for_status()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(resp.text, encoding="utf-8")
    return resp.text


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Render a KanjiVG SVG as a stroke-order GIF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("source", nargs="+", help="KanjiVG URL (GitHub web or raw), or a local .svg path")
    p.add_argument("-o", "--output", type=Path, help="output file (single source only)")
    p.add_argument("--outdir", type=Path, default=Path("."), help="directory for outputs")
    p.add_argument("--size", type=int, default=400, help="output size in pixels")
    p.add_argument("--fps", type=int, default=30, help="frames per second")
    p.add_argument("--speed", type=float, default=2.2, help="SVG units drawn per frame")
    p.add_argument("--min-frames", type=int, default=6, help="minimum frames per stroke")
    p.add_argument("--pause", type=float, default=0.12, help="seconds held after each stroke")
    p.add_argument("--hold", type=float, default=1.6, help="seconds held on the finished kanji")
    p.add_argument("--stroke-width", type=float, help="override the SVG stroke width")
    p.add_argument("--palette", choices=["categorical", "rainbow", "mono"], default="categorical")
    p.add_argument("--bg", default="#ffffff", help="background colour")
    p.add_argument("--no-numbers", action="store_true", help="hide stroke numbers")
    p.add_argument("--grid", action="store_true", help="draw a practice grid")
    p.add_argument("--no-ghost", action="store_true", help="hide the light grey preview of upcoming strokes")
    p.add_argument("--ghost-color", default="#e6e6e6", help="colour of the upcoming strokes")
    p.add_argument("--supersample", type=int, default=3, help="anti-aliasing factor")
    p.add_argument("--cache-dir", type=Path, default=Path(".kanjivg_cache"))
    args = p.parse_args(argv)

    if args.output and len(args.source) > 1:
        p.error("-o works with a single source; use --outdir for several")

    for source in args.source:
        svg_text, code = resolve_svg(source, args.cache_dir)
        kanji = load_kanji(svg_text)
        out = args.output or args.outdir / f"{code}.gif"
        render_gif(
            kanji,
            out,
            size=args.size,
            fps=args.fps,
            speed=args.speed,
            min_frames=args.min_frames,
            pause=args.pause,
            hold=args.hold,
            stroke_width=args.stroke_width,
            palette=args.palette,
            bg=args.bg,
            numbers=not args.no_numbers,
            grid=args.grid,
            ghost=not args.no_ghost,
            ghost_color=args.ghost_color,
            supersample=args.supersample,
        )
        print(f"{out}  ({len(kanji.strokes)} strokes)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
