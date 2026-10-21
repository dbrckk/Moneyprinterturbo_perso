"""Offline verification of restrained hard-cut detection and chronological edits."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from media_assets import MediaAsset
from shot_cuts import probe_hard_cuts
from video_timeline import plan_local_video_timeline


def source(path: Path) -> MediaAsset:
    return MediaAsset(path, "video", "local", "user-provided", "user", path.name)


def encode(*args: str) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         *args], capture_output=True, text=True, timeout=65)
    if proc.returncode:
        raise AssertionError(proc.stderr[-1400:])


class TimelineHardCutTests(unittest.TestCase):
    def test_aligns_to_next_actual_cut_without_reordering_source(self):
        a = source(Path("sports.mp4"))
        duration = Mock(return_value=9.0)
        cut_probe = Mock(return_value=[6.0, 3.0])
        planned, messages = plan_local_video_timeline(
            [a, a, a], [2.6] * 3, probe=duration, probe_cuts=cut_probe)
        self.assertEqual([x.source_start_seconds for x in planned], [0.0, 3.0, 6.0])
        duration.assert_called_once_with(a.file)
        cut_probe.assert_called_once_with(a.file, 9.0)
        self.assertEqual(len(messages), 1)
        self.assertIn("Aligned 2 scene(s)", messages[0])

    def test_unknown_and_unstable_boundaries_keep_existing_timeline(self):
        a = source(Path("phone.mp4"))
        for candidates in ([], [0.1], [float("nan"), 500.0],
                           [True, -1], "invalid", [1.0] * 129):
            with self.subTest(candidates=candidates):
                planned, messages = plan_local_video_timeline(
                    [a, a, a], [2.6] * 3, probe=lambda _: 9.0,
                    probe_cuts=lambda _p, _d: candidates)
                self.assertEqual(
                    [x.source_start_seconds for x in planned],
                    [0.0, 2.6, 5.2])
                self.assertEqual(messages, [])

    def test_cut_not_taken_if_insufficient_scene_duration_or_too_far(self):
        a = source(Path("camera.mp4"))
        planned, messages = plan_local_video_timeline(
            [a, a], [2.6, 4.0], probe=lambda _: 6.0,
            probe_cuts=lambda _p, _d: [3.0, 5.0])
        self.assertEqual([x.source_start_seconds for x in planned], [0.0, 2.6])
        self.assertEqual(messages, [])

    def test_single_use_skips_expensive_cut_probe(self):
        a = source(Path("a.mp4"))
        b = source(Path("b.mp4"))
        cuts = Mock(side_effect=AssertionError("no unnecessary scene scan"))
        planned, notices = plan_local_video_timeline(
            [a, b], [2, 2], probe=lambda _: 10.0, probe_cuts=cuts)
        self.assertEqual([s.source_start_seconds for s in planned], [0, 0])
        self.assertEqual(notices, [])
        cuts.assert_not_called()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg required")
class RealHardCutTests(unittest.TestCase):
    def test_actual_red_green_blue_video_has_two_verified_shot_cuts(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "three-takes.mp4"
            encode("-f", "lavfi", "-i", "color=c=red:s=320x240:r=12:d=3",
                   "-f", "lavfi", "-i", "color=c=green:s=320x240:r=12:d=3",
                   "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=12:d=3",
                   "-filter_complex",
                   "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                   "-map", "[v]", "-an", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", str(video))
            cuts = probe_hard_cuts(video, 9.0)
            self.assertEqual(len(cuts), 2, cuts)
            self.assertAlmostEqual(cuts[0], 3.0, delta=0.25)
            self.assertAlmostEqual(cuts[1], 6.0, delta=0.25)
            planned, notices = plan_local_video_timeline(
                [source(video)] * 3, [2.6] * 3,
                probe=lambda _: 9.0, probe_cuts=probe_hard_cuts)
            self.assertEqual([m.source_start_seconds for m in planned],
                             [0.0, 3.0, 6.0])
            self.assertEqual(len(notices), 1)

    def test_static_and_flickering_footage_does_not_create_fake_cuts(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            same = folder / "static.mp4"
            encode("-f", "lavfi", "-i", "color=c=red:s=160x120:r=12:d=5",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(same))
            self.assertEqual(probe_hard_cuts(same, 5), [])
            # Alternating colors are rapid flicker, not a stable new shot.
            flicker = folder / "flicker.mp4"
            encode("-f", "lavfi", "-i",
                   "testsrc2=s=160x120:r=12:d=4",
                   "-vf", "eq=contrast=3", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", str(flicker))
            self.assertEqual(probe_hard_cuts(flicker, 4), [])

    def test_missing_or_invalid_files_leave_timeline_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.mp4"
            path.write_bytes(b"not an actual video")
            for seconds in (float("nan"), -1, True, 4.0):
                self.assertEqual(probe_hard_cuts(path, seconds), [])


if __name__ == "__main__":
    unittest.main()
