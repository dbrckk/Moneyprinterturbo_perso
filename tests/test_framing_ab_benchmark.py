"""Truthful, fully offline visual A/B framing benchmark contracts."""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

import framing_ab_benchmark as benchmark
from media_assets import MediaAsset
from motion_framing import plan_motion_crops


class FramingABPolicyTests(unittest.TestCase):
    def test_synthetic_cases_are_fixed_and_cover_adversarial_scenes(self):
        self.assertEqual(len(benchmark.CASES), 8)
        self.assertEqual(len(set(x[0] for x in benchmark.CASES)), 7)
        self.assertEqual(
            [x[2] for x in benchmark.CASES],
            ["shift", "shift", "center", "center", "center", "center", "matte", "matte"])
        for row in benchmark.CASES:
            self.assertIn(row[3:], ((640, 480), (1280, 720)))

    def test_guard_uses_actual_visible_synthetic_marker_not_hallucinated_score(self):
        base = {"name": "sport-right", "expected": "shift", "applied": "shift",
                "guided_red_fraction": .09, "center_red_fraction": 0.0}
        self.assertTrue(benchmark.expected_guard(base))
        self.assertFalse(benchmark.expected_guard({
            **base, "guided_red_fraction": .001}))
        self.assertFalse(benchmark.expected_guard({
            **base, "applied": "center"}))
        self.assertTrue(benchmark.expected_guard({
            **base, "expected": "center", "applied": "center"}))
        self.assertFalse(benchmark.expected_guard({
            **base, "expected": "center", "applied": "shift"}))
        self.assertTrue(benchmark.expected_guard({
            **base, "expected": "matte", "applied": "matte",
            "guided_red_fraction": .01, "center_red_fraction": .001}))
        self.assertFalse(benchmark.expected_guard({
            **base, "expected": "matte", "applied": "matte",
            "guided_red_fraction": .001, "center_red_fraction": .001}))

    def test_portable_report_escapes_all_titles_and_paths(self):
        row = {"name": "x<script>", "description": 'Footage <img src=x>',
               "expected": "center", "applied": "center",
               "guided_red_fraction": 0.0, "center_red_fraction": 0.0,
               "passes_expected_guard": False,
               "center_video": 'a" onerror="alert(1)',
               "guided_video": "videos/guided.mp4"}
        html = benchmark.render_report({"cases": [row]})
        self.assertIn("&lt;img src=x&gt;", html)
        self.assertIn("a&quot; onerror=&quot;alert(1)", html)
        self.assertNotIn("Footage <img", html)
        self.assertIn('media-src &#39;self&#39;', html)
        self.assertIn("playsinline", html)
        self.assertIn("À examiner", html)

    def test_long_crossing_guard_requires_both_ends_of_actual_output(self):
        case = {"name": "long-crossing", "expected": "matte", "applied": "matte",
                "guided_red_fraction": .02, "center_red_fraction": 0.0,
                "late_guided_red_fraction": .02, "late_center_red_fraction": 0.0}
        self.assertTrue(benchmark.expected_guard(case))
        self.assertFalse(benchmark.expected_guard({
            **case, "late_guided_red_fraction": 0.0}))
        self.assertFalse(benchmark.expected_guard({
            **case, "applied": "center"}))
        self.assertEqual(benchmark.scene_seconds("long-crossing"), 9.0)
        self.assertEqual(benchmark.scene_seconds("sport-left"), 2.5)

    def test_unknown_benchmark_case_rejected_before_execution(self):
        with self.assertRaises(ValueError):
            benchmark.synthetic_video("path/../../../private", Path("ignore.mp4"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg required for actual MP4 benchmarks")
class RealSyntheticFramingABTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_real_strobe_and_runner_keep_correct_motion_decisions(self):
        for name, expected in (("blinking-sign", "center"),
                               ("sport-right", "shift")):
            with self.subTest(synthetic_clip=name):
                source = self.root / (name + ".mp4")
                benchmark.synthetic_video(name, source)
                self.assertGreater(source.stat().st_size, 1800)
                asset = MediaAsset(
                    source, "video", "local", "generated", "benchmark", name,
                    source_width=640, source_height=480)
                selections, notes = plan_motion_crops(
                    [asset], [benchmark.SECONDS], width=360, height=640)
                guided = selections[0]
                observed = ("shift" if guided.crop_horizontal_anchor is not None
                            else "center")
                self.assertEqual(observed, expected, notes)
                if name == "sport-right":
                    target = self.root / "runner-guided.mp4"
                    import fast_media
                    cmd = fast_media.build_onepass_media_command(
                        [guided], [benchmark.SECONDS], width=360, height=640,
                        fps=benchmark.FPS, transition="cut",
                        visual_filters=["format=yuv420p"], audio=None,
                        output=target, total=benchmark.SECONDS)
                    benchmark.run_command(cmd, timeout=100)
                    self.assertEqual(benchmark.probe_export(target)["frames"], 30)
                    self.assertGreater(benchmark.red_coverage(target), .004)

    def test_centered_dark_global_multi_and_panorama_sources_are_valid(self):
        for name, _, expected, width, height in benchmark.CASES:
            if name in ("sport-right", "sport-left", "blinking-sign"):
                continue
            with self.subTest(name=name):
                source = self.root / (name + ".mp4")
                benchmark.synthetic_video(name, source)
                self.assertGreater(source.stat().st_size, 1800)
                asset = MediaAsset(source, "video", "local", "generated",
                                   "benchmark", name,
                                   source_width=width, source_height=height)
                if expected == "matte" and name != "long-crossing":
                    from scene_framing import needs_video_soft_matte
                    self.assertTrue(needs_video_soft_matte(
                        asset, width=360, height=640))
                else:
                    choices, _ = plan_motion_crops(
                        [asset], [benchmark.SECONDS], width=360, height=640)
                    if name == "long-crossing":
                        self.assertTrue(choices[0].preserve_full_video_frame)
                        self.assertIsNone(choices[0].crop_horizontal_anchor)
                    else:
                        self.assertIsNone(choices[0].crop_horizontal_anchor)


if __name__ == "__main__":
    unittest.main()
