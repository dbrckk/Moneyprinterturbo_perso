"""Verified repair: unchanged scene pixels and frame count on delivered MP4."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from visual_repair import (
    encoded_duration_ok, unchanged_scene_integrity, sample_encoded_rgb,
)


_FRAME_SIZE = 48 * 27 * 3


def ffmpeg(*args: str) -> None:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, timeout=60)
    if process.returncode:
        raise AssertionError(process.stderr[-1200:])


def timeline(count: int, seconds: float = 2.6) -> list[dict]:
    return [dict(start=i * seconds, end=(i + 1) * seconds)
            for i in range(count)]


class SceneIntegrityUnitTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Path("old.mp4"), Path("candidate.mp4")
        self.flat = bytes((120, 121, 119)) * (48 * 27)

    def test_strict_maximum_four_stable_scene_samples(self):
        times = []
        def probe(path, t):
            times.append((path, t))
            return self.flat
        with patch("visual_repair.sample_encoded_rgb", side_effect=probe):
            result = unchanged_scene_integrity(
                self.a, self.b, timeline(8), 3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sampled_frames"], 4)
        self.assertEqual(result["worst_mae"], 0)
        self.assertEqual(len(times), 8)
        self.assertEqual([t for path,t in times if path == self.a],
                         [1.3, 9.1, 11.7, 19.5])

    def test_minor_compression_noise_passes_but_overlay_difference_fails(self):
        small_change = bytes((124, 125, 123)) * (48 * 27)
        changed = bytearray(self.flat)
        # 10% pixels with completely different overlay (e.g. lost caption)
        for pixel in range(150):
            i = pixel * 3
            changed[i:i+3] = bytes((245, 245, 245))
        for next_frame, good in ((small_change, True),
                                 (bytes(changed), False),
                                 (bytes((240, 240, 240)) * (48 * 27), False)):
            with self.subTest(good=good), patch(
                "visual_repair.sample_encoded_rgb",
                side_effect=lambda path, timestamp:
                    self.flat if path == self.a else next_frame
            ):
                result = unchanged_scene_integrity(
                    self.a, self.b, timeline(2), 0)
            self.assertEqual(result["ok"], good, result)
            if not good:
                self.assertGreater(result["worst_mae"], 7)

    def test_missing_or_corrupt_samples_cannot_allow_new_video(self):
        for value in (None,):
            with patch("visual_repair.sample_encoded_rgb", return_value=value):
                result = unchanged_scene_integrity(
                    self.a, self.b, timeline(2), 0)
            self.assertFalse(result["ok"])
            self.assertEqual(result["sampled_frames"], 0)
        for spans, boundary in (([], 0), (timeline(9), 1),
                                (timeline(2), -1), (timeline(2), 1),
                                ([dict(start=0, end=0.5),
                                  dict(start=.5, end=1.0)], 0)):
            with self.subTest(spans=spans, boundary=boundary):
                self.assertFalse(unchanged_scene_integrity(
                    self.a, self.b, spans, boundary)["ok"])
        self.assertFalse(unchanged_scene_integrity(
            self.a, self.a, timeline(2), 0)["ok"])

    def test_mp4_with_false_frame_count_is_rejected_despite_good_durations(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "metadata.mp4"
            fake.write_bytes(b"x" * 3000)
            def encoded(frames, fps):
                return json.dumps({"format": {"duration": "3.0"},
                    "streams": [{"codec_type": "video",
                                 "codec_name": "h264",
                                 "width": 360, "height": 640,
                                 "duration": "3.0",
                                 "nb_frames": str(frames),
                                 "avg_frame_rate": fps}]})
            for count, rate, valid in ((36,"12/1",True),
                                       (26,"12/1",False),
                                       (36,"30/1",False),
                                       (36,"0/0",False),
                                       (36,"NaN",False)):
                with self.subTest(count=count,rate=rate), patch(
                    "visual_repair.subprocess.run",
                    return_value=Mock(stdout=encoded(count, rate))
                ):
                    self.assertEqual(
                        encoded_duration_ok(fake, expected=3.0, fps=12),
                        valid)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg encode/decoded-pixel checks")
class SceneIntegrityRealMP4Tests(unittest.TestCase):
    def test_ffprobe_cfr_frame_count_and_exact_stable_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original.mp4"
            copied = root / "identical.mp4"
            wrong_fps = root / "wrong-fps.mp4"
            modified = root / "different-overlay.mp4"
            ffmpeg("-f", "lavfi", "-i",
                   "color=c=0x787878:s=180x320:r=12:d=5.2",
                   "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   str(original))
            shutil.copyfile(original, copied)
            ffmpeg("-f", "lavfi", "-i",
                   "color=c=0x787878:s=180x320:r=24:d=5.2",
                   "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   str(wrong_fps))
            ffmpeg("-f", "lavfi", "-i",
                   "color=c=0x787878:s=180x320:r=12:d=5.2",
                   "-vf", "drawbox=x=0:y=0:w=iw:h=ih/3:color=white:t=fill",
                   "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   str(modified))
            self.assertTrue(encoded_duration_ok(
                original, expected=5.2, fps=12))
            self.assertFalse(encoded_duration_ok(
                wrong_fps, expected=5.2, fps=12))
            self.assertTrue(encoded_duration_ok(
                copied, expected=5.2, fps=12, reference=original))
            self.assertEqual(len(sample_encoded_rgb(original, 1.3)), _FRAME_SIZE)
            self.assertTrue(unchanged_scene_integrity(
                original, copied, timeline(2), 0)["ok"])
            different = unchanged_scene_integrity(
                original, modified, timeline(2), 0)
            self.assertFalse(different["ok"])
            self.assertGreater(different["worst_mae"], 7)
            self.assertIsNone(sample_encoded_rgb(original, float("nan")))


if __name__ == "__main__":
    unittest.main()
