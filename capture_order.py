"""Conservative capture-time ordering for user-owned phone videos/photos.

This module never guesses filming order from modification times. It recognizes
embedded video creation tags and JPEG EXIF DateTimeOriginal, and uses capture
chronology *only when every candidate has comparable timestamps*.
Missing dates, mixed timezone conventions, or tied capture dates keep the
existing human-friendly filename order.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import struct
import subprocess
from typing import Callable, Iterable

_VIDEO = frozenset((".mp4", ".mov", ".mkv", ".webm"))
_JPEG = frozenset((".jpg", ".jpeg"))
_MAX_JPEG_HEADER = 1024 * 1024
_ISO = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)
_EXIF = re.compile(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$")
_OFFSET = re.compile(r"^[+-](?:0\d|1[0-4]):[0-5]\d$")


def _valid_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if _EXIF.fullmatch(value):
            stamp = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
        elif _ISO.fullmatch(value):
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        # FFmpeg/QuickTime can emit an uninitialized 1904 epoch. Reject it,
        # and obvious future clock errors, rather than manufacturing a timeline.
        if not 1970 <= stamp.year <= 2100:
            return None
        return stamp
    except ValueError:
        return None


def _video_capture_time(path: Path) -> datetime | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format_tags=creation_time,com.apple.quicktime.creationdate:"
             "stream_tags=creation_time,com.apple.quicktime.creationdate",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=12,
        )
        metadata = json.loads(result.stdout)
        if not isinstance(metadata, dict):
            return None
        sources = [metadata.get("format", {}).get("tags", {})]
        sources.extend(s.get("tags", {}) for s in metadata.get("streams", [])
                       if isinstance(s, dict))
        for tags in sources:
            if not isinstance(tags, dict):
                continue
            for key in ("com.apple.quicktime.creationdate", "creation_time"):
                parsed = _valid_datetime(tags.get(key))
                if parsed is not None:
                    return parsed
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        pass
    return None


def _tiff_capture_time(data: bytes) -> datetime | None:
    """Parse only bounded TIFF IFD fields; never decode pixels or GPS data."""
    if len(data) < 8 or data[:2] not in (b"II", b"MM"):
        return None
    byteorder = "<" if data[:2] == b"II" else ">"

    def uint16(offset: int) -> int:
        if offset < 0 or offset + 2 > len(data):
            raise ValueError("truncated TIFF field")
        return struct.unpack_from(byteorder + "H", data, offset)[0]

    def uint32(offset: int) -> int:
        if offset < 0 or offset + 4 > len(data):
            raise ValueError("truncated TIFF field")
        return struct.unpack_from(byteorder + "I", data, offset)[0]

    def ifd(offset: int) -> dict[int, bytes]:
        count = uint16(offset)
        if count > 256 or offset + 2 + count * 12 + 4 > len(data):
            raise ValueError("invalid TIFF directory")
        fields = {}
        for i in range(count):
            pos = offset + 2 + i * 12
            tag, kind = uint16(pos), uint16(pos + 2)
            units = uint32(pos + 4)
            if kind == 2 and 1 <= units <= 128:
                start = pos + 8 if units <= 4 else uint32(pos + 8)
                if start + units <= len(data):
                    fields[tag] = data[start:start + units].split(b"\0", 1)[0]
            elif kind == 4 and units == 1:
                fields[tag] = data[pos + 8:pos + 12]
        return fields

    try:
        if uint16(2) != 42:
            return None
        root = ifd(uint32(4))
        exif_pointer = root.get(0x8769)
        if exif_pointer is not None:
            extra = ifd(struct.unpack(byteorder + "I", exif_pointer)[0])
            raw_date = extra.get(0x9003) or extra.get(0x9004)
            date = _valid_datetime(raw_date.decode("ascii") if raw_date else None)
            if date is not None:
                raw_offset = extra.get(0x9011) or extra.get(0x9010)
                if raw_offset:
                    offset = raw_offset.decode("ascii")
                    if _OFFSET.fullmatch(offset):
                        return datetime.fromisoformat(date.isoformat() + offset)
                return date
        raw = root.get(0x0132)
        return _valid_datetime(raw.decode("ascii") if raw else None)
    except (ValueError, UnicodeDecodeError, struct.error, OverflowError):
        return None


def _jpeg_capture_time(path: Path) -> datetime | None:
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"\xff\xd8":
                return None
            scanned = 2
            while scanned < _MAX_JPEG_HEADER:
                marker = stream.read(2)
                scanned += 2
                if len(marker) != 2 or marker[0] != 0xff or marker[1] in (0xda, 0xd9):
                    break
                # Standalone markers have no segment length.
                if marker[1] in (0x01,) or 0xd0 <= marker[1] <= 0xd7:
                    continue
                raw_length = stream.read(2)
                scanned += 2
                if len(raw_length) != 2:
                    break
                length = int.from_bytes(raw_length, "big")
                if length < 2 or scanned + length - 2 > _MAX_JPEG_HEADER:
                    break
                segment = stream.read(length - 2)
                scanned += len(segment)
                if len(segment) != length - 2:
                    break
                if marker[1] == 0xe1 and segment.startswith(b"Exif\0\0"):
                    return _tiff_capture_time(segment[6:])
    except (OSError, ValueError):
        pass
    return None


def capture_time(path: Path) -> datetime | None:
    extension = path.suffix.lower()
    if extension in _VIDEO:
        return _video_capture_time(path)
    if extension in _JPEG:
        return _jpeg_capture_time(path)
    return None


def order_media_by_capture(
    paths: Iterable[Path], *, fallback_key: Callable[[Path], tuple],
) -> tuple[list[Path], str]:
    """Choose capture-time order only if all dates exist and are comparable.

    Naive EXIF local-wall timestamps cannot be safely interleaved with UTC
    video tags. If the timezones differ in awareness, retain supplied order.
    """
    original = sorted(paths, key=fallback_key)
    if len(original) < 2:
        return original, "filename"
    stamped = [(path, capture_time(path)) for path in original]
    if any(stamp is None for _, stamp in stamped):
        return original, "filename"
    awareness = {stamp.utcoffset() is not None for _, stamp in stamped}
    if len(awareness) != 1:
        return original, "filename"
    # Same-instant files have no trustworthy chronological distinction.
    if len({stamp for _, stamp in stamped}) < 2:
        return original, "filename"
    return [path for path, _ in sorted(stamped, key=lambda pair:
                                       (pair[1], fallback_key(pair[0])))], "capture"
