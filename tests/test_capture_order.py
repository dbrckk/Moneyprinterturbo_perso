"""Evidence-based local chronology: real MP4 tags and JPEG EXIF, no cloud."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import capture_order
import media_assets


def jpeg_with_exif(shot: str, offset: str | None = None) -> bytes:
    """Minimal APP1 EXIF for checking a bounded pure-Python TIFF reader."""
    raw = bytearray(160)
    raw[0:2] = b"II"
    struct.pack_into("<H", raw, 2, 42)
    struct.pack_into("<I", raw, 4, 8)
    struct.pack_into("<H", raw, 8, 1)
    struct.pack_into("<HHII", raw, 10, 0x8769, 4, 1, 26)
    count = 2 if offset else 1
    struct.pack_into("<H", raw, 26, count)
    struct.pack_into("<HHII", raw, 28, 0x9003, 2, 20, 64)
    raw[64:84] = (shot + "\0").encode("ascii")
    if offset:
        struct.pack_into("<HHII", raw, 40, 0x9011, 2, 7, 88)
        raw[88:95] = (offset + "\0").encode("ascii")
    payload = b"Exif\0\0" + bytes(raw)
    return b"\xff\xd8\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload + b"\xff\xd9"


class EmbeddedChronologyTests(unittest.TestCase):
    def test_jpeg_exif_local_and_explicit_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "photo.jpg"
            p.write_bytes(jpeg_with_exif("2024:07:15 15:45:03"))
            stamp = capture_order.capture_time(p)
            self.assertEqual(stamp, datetime(2024, 7, 15, 15, 45, 3))
            self.assertIsNone(stamp.utcoffset())
            p.write_bytes(jpeg_with_exif("2024:07:15 15:45:03", "+02:00"))
            stamp = capture_order.capture_time(p)
            self.assertEqual(stamp.isoformat(), "2024-07-15T15:45:03+02:00")

    def test_malformed_exif_and_impossible_clock_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "photo.jpg"
            for payload in (b"", b"JPEG", b"\xff\xd8\xff\xda",
                            jpeg_with_exif("2024:99:15 15:45:03"),
                            jpeg_with_exif("1904:01:01 00:00:00")):
                p.write_bytes(payload)
                self.assertIsNone(capture_order.capture_time(p))

    def test_jpeg_mixed_timezone_fallback_and_missing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            a, b = folder / "shot1.jpg", folder / "shot2.jpg"
            a.write_bytes(jpeg_with_exif("2024:08:01 12:00:00", "+02:00"))
            b.write_bytes(jpeg_with_exif("2024:07:01 12:00:00"))
            result, mode = capture_order.order_media_by_capture(
                [a, b], fallback_key=media_assets._local_media_order)
            self.assertEqual(mode, "filename")
            self.assertEqual([p.name for p in result], ["shot1.jpg", "shot2.jpg"])
            b.write_bytes(b"no capture date")
            result, mode = capture_order.order_media_by_capture(
                [a, b], fallback_key=media_assets._local_media_order)
            self.assertEqual(mode, "filename")

    def test_real_jpeg_exif_reorders_validated_local_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first, second = folder / "shot1.jpg", folder / "shot2.jpg"
            first.write_bytes(jpeg_with_exif("2024:08:01 12:00:00"))
            second.write_bytes(jpeg_with_exif("2024:07:01 12:00:00"))
            with patch.object(media_assets, "media_dimensions", return_value=(640, 480)):
                clips, warnings = media_assets.local_assets(folder)
            self.assertEqual(warnings, [])
            self.assertEqual([clip.title for clip in clips], ["shot2.jpg", "shot1.jpg"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "real FFmpeg and FFprobe required")
    def test_real_encoded_mp4_capture_dates_reorder_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for filename, timestamp in [
                    ("clip1.mp4", "2024-09-15T12:00:00Z"),
                    ("clip2.mp4", "2024-09-01T12:00:00Z")]:
                dest = folder / filename
                process = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i", "color=c=blue:s=192x128:r=10:d=0.5",
                     "-frames:v", "5", "-metadata", "creation_time=" + timestamp,
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)],
                    capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 0, process.stderr[-500:])
            stamp = capture_order.capture_time(folder / "clip1.mp4")
            self.assertEqual(stamp.isoformat(), "2024-09-15T12:00:00+00:00")
            media, notes = media_assets.local_assets(folder)
            self.assertEqual(notes, [])
            self.assertEqual([clip.title for clip in media], ["clip2.mp4", "clip1.mp4"])

    def test_unsupported_video_and_same_date_do_not_fake_capture_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            a, b = folder / "clip1.mp4", folder / "clip2.mp4"
            a.write_bytes(b"not valid video")
            b.write_bytes(b"also invalid video")
            self.assertIsNone(capture_order.capture_time(a))
            self.assertIsNone(capture_order.capture_time(b))
            with patch.object(capture_order, "capture_time",
                              return_value=datetime(2024, 1, 1)):
                order, kind = capture_order.order_media_by_capture(
                    [b, a], fallback_key=media_assets._local_media_order)
            self.assertEqual(kind, "filename")
            self.assertEqual(order, [a, b])


if __name__ == "__main__":
    unittest.main()
