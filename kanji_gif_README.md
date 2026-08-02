# Kanji stroke-order GIFs

Turns a [KanjiVG](https://kanjivg.tagaini.net/) SVG into an animated stroke-order GIF,
drawing each stroke in its own colour with its stroke number. Strokes still to come
sit underneath in light grey, so the whole character is readable from the first frame.

## Setup

```bash
conda create -n kanji python=3.12
conda activate kanji
pip install -r requirements.txt
```

## Usage

Pass the KanjiVG file's URL — either the GitHub page URL or the raw one:

```bash
python kanji_gif.py https://github.com/KanjiVG/kanjivg/blob/master/kanji/080fd.svg
python kanji_gif.py https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/080fd.svg
python kanji_gif.py ./080fd.svg                 # a local file works too
python kanji_gif.py <url1> <url2> --outdir gifs # several at once
```

The filename in the URL is the character's Unicode codepoint in hex, so 能 (U+80FD)
lives at `kanji/080fd.svg`. Output lands in `--outdir` as `<codepoint>.gif`, or
wherever `-o` points. Downloads are cached in `.kanjivg_cache/`.

## Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--size` | 400 | Output size in pixels |
| `--fps` | 30 | Frames per second |
| `--speed` | 2.2 | SVG units drawn per frame — higher is faster |
| `--min-frames` | 6 | Floor on frames per stroke, so short strokes stay visible |
| `--pause` | 0.12 | Seconds held after each stroke |
| `--hold` | 1.6 | Seconds held on the finished kanji before looping |
| `--palette` | categorical | `categorical`, `rainbow`, or `mono` |
| `--stroke-width` | from SVG | Override the brush width (KanjiVG uses 3 units) |
| `--bg` | `#ffffff` | Background colour |
| `--grid` | off | Faint dashed practice grid |
| `--ghost-color` | `#e6e6e6` | Colour of the not-yet-drawn strokes |
| `--no-ghost` | — | Hide the preview of upcoming strokes entirely |
| `--no-numbers` | — | Hide the stroke numbers |
| `--supersample` | 3 | Anti-aliasing factor; 1 renders faster but jagged |

## How it works

- A `github.com/<owner>/<repo>/blob/<ref>/<path>` URL is rewritten to its
  `raw.githubusercontent.com` equivalent before fetching; `/raw/` URLs and
  already-raw URLs work as-is.
- Stroke order is the document order of `<path>` elements inside the
  `kvg:StrokePaths` group — that ordering is what KanjiVG guarantees.
- Number positions come from the `kvg:StrokeNumbers` group's `transform`
  matrices; if a file lacks them, they're placed just behind each stroke's start.
- Paths are flattened with `svgpathtools` into a polyline plus a cumulative
  arc-length table, so the brush tip advances at a constant speed regardless of
  how the curve is parameterised.
- Strokes are painted as overlapping discs, not PIL wide lines: both
  `ImageDraw.line` modes leave hairline notches at every vertex of a densely
  flattened path.
- Frames are rendered at `--supersample`× and downscaled with Lanczos for
  anti-aliasing, then quantised against one shared palette so the GIF's colours
  don't shift between frames. That palette is sampled from the first, middle and
  last frames — the ghost grey is painted over by the end, and a palette built
  from the final frame alone would snap it to the nearest unrelated colour.

KanjiVG is CC BY-SA 3.0 — attribute it if you publish the output.
