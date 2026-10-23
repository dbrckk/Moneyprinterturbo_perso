"""Regressions for non-blocking, bounded MP4 motion and scene pacing audit."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import motion_cadence_review as motion
import video_qa


def scenes(*durations: float) -> list[dict]:
    cursor = 0.0
    output = []
    for duration in durations:
        output.append({"start": cursor, "end": cursor + duration, "text": "Scene"})
        cursor += duration
    return output


def videos(*indices: int) -> list[dict]:
    return [{"segment": index, "kind": "video", "source": "local"}
            for index in indices]


def encode(*argv: str) -> None:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *argv],
        capture_output=True, timeout=90)
    if process.returncode:
        raise AssertionError(process.stderr[-1600:])


class MotionCadenceUnitTests(unittest.TestCase):
    def test_all_animated_photos_and_missing_sources_skip_decode(self):
        with patch.object(motion.subprocess, "run", side_effect=AssertionError("No decode")):
            result = motion.inspect_encoded_motion_cadence(
                Path("not-real.mp4"), scenes(5, 6),
                [{"segment": 1, "kind": "image"}])
        self.assertEqual(result["status"], "no-eligible-video-scenes")
        self.assertEqual(result["decoded_frames"], 0)
        self.assertEqual(result["video_scenes_sampled"], 0)

    def test_bounded_duration_skips_expensive_video_decode(self):
        with patch.object(motion.subprocess, "run", side_effect=AssertionError("No decode")):
            report = motion.inspect_encoded_motion_cadence(
                Path("not-real.mp4"), scenes(40, 40), videos(1, 2))
        self.assertEqual(report["status"], "skipped-duration-budget")

    def test_reports_short_and_long_pacing_without_claiming_quality_failure(self):
        short = motion.inspect_encoded_motion_cadence(
            Path("none.mp4"), scenes(1, 1, 1, 3), [])
        self.assertEqual(short["cadence"]["short_scenes"], 3)
        self.assertTrue(any("under 1.25s" in text for text in short["warnings"]))
        long = motion.inspect_encoded_motion_cadence(
            Path("none.mp4"), scenes(10, 10, 2), [])
        self.assertEqual(long["cadence"]["long_scenes"], 2)
        self.assertTrue(any("exceed 9s" in text for text in long["warnings"]))

    def test_invalid_scenes_fail_without_decoding_or_modifying_files(self):
        invalid = (
            [],
            [{"start": 0, "end": float("nan")}],
            [{"start": 0, "end": 3}, {"start": 3.5, "end": 5}],
            [{"start": 0, "end": -1}],
            [{"start": 0, "end": 1}] * 19,
        )
        with patch.object(motion.subprocess, "run", side_effect=AssertionError("No decode")):
            for entries in invalid:
                with self.subTest(entries=entries), self.assertRaises(ValueError):
                    motion.inspect_encoded_motion_cadence(
                        Path("none.mp4"), entries, videos(1))

    def test_sample_errors_are_nonfatal_advisory_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "invalid.mp4"
            file.write_bytes(b"not a video")
            with patch.object(motion.subprocess, "run") as mocked:
                mocked.return_value.stdout = b"bad-size"
                report = motion.inspect_encoded_motion_cadence(
                    file, scenes(5.0, 5.0), videos(1, 2))
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(report["status"], "unavailable")
            self.assertFalse(report["warnings"])

    def test_motion_uses_center_image_not_subtitle_or_top_banner(self):
        original = bytes([99] * (48 * 27))
        update = bytearray(original)
        # Top 8 and bottom 9 rows are excluded from video motion.
        update[:48 * 8] = bytes([220] * (48 * 8))
        update[48 * 18:] = bytes([230] * (48 * 9))
        self.assertEqual(motion._central_motion(original, bytes(update)), 0)
        update[48 * 11] = 150
        self.assertGreater(motion._central_motion(original, bytes(update)), 0)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg/FFprobe encoded H.264 checks required")
class MotionCadenceEncodedVideoTests(unittest.TestCase):
    def test_real_export_static_and_moving_scenes_audit_stays_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            movie = root / "mixed.mp4"
            encode("-f", "lavfi", "-i", "color=c=gray:s=160x288:r=12:d=5",
                   "-f", "lavfi", "-i", "testsrc2=s=160x288:r=12:d=5",
                   "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                   "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast",
                   "-pix_fmt", "yuv420p", str(movie))
            original = movie.read_bytes()
            segments = scenes(5.0, 5.0)
            report = motion.inspect_encoded_motion_cadence(
                movie, segments, videos(1, 2))
            self.assertEqual(report["status"], "analyzed", report)
            self.assertEqual(report["video_scenes_sampled"], 2)
            still, moving = report["scenes"]
            self.assertTrue(still["very_low_motion"], still)
            self.assertFalse(moving["very_low_motion"], moving)
            self.assertLess(still["median_motion_luma"], 1.25)
            self.assertGreater(moving["median_motion_luma"],
                               still["median_motion_luma"] + 1.0)
            self.assertTrue(any("Scene 1:" in warning
                                for warning in report["warnings"]))
            self.assertFalse(any("Scene 2:" in warning
                                 for warning in report["warnings"]))

            metadata = {"duration_seconds": 10.0, "size": [160, 288],
                        "speech": False, "segments": segments,
                        "media": videos(1, 2), "scene_transitions": ["cut"]}
            segments[0]["text"] = "First scene."
            segments[1]["text"] = "Second scene."
            movie.with_suffix(".json").write_text(
                json.dumps(metadata), encoding="utf-8")
            movie.with_suffix(".srt").write_text(
                "1\n00:00:00,000 --> 00:00:05,000\nFirst scene.\n\n"
                "2\n00:00:05,000 --> 00:00:10,000\nSecond scene.\n",
                encoding="utf-8")
            checked = video_qa.audit(movie)
            self.assertTrue(checked["ok"], checked["errors"])
            self.assertEqual(checked["motion_cadence_review"]["status"], "analyzed")
            self.assertEqual(len(checked["motion_cadence_review"]["scenes"]), 2)
            self.assertTrue(json.loads(movie.with_suffix(".quality.json").read_text())
                            ["motion_cadence_review"]["scenes"][0]["very_low_motion"])
            self.assertEqual(movie.read_bytes(), original,
                             "Audit must never modify delivered MP4")


if __name__ == "__main__":
    unittest.main()
