"""Conservative sharpness-assisted edit points on local phone footage."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from media_assets import MediaAsset
from shot_cuts import ShotProfile, clearer_start, probe_shot_profile
from video_timeline import plan_local_video_timeline


def clip(path: Path) -> MediaAsset:
    return MediaAsset(path, "video", "local", "user-provided", "user", path.name)


def ffmpeg(*args: str) -> None:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(command, capture_output=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr[-1500:])


class FocusAlignmentUnitTests(unittest.TestCase):
    def test_moves_only_later_scene_towards_sustained_focus(self):
        profile = ShotProfile((), (9.0,) * 10 + (3.0, 3.0, 7.0, 8.0) +
                              (8.0,) * 24)
        source = clip(Path("phone.mp4"))
        scan = Mock(return_value=profile)
        planned, notes = plan_local_video_timeline(
            [source, source], [2.6, 2.6], probe=lambda _: 9.0,
            probe_profile=scan)
        self.assertEqual([s.source_start_seconds for s in planned],
                         [0.0, 3.0])
        scan.assert_called_once_with(source.file, 9.0)
        self.assertEqual(len(notes), 1)
        self.assertIn("clearer frames", notes[0])

    def test_never_moves_across_a_real_hard_cut(self):
        profile = ShotProfile((2.85,), (4.0,) * 11 + (9.0,) * 25)
        self.assertEqual(clearer_start(profile, 2.6, 2.0, 9.0), 2.6)

    def test_no_selection_if_soft_or_clip_ending_or_static(self):
        static = ShotProfile((), (8.0,) * 40)
        self.assertEqual(clearer_start(static, 2.6, 2.6, 9.0), 2.6)
        improved = ShotProfile((), (3.0,) * 11 + (8.0,) * 29)
        self.assertEqual(clearer_start(improved, 2.6, 2.6, 4.0), 2.6)
        self.assertEqual(clearer_start(improved, 0.0, 2.6, 9.0), 0.0)
        self.assertEqual(clearer_start(ShotProfile(), 2.6, 2.6, 9.0), 2.6)

    def test_failed_profile_probe_is_non_fatal_and_single_use_skips_scan(self):
        a, b = clip(Path("a.mp4")), clip(Path("b.mp4"))
        only = Mock(side_effect=AssertionError("unneeded quality scan"))
        files, _ = plan_local_video_timeline(
            [a, b], [2.6, 2.6], probe=lambda _: 9.0, probe_profile=only)
        self.assertEqual([a.source_start_seconds for a in files], [0.0, 0.0])
        only.assert_not_called()
        failed = Mock(side_effect=OSError("mobile decoder unavailable"))
        planned, notes = plan_local_video_timeline(
            [a, a], [2.6, 2.6], probe=lambda _: 9.0, probe_profile=failed)
        self.assertEqual([a.source_start_seconds for a in planned], [0.0, 2.6])
        self.assertEqual(notes, [])

    def test_prefer_valid_hard_cut_over_less_confident_focus(self):
        profile = ShotProfile((3.0,), (3.0,) * 11 + (9.0,) * 30)
        source = clip(Path("source.mp4"))
        planned, notes = plan_local_video_timeline(
            [source, source], [2.6, 2.6], probe=lambda _: 9.0,
            probe_profile=lambda _p, _d: profile)
        self.assertEqual([x.source_start_seconds for x in planned], [0.0, 3.0])
        self.assertIn("source shot cuts", notes[0])
        self.assertNotIn("clearer frames", " ".join(notes))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "real FFmpeg required")
class FocusAlignmentRealVideoTests(unittest.TestCase):
    def test_actual_soft_to_clear_clip_selects_clearer_frames_without_cut(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "phone.mp4"
            ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=12:d=2.5",
                   "-f", "lavfi", "-i", "testsrc2=s=320x180:r=12:d=0.5",
                   "-f", "lavfi", "-i", "testsrc2=s=320x180:r=12:d=4.0",
                   "-filter_complex",
                   "[1:v]boxblur=8:2[soft];"
                   "[0:v][soft][2:v]concat=n=3:v=1:a=0[v]",
                   "-map", "[v]", "-an", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", str(path))
            report = probe_shot_profile(path, 7.0)
            self.assertGreaterEqual(len(report.focus), 25)
            self.assertEqual(clearer_start(report, 2.6, 2.6, 7.0),
                             3.0, report)
            planned, notes = plan_local_video_timeline(
                [clip(path), clip(path)], [2.6, 2.6],
                probe=lambda _: 7.0, probe_profile=probe_shot_profile)
            self.assertEqual([c.source_start_seconds for c in planned],
                             [0.0, 3.0])
            self.assertEqual(len(notes), 1)
            self.assertIn("clearer frames", notes[0])

    def test_static_real_clip_does_not_manufacture_quality_boost(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "still.mp4"
            ffmpeg("-f", "lavfi", "-i", "color=c=red:s=160x120:r=12:d=6",
                   "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
            report = probe_shot_profile(path, 6.0)
            self.assertTrue(report.focus)
            self.assertEqual(clearer_start(report, 2.6, 2.6, 6.0), 2.6)
            self.assertEqual(probe_shot_profile(path, float("nan")), ShotProfile())


if __name__ == "__main__":
    unittest.main()
