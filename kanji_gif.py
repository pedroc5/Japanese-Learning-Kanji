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
    """A parsed KanjiVG character: its strokes in writing order, plus the
    canvas geometry needed to scale them to pixels."""

    strokes: list[Stroke]
    view_box: tuple[float, float, float, float]   # minx, miny, width, height
    stroke_width: float                           # SVG user units


def _style_value(style: str, prop: str) -> str | None:
    """Pull one property out of an inline `style="a:1;b:2"` attribute."""
    match = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", style or "")
    return match.group(1).strip() if match else None


def _group_by_id_prefix(root: ET.Element, prefix: str) -> ET.Element | None:
    """The first <g> whose id starts with `prefix` (KanjiVG suffixes ids with
    the character's codepoint, so an exact match won't do)."""
    for group in root.iter(SVG_NS + "g"):
        if (group.get("id") or "").startswith(prefix):
            return group
    return None


def _label_positions(root: ET.Element) -> list[tuple[float, float]]:
    """Read the baseline anchors out of the kvg:StrokeNumbers group."""
    group = _group_by_id_prefix(root, "kvg:StrokeNumbers")
    if group is None:
        return []
    positions: list[tuple[float, float]] = []
    for text in group.iter(SVG_NS + "text"):
        # Usually positioned by transform="matrix(a b c d e f)", where (e, f)
        # is the translation; fall back to plain x/y attributes.
        match = re.search(r"matrix\(([^)]*)\)", text.get("transform", ""))
        if match:
            nums = [float(v) for v in re.split(r"[,\s]+", match.group(1).strip()) if v]
            if len(nums) == 6:
                positions.append((nums[4], nums[5]))
                continue
        positions.append((float(text.get("x", 0)), float(text.get("y", 0))))
    return positions


def _view_box(root: ET.Element) -> tuple[float, float, float, float]:
    """The drawing area as (minx, miny, width, height); 109x109 is KanjiVG's own."""
    view_box = root.get("viewBox")
    if view_box:
        minx, miny, width, height = (float(v) for v in re.split(r"[,\s]+", view_box.strip()))
        return minx, miny, width, height
    return 0.0, 0.0, float(root.get("width", 109)), float(root.get("height", 109))


def _fallback_label_positions(geoms: list) -> list[tuple[float, float]]:
    """Anchors for files without a usable kvg:StrokeNumbers group.

    Places each number just *behind* where the stroke starts, by stepping back
    along its initial direction, so the label doesn't sit on top of the ink.
    """
    positions = []
    for geom in geoms:
        start = geom.point(0.0)
        head = geom.point(min(0.15, 1.0))
        dx, dy = head.real - start.real, head.imag - start.imag
        norm = math.hypot(dx, dy) or 1.0
        positions.append((start.real - 4.5 * dx / norm, start.imag - 4.5 * dy / norm))
    return positions


def load_kanji(svg_text: str) -> Kanji:
    """Pull the ordered strokes and their number anchors out of a KanjiVG file."""
    root = ET.fromstring(svg_text)
    minx, miny, width, height = _view_box(root)

    paths_group = _group_by_id_prefix(root, "kvg:StrokePaths") or root
    stroke_width = 3.0
    declared_width = _style_value(paths_group.get("style", ""), "stroke-width")
    if declared_width:
        try:
            stroke_width = float(re.sub(r"[^0-9.]", "", declared_width))
        except ValueError:
            pass                                  # keep the 3.0 default

    # Document order in KanjiVG *is* stroke order.
    path_elems = [p for p in paths_group.iter(SVG_NS + "path") if p.get("d")]
    if not path_elems:
        raise ValueError("no <path> elements found — is this a KanjiVG file?")
    geoms = [parse_path(p.get("d")) for p in path_elems]

    labels = _label_positions(root)
    if len(labels) != len(geoms):
        labels = _fallback_label_positions(geoms)

    strokes = [
        Stroke(path=geom, label=str(i + 1), label_xy=labels[i])
        for i, geom in enumerate(geoms)
    ]
    return Kanji(strokes=strokes, view_box=(minx, miny, width, height),
                 stroke_width=stroke_width)


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def polyline(path, scale: float, offset: tuple[float, float], step_px: float = 1.5):
    """Flatten a path to pixel points plus a cumulative-length table.

    The length table is what makes the brush advance at a constant speed: SVG
    curves are parameterised unevenly, so sampling t alone would race through
    some segments and crawl through others.
    """
    total_units = path.length()
    steps = max(int(total_units * scale / step_px), 24)

    points: list[tuple[float, float]] = []
    for i in range(steps + 1):
        z = path.point(i / steps)                 # svgpathtools returns a complex
        points.append(((z.real - offset[0]) * scale, (z.imag - offset[1]) * scale))

    lengths = [0.0]
    for current, following in zip(points, points[1:]):
        lengths.append(lengths[-1]
                       + math.hypot(following[0] - current[0], following[1] - current[1]))
    return points, lengths


def points_upto(points, lengths, frac: float):
    """The leading portion of a polyline, cut at `frac` of its arc length."""
    target = lengths[-1] * max(0.0, min(1.0, frac))
    i = bisect.bisect_right(lengths, target)
    if i <= 1:
        return [points[0]]

    head = points[:i]
    if i < len(points):
        # Interpolate a final point so the tip lands exactly on `target`
        # rather than snapping to the nearest sample.
        span = lengths[i] - lengths[i - 1]
        t = (target - lengths[i - 1]) / span if span else 0.0
        start, end = points[i - 1], points[i]
        head = head + [(start[0] + (end[0] - start[0]) * t,
                        start[1] + (end[1] - start[1]) * t)]
    return head


def draw_stroke(draw: ImageDraw.ImageDraw, points, color: str, width: float) -> None:
    """Round-capped, round-joined line — the look KanjiVG's own styling asks for.

    Stamping overlapping discs along the path rather than using PIL's wide-line
    modes: both `joint="curve"` and a plain wide line leave hairline notches at
    every vertex, and a flattened path has hundreds of them.
    """
    radius = width / 2.0
    if len(points) >= 2:
        draw.line(points, fill=color, width=int(round(width)))

    # Discs go down every `step` pixels — dense enough to fill the notches,
    # sparse enough not to redraw the same spot hundreds of times.
    step = max(width / 8.0, 1.0)
    last = None
    for x, y in points:
        if last is None or math.hypot(x - last[0], y - last[1]) >= step:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
            last = (x, y)
    x, y = points[-1]  # the moving tip needs its cap on every frame
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def dashed_line(draw, start, end, color, width, dash: float, gap: float) -> None:
    """Draw a dashed segment, used for the practice grid's centre lines."""
    x0, y0 = start
    x1, y1 = end
    length = math.hypot(x1 - x0, y1 - y0)
    if not length:
        return

    unit_x, unit_y = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    while pos < length:
        dash_end = min(pos + dash, length)
        draw.line(
            [(x0 + unit_x * pos, y0 + unit_y * pos),
             (x0 + unit_x * dash_end, y0 + unit_y * dash_end)],
            fill=color,
            width=int(round(width)),
        )
        pos = dash_end + gap


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """First usable font from FONT_CANDIDATES, else PIL's built-in bitmap font.

    The bitmap fallback ignores the requested size and can't do text anchors —
    see put_label() in render_gif() for the workaround.
    """
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def stroke_colors(count: int, palette: str) -> list[str]:
    """One colour per stroke, as #rrggbb.

    "categorical" cycles the fixed PALETTE, "rainbow" spreads hues evenly over
    the stroke count, "mono" is all near-black.
    """
    if palette == "rainbow":
        colors = []
        for i in range(count):
            r, g, b = colorsys.hsv_to_rgb(i / max(count, 1), 0.78, 0.88)
            colors.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
        return colors
    if palette == "mono":
        return ["#1a1a1a"] * count
    return [PALETTE[i % len(PALETTE)] for i in range(count)]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def draw_practice_grid(draw: ImageDraw.ImageDraw, canvas: int, supersample: int) -> None:
    """The dashed cross and border of a 漢字 practice square."""
    color = "#f3d3d3"
    width = max(1.0, 0.6 * supersample)
    dash, gap = 9 * supersample, 6 * supersample
    dashed_line(draw, (canvas / 2, 0), (canvas / 2, canvas), color, width, dash, gap)
    dashed_line(draw, (0, canvas / 2), (canvas, canvas / 2), color, width, dash, gap)
    draw.rectangle((0, 0, canvas - 1, canvas - 1), outline=color, width=int(width))


def quantize_frames(frames: list[Image.Image], size: int) -> list[Image.Image]:
    """Map every frame onto one shared palette, so colours don't shift mid-GIF.

    The palette is sampled from the start and middle as well as the end: the
    ghost grey is painted over by the last frame, and a palette that never saw
    it would snap it to whatever unrelated colour happens to be nearest.
    """
    sampled = sorted({0, len(frames) // 2, len(frames) - 1})
    strip = Image.new("RGB", (size * len(sampled), size))
    for i, index in enumerate(sampled):
        strip.paste(frames[index], (i * size, 0))
    shared = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    return [frame.quantize(palette=shared, dither=Image.Dither.NONE) for frame in frames]


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
    """Animate `kanji` one stroke at a time and write the GIF to `out_path`.

    Frames are drawn on a `supersample`x oversized canvas and shrunk down at
    the end, which is what smooths the edges. `speed` is in SVG units per
    frame, so long strokes naturally take longer to draw than short ones.
    """
    minx, miny, view_w, view_h = kanji.view_box
    oversample = max(1, supersample)
    canvas = size * oversample
    # SVG user units -> oversized-canvas pixels.
    px_scale = (size / max(view_w, view_h)) * oversample
    offset = (minx, miny)

    width_px = (stroke_width or kanji.stroke_width) * px_scale
    colors = stroke_colors(len(kanji.strokes), palette)
    font = load_font(max(int(8 * px_scale), 8))
    frame_ms = max(int(round(1000 / fps)), 20)

    flattened = [polyline(stroke.path, px_scale, offset) for stroke in kanji.strokes]

    # `base` accumulates everything already finished; each animation frame is a
    # copy of it with the stroke currently being drawn painted on top.
    base = Image.new("RGB", (canvas, canvas), bg)
    base_draw = ImageDraw.Draw(base)

    if grid:
        draw_practice_grid(base_draw, canvas, oversample)

    if ghost:
        # Laid down first, so each coloured stroke paints over its own ghost as
        # it is drawn — the reader sees the whole character from frame one.
        for points, _ in flattened:
            draw_stroke(base_draw, points, ghost_color, width_px)

    def shrink(img: Image.Image) -> Image.Image:
        """Oversized canvas -> final size (this is the anti-aliasing step)."""
        return img.resize((size, size), Image.LANCZOS) if oversample > 1 else img.copy()

    def put_label(target: ImageDraw.ImageDraw, stroke: Stroke, color: str) -> None:
        """Draw a stroke's number, outlined in the background colour so it
        stays readable where it overlaps the ink."""
        if not numbers:
            return
        x = (stroke.label_xy[0] - minx) * px_scale
        y = (stroke.label_xy[1] - miny) * px_scale
        kwargs = dict(fill=color, font=font,
                      stroke_width=max(int(1.2 * oversample), 1), stroke_fill=bg)
        try:
            target.text((x, y), stroke.label, anchor="ls", **kwargs)
        except (ValueError, TypeError):  # bitmap fallback font: no anchors
            target.text((x, y - 8 * px_scale), stroke.label, **kwargs)

    frames: list[Image.Image] = []
    durations: list[int] = []

    for stroke, (points, lengths), color in zip(kanji.strokes, flattened, colors):
        stroke_units = lengths[-1] / px_scale
        n_frames = max(min_frames, int(math.ceil(stroke_units / max(speed, 0.1))))

        # Grow the stroke from nothing to its full length.
        for f in range(1, n_frames + 1):
            frame = base.copy()
            frame_draw = ImageDraw.Draw(frame)
            draw_stroke(frame_draw, points_upto(points, lengths, f / n_frames),
                        color, width_px)
            put_label(frame_draw, stroke, color)
            frames.append(shrink(frame))
            durations.append(frame_ms)

        # Bake the finished stroke into the background for the next round.
        draw_stroke(base_draw, points, color, width_px)
        put_label(base_draw, stroke, color)
        if pause > 0:
            frames.append(shrink(base))
            durations.append(max(int(pause * 1000), 20))

    # Linger on the completed character before the loop restarts.
    frames.append(shrink(base))
    durations.append(max(int(hold * 1000), 20))

    paletted = quantize_frames(frames, size)
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
    match = re.match(r"^/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$", parts.path)
    if not match:
        return url
    owner, repo, ref_and_path = match.groups()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref_and_path}"


def resolve_svg(source: str, cache_dir: Path) -> tuple[str, str]:
    """Accept a KanjiVG URL (GitHub web or raw) or a local .svg path.

    Returns (SVG text, base name) — the name becomes the output GIF's filename.
    """
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
    """Download an SVG, or return the cached copy if we already have it."""
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    import requests    # imported lazily: cached runs need no network stack

    resp = requests.get(url, timeout=30)
    if resp.status_code == 404:
        raise SystemExit(f"not in KanjiVG: {url}")
    resp.raise_for_status()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(resp.text, encoding="utf-8")
    return resp.text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a KanjiVG SVG as a stroke-order GIF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("source", nargs="+",
                        help="KanjiVG URL (GitHub web or raw), or a local .svg path")
    parser.add_argument("-o", "--output", type=Path, help="output file (single source only)")
    parser.add_argument("--outdir", type=Path, default=Path("."), help="directory for outputs")
    parser.add_argument("--size", type=int, default=400, help="output size in pixels")
    parser.add_argument("--fps", type=int, default=30, help="frames per second")
    parser.add_argument("--speed", type=float, default=2.2, help="SVG units drawn per frame")
    parser.add_argument("--min-frames", type=int, default=6, help="minimum frames per stroke")
    parser.add_argument("--pause", type=float, default=0.12, help="seconds held after each stroke")
    parser.add_argument("--hold", type=float, default=1.6,
                        help="seconds held on the finished kanji")
    parser.add_argument("--stroke-width", type=float, help="override the SVG stroke width")
    parser.add_argument("--palette", choices=["categorical", "rainbow", "mono"],
                        default="categorical")
    parser.add_argument("--bg", default="#ffffff", help="background colour")
    parser.add_argument("--no-numbers", action="store_true", help="hide stroke numbers")
    parser.add_argument("--grid", action="store_true", help="draw a practice grid")
    parser.add_argument("--no-ghost", action="store_true",
                        help="hide the light grey preview of upcoming strokes")
    parser.add_argument("--ghost-color", default="#e6e6e6", help="colour of the upcoming strokes")
    parser.add_argument("--supersample", type=int, default=3, help="anti-aliasing factor")
    parser.add_argument("--cache-dir", type=Path, default=Path(".kanjivg_cache"))
    args = parser.parse_args(argv)

    if args.output and len(args.source) > 1:
        parser.error("-o works with a single source; use --outdir for several")

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
