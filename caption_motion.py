"""Readable, estimated word-progress subtitles for FFmpeg/libass.

The timing starts from actual per-sentence audio duration, not speech recognition.
Word locations within each sentence are approximate, not forced alignment.
A standard editable SRT remains the ground-truth scene-level subtitle track.
"""
from __future__ import annotations

from pathlib import Path
import math
import re

MAX_LINES = 2
ASS_STYLE = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
             "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
             "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding")


def _timestamp(centiseconds: int) -> str:
    hrs, rem = divmod(max(0, centiseconds), 360000)
    mins, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hrs}:{mins:02}:{secs:02}.{cs:02}"


def _clean_word(word: str) -> str:
    # Prevent a subtitle from becoming executable ASS override markup.
    return re.sub(r"[\x00-\x1f\x7f]", "", word).replace("\\", "＼").replace("{", "（").replace("}", "）")


def _group_words(text: str, width: int, height: int) -> list[list[str]]:
    """Keep each phrase legible on 9:16, 16:9 and 1:1 videos."""
    words = [_clean_word(word) for word in text.split()]
    if not words:
        raise ValueError("Empty spoken sentence")
    # Horizontal videos are physically wider; still avoid huge captions.
    limit = 34 if width <= height else 48
    max_words = 6 if width <= height else 8
    groups: list[list[str]] = []
    current: list[str] = []
    for word in words:
        proposed = " ".join(current + [word])
        if current and (len(current) >= max_words or len(proposed) > limit):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def _word_weight(word: str) -> float:
    # Readable approximation; punctuation adds an estimated pause.
    chars = len(re.sub(r"\W", "", word, flags=re.UNICODE))
    weight = max(1.0, chars ** 0.65)
    if word.endswith((",", ";", ":", "…")):
        weight += 1.0
    if word.endswith((".", "!", "?")):
        weight += 1.7
    return weight


def _apportion(total_cs: int, weights: list[float]) -> list[int]:
    """Largest-remainder integer division, keeping each word nonzero."""
    if total_cs < len(weights) or not weights:
        raise ValueError("Spoken duration is too short for word effects")
    remainder = total_cs - len(weights)
    scale = sum(weights)
    values = [remainder * item / scale for item in weights]
    base = [math.floor(item) for item in values]
    fractions = sorted(range(len(values)), key=lambda i: (values[i] - base[i], -i), reverse=True)
    for index in fractions[:remainder - sum(base)]:
        base[index] += 1
    result = [item + 1 for item in base]
    assert sum(result) == total_cs
    return result


def _karaoke_fragments(words: list[str], times: list[int]) -> str:
    # \kf fills one word left-to-right; secondary -> primary colour.
    return " ".join(f"{{\\kf{amount}}}{word}" for word, amount in zip(words, times, strict=True))


def generate_karaoke_ass(segments: list[dict[str, object]], work: Path,
                         *, width: int, height: int) -> dict[str, object]:
    """Generate optional .ass for approximate karaoke, preserving SRT elsewhere."""
    if not 1 <= len(segments) <= 18 or width < 240 or height < 240:
        raise ValueError("Invalid caption dimensions or scene count")
    if width > 4096 or height > 4096:
        raise ValueError("Subtitle resolution too large")
    font_size = max(22, int(min(width / 15, height / 18)))
    margin_v = max(28, int(height * 0.17))
    margin_lr = max(18, int(width * 0.095))
    lines = [
        "[Script Info]", "Title: Free Studio estimated kinetic captions",
        "ScriptType: v4.00+", "WrapStyle: 2", "ScaledBorderAndShadow: yes",
        f"PlayResX: {width}", f"PlayResY: {height}", "",
        "[V4+ Styles]", ASS_STYLE,
        "Style: Kinetic,DejaVu Sans," + str(font_size) +
        ",&H00FFFFFF,&H0069F1D3,&H001A0C0A,&H9A020408,"
        "1,0,0,0,100,100,0,0,1,2,1,2,"
        + str(margin_lr) + "," + str(margin_lr) + "," + str(margin_v) + ",1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[dict[str, object]] = []
    previous_end = 0
    for scene_number, scene in enumerate(segments, start=1):
        start = float(scene["start"])
        end = float(scene["end"])
        if not all(map(math.isfinite, (start, end))) or start < -0.001 or end <= start:
            raise ValueError("Invalid narration scene timestamps")
        start_cs = round(start * 100)
        end_cs = round(end * 100)
        if start_cs < previous_end:
            raise ValueError("Overlapping caption scenes")
        groups = _group_words(str(scene["text"]), width, height)
        words = [word for group in groups for word in group]
        total_cs = end_cs - start_cs
        if total_cs < len(words):
            raise ValueError("Duration is too short for spoken word count")
        timing = _apportion(total_cs, [_word_weight(word) for word in words])
        offset = start_cs
        cursor = 0
        for group in groups:
            count = len(group)
            slots = timing[cursor:cursor + count]
            duration_cs = sum(slots)
            # Split at phrasing boundaries, keeping 100% of the narration interval.
            body = _karaoke_fragments(group, slots)
            lines.append(
                f"Dialogue: 0,{_timestamp(offset)},{_timestamp(offset+duration_cs)},"
                f"Kinetic,,0,0,0,,{{\\fad(60,80)}}{body}"
            )
            events.append({
                "scene": scene_number, "start": round(offset/100, 2),
                "end": round((offset+duration_cs)/100, 2),
                "word_count": count, "estimated": True,
            })
            offset += duration_cs
            cursor += count
        if offset != end_cs:
            raise AssertionError("Caption phrases do not cover the scene")
        previous_end = end_cs
    work.mkdir(parents=True, exist_ok=True)
    target = work / "captions.ass"
    target.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return {"path": str(target), "phrases": events,
            "estimated_word_timing": True, "timing_method": "length-weighted-per-sentence",
            "source": "audio-derived-scene-durations"}
