"""Offline encoder comparison must be bounded, objective and non-destructive."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import encoder_profile_benchmark as bench


class EncoderProfileUnitTests(unittest.TestCase):
    def test_resolution_fps_and_duration_budget(self):
        for resolution, fps, seconds in ((360, 12, 2), (360, 30, 4),
                                         (720, 24, 4)):
            self.assertTrue(bench.config_ok(resolution, fps, seconds))
        for resolution, fps, seconds in ((1080, 12, 3), (720, 30, 4),
                                         (360, 60, 3), (360, 12, 9),
                                         (360, 12, 0), (True, 12, 2),
                                         (360, True, 2)):
            self.assertFalse(bench.config_ok(resolution, fps, seconds))
        with patch.object(bench.subprocess, "run", side_effect=AssertionError(
            "Malformed request must not invoke FFmpeg")):
            with self.assertRaises(ValueError):
                bench.run_benchmark(Path("ignored"), resolution=1080)

    def test_invalid_probe_fps_or_frame_count_is_not_a_benchmark_result(self):
        info = dict(streams=[dict(width=360, height=640, codec_name="h264",
                                  nb_frames="24", avg_frame_rate="12/1")])
        with patch.object(bench, "_execute", return_value=Mock(
                stdout=json.dumps(info))):
            bench._video_probe(Path("fake.mp4"), width=360, height=640,
                               fps=12, frames=24, codec="h264")
            for kwargs in (dict(width=720, height=640, fps=12,
                                frames=24, codec="h264"),
                           dict(width=360, height=640, fps=24,
                                frames=24, codec="h264"),
                           dict(width=360, height=640, fps=12,
                                frames=23, codec="h264")):
                with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                    bench._video_probe(Path("fake.mp4"), **kwargs)
        malformed = dict(streams=[dict(width=360, height=640,
                                       codec_name="h264", nb_frames="N/A",
                                       avg_frame_rate="0/0")])
        with patch.object(bench, "_execute", return_value=Mock(
                stdout=json.dumps(malformed))):
            with self.assertRaises(RuntimeError):
                bench._video_probe(Path("fake.mp4"), width=360, height=640,
                                   fps=12, frames=24, codec="h264")

    def test_ssim_invalid_or_unavailable_is_rejected_without_fabrication(self):
        for stderr, expected in (("[Parsed_ssim_0] SSIM Y:0.96 U:0.99 All:0.975563 (16.3)", 0.975563),
                                 ("SSIM All:1.000000", 1.0)):
            with patch.object(bench, "_execute", return_value=Mock(stderr=stderr)):
                self.assertEqual(bench._ssim(Path("ref"), Path("test")), expected)
        for stderr in ("FFmpeg built without SSIM", "SSIM All:1.2", "SSIM All:NaN"):
            with patch.object(bench, "_execute", return_value=Mock(stderr=stderr)):
                with self.assertRaises(RuntimeError):
                    bench._ssim(Path("ref"), Path("test"))

    def test_pareto_is_empirical_and_does_not_select_a_production_profile(self):
        profiles = [dict(name="current", mean_ssim=0.95, total_size_bytes=10000),
                    dict(name="crisper", mean_ssim=0.98, total_size_bytes=14000),
                    dict(name="compact", mean_ssim=0.94, total_size_bytes=13000)]
        bench._pareto_profiles(profiles)
        self.assertTrue(profiles[0]["sample_pareto"])
        self.assertTrue(profiles[1]["sample_pareto"])
        self.assertFalse(profiles[2]["sample_pareto"])

    def test_html_never_interprets_report_strings_as_markup(self):
        report = dict(resolution=360, fps=12, seconds=2,
                      machine='<img src="https://not-allowed.example/">',
                      profiles=[dict(name="current<script>alert(1)</script>",
                                     preset="veryfast", crf=23,
                                     mean_ssim=0.945, total_size_bytes=12345,
                                     total_wall_seconds=2.32, sample_pareto=True)],
                      scenarios=[dict(label="<script>bad</script>", encodes=[
                          dict(name="current", ssim=0.945,
                               encoded_size_bytes=12345,
                               video="videos/test.mp4")])])
        markup = bench.render_html(report)
        self.assertIn("&lt;script&gt;", markup)
        self.assertNotIn("<script>", markup)
        self.assertIn("default-src 'none'", markup)
        self.assertIn('src="videos/test.mp4"', markup)
        self.assertIn("GitHub ≠ Android", markup)
        self.assertIn("0.94500", markup)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg and FFprobe required for real encoded MP4 A/B")
class EncoderProfileRealFFmpegTests(unittest.TestCase):
    def test_previous_complete_benchmark_survives_failed_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result"
            first = bench.run_benchmark(output, resolution=360, fps=12,
                                        seconds=2)
            before = {p.relative_to(output).as_posix(): p.read_bytes()
                      for p in output.rglob("*") if p.is_file()}
            with patch.object(bench, "_ssim", side_effect=RuntimeError(
                    "Filter unavailable during retry")):
                with self.assertRaises(RuntimeError):
                    bench.run_benchmark(output, resolution=360, fps=12,
                                        seconds=2)
            after = {p.relative_to(output).as_posix(): p.read_bytes()
                     for p in output.rglob("*") if p.is_file()}
            self.assertEqual(after, before)
            second = bench.run_benchmark(output, resolution=360, fps=12,
                                         seconds=2)
            self.assertEqual(second["description"], first["description"])
            self.assertEqual(len(list(output.glob("videos/*.mp4"))), 8)
            other = Path(folder) / "unrelated"
            other.mkdir()
            (other / "user-file.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                bench.run_benchmark(other, resolution=360, fps=12,
                                    seconds=2)
            self.assertEqual((other / "user-file.txt").read_text(), "keep")

    def test_real_ffv1_reference_and_four_h264_profiles_two_scenarios(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "experiment"
            result = bench.run_benchmark(output, resolution=360, fps=12,
                                         seconds=2)
            saved = json.loads((output / "metrics.json").read_text())
            self.assertEqual(saved, result)
            self.assertEqual(result["production_profile_unchanged"], True)
            self.assertEqual(result["reference_codec"], "ffv1-yuv420p-lossless")
            self.assertEqual(len(result["scenarios"]), 2)
            self.assertEqual(len(result["profiles"]), 4)
            rendered = (output / "report.html").read_text()
            self.assertIn("SSIM moyen", rendered)
            self.assertIn('src="videos/motion-current.mp4"', rendered)
            for case in result["scenarios"]:
                self.assertEqual(len(case["encodes"]), 4)
                for entry in case["encodes"]:
                    self.assertGreater(entry["ssim"], 0.75)
                    self.assertLessEqual(entry["ssim"], 1.0)
                    self.assertGreater(entry["encoded_size_bytes"], 1000)
                    self.assertGreater(entry["encode_wall_seconds"], 0)
                    self.assertGreater(entry["encoded_fps"], 0)
                    video = output / entry["video"]
                    self.assertTrue(video.is_file())
                    self.assertLess(video.stat().st_size, 20 * 1024 * 1024)
                    bench._video_probe(video, width=360, height=640,
                                       fps=12, frames=24, codec="h264")
            self.assertNotIn("winner", result)
            self.assertIn("no Android battery/RAM estimate",
                          result["hardware_notice"])
            self.assertFalse((output / "reference-motion.mkv").exists())


if __name__ == "__main__":
    unittest.main()
