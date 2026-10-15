"""FFmpeg cinematic motion graphics: deterministic, local and license-free."""
from __future__ import annotations
from pathlib import Path
import math
import textwrap

GRADIENTS = (
    ("0x06101F", "0x174355", "0x0B253B"),
    ("0x090D24", "0x34335B", "0x152A45"),
    ("0x071821", "0x164944", "0x0B2834"),
    ("0x100E22", "0x4B2D49", "0x202344"),
)
ACCENT = "0x69F1D3"


def gradient_source(index: int, width: int, height: int, fps: int) -> str:
    """Slow animated backdrop rather than static generic color cards."""
    if not (0 <= index < 18 and width >= 2 and height >= 2 and 1 <= fps <= 60):
        raise ValueError("Invalid gradient parameters")
    c0, c1, c2 = GRADIENTS[index % len(GRADIENTS)]
    return (f"gradients=s={width}x{height}:r={fps}:c0={c0}:c1={c1}:c2={c2}:"
            "n=3:speed=0.008:type=radial")


def _fade_alpha(start: float, end: float) -> str:
    fade = min(0.32, max(0.12, (end - start) * .28))
    return (f"min(1\\,max(0\\,(t-{start:.3f})/{fade:.3f}))"
            f"*min(1\\,max(0\\,({end:.3f}-t)/{fade:.3f}))")


def _heading(text: str, width: int, height: int) -> str:
    words = text.rstrip(".!?… ").split()
    headline = " ".join(words[:11])
    size = max(20, int(min(width * .078, height * .11)))
    max_chars = max(11, min(29, int(width * .81 / (size * .52))))
    lines = 2 if width > height else 3
    return "\n".join(textwrap.wrap(
        headline, width=max_chars, break_long_words=False,
        break_on_hyphens=False)[:lines])


def build_filters(segments: list[dict[str, object]], media_slots: list[object | None],
                  work: Path, *, width: int, height: int, total: float,
                  style: str = "cinematic") -> list[str]:
    """Clean-safe-zone titles, scene counters and timeline progress."""
    if style not in ("cinematic", "classic"):
        raise ValueError("Unknown visual style")
    if not segments or len(segments) != len(media_slots) or not math.isfinite(total) or total <= 0:
        raise ValueError("Invalid storyboard")
    if style == "classic":
        return classic_filters(segments, media_slots, work, width=width, height=height)

    count = len(segments)
    margin = int(width * .086)
    pad = max(5, width // 90)
    header_y = int(height * .085)
    progress_y = int(height * .143)
    bar_width = width - 2 * margin
    track_height = max(2, width // 155)
    header_size = max(11, width // 31)
    scene_size = max(10, width // 34)
    (work / "brand.txt").write_text("FREE  /  STUDIO", encoding="utf-8")
    filters = [
        "format=yuv420p", "vignette=angle=PI/5:mode=forward",
        f"drawbox=x=0:y={int(height*.665)}:w=iw:h={int(height*.24)}:color=0x041020@0.40:t=fill",
        f"drawbox=x={margin}:y={progress_y}:w={bar_width}:h={track_height}:color=white@0.16:t=fill",
        "drawtext=textfile=brand.txt:font=DejaVu Sans:"
        f"fontcolor=white@0.82:fontsize={header_size}:x={margin}:y={header_y}",
    ]
    for i, segment in enumerate(segments):
        start, end = float(segment["start"]), float(segment["end"])
        if not (0 <= start < end <= total + .02):
            raise ValueError("Invalid scene times")
        enabled = f"between(t,{start:.3f},{end:.3f})"
        alpha = _fade_alpha(start, end)
        filled = max(2, round(bar_width * (i + 1) / count))
        filters.append(f"drawbox=x={margin}:y={progress_y}:w={filled}:h={track_height}:"
                       f"color={ACCENT}:t=fill:enable='{enabled}'")
        (work / f"counter_{i}.txt").write_text(f"{i+1:02}  /  {count:02}", encoding="utf-8")
        filters.append(
            f"drawtext=textfile=counter_{i}.txt:font=DejaVu Sans:fontcolor=white@0.89:"
            f"fontsize={scene_size}:x=w-{margin}-text_w:y={header_y}:enable='{enabled}'")
        if media_slots[i] is not None:
            continue
        (work / f"chapter_{i}.txt").write_text(f"CHAPTER  {i+1:02}", encoding="utf-8")
        (work / f"heading_{i}.txt").write_text(
            _heading(str(segment["text"]), width, height), encoding="utf-8")
        caption_size = max(20, int(min(width * .078, height * .11)))
        y_label = int(height * (.292 if width > height else .325))
        y_heading = int(height * (.355 if width > height else .398))
        filters.extend([
            f"drawbox=x={margin}:y={y_heading}:w={pad}:h={int(height*.17)}:"
            f"color={ACCENT}@0.92:t=fill:enable='{enabled}'",
            f"drawtext=textfile=chapter_{i}.txt:font=DejaVu Sans:"
            f"fontcolor={ACCENT}:fontsize={scene_size}:x={margin+pad*3}:y={y_label}:"
            f"alpha='{alpha}':enable='{enabled}'",
            f"drawtext=textfile=heading_{i}.txt:font=DejaVu Sans:"
            f"fontcolor=white:fontsize={caption_size}:line_spacing={max(7,width//65)}:"
            f"x={margin+pad*3}+max(0\\,({start:.3f}+0.35-t)*60):y={y_heading}:"
            f"alpha='{alpha}':enable='{enabled}'",
        ])
    filters.append("fade=t=in:st=0:d=0.18")
    if total > 1:
        filters.append(f"fade=t=out:st={max(0,total-.18):.3f}:d=0.18")
    return filters


def classic_filters(segments: list[dict[str, object]], media_slots: list[object | None],
                    work: Path, *, width: int, height: int) -> list[str]:
    """Backwards-compatible basic typography, without premium overlays."""
    fs = ["format=yuv420p", "drawgrid=w=110:h=110:t=1:color=white@0.025",
          "drawbox=x=0:y=ih*0.68:w=iw:h=ih*0.23:color=0x040915@0.20:t=fill",
          f"drawbox=x=0:y=0:w=iw:h={max(5,height//115)}:color={ACCENT}:t=fill"]
    (work / "brand.txt").write_text("MONEYPRINTER  /  FREE STUDIO", encoding="utf-8")
    fs.append(f"drawtext=textfile=brand.txt:fontcolor=white:fontsize={max(10,width//45)}:"
              "x=(w-text_w)/2:y=h*0.12")
    for i, segment in enumerate(segments):
        if media_slots[i] is not None:
            continue
        start, end = float(segment["start"]), float(segment["end"])
        heading = " ".join(str(segment["text"]).rstrip(".!?…").split()[:8])
        size = max(17, width // 20)
        line_width = max(11, int(width * .74 / (size * .60)))
        (work / f"heading_{i}.txt").write_text(
            "\n".join(textwrap.wrap(heading, width=line_width,
                                    break_long_words=True, break_on_hyphens=False)[:4]),
            encoding="utf-8")
        fs.append(f"drawtext=textfile=heading_{i}.txt:fontcolor=white:fontsize={size}:"
                  f"line_spacing={max(5,size//3)}:x=(w-text_w)/2:y=h*0.38:"
                  f"enable='between(t,{start},{end})'")
    return fs
