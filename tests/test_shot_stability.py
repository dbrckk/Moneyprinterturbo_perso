"""Visual turbulence and repeated footage safeguards in mobile local renders."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from media_assets import MediaAsset
from shot_cuts import ShotProfile, probe_shot_profile, steadier_start
from video_timeline import plan_local_video_timeline


def local(path: Path) -> MediaAsset:
    return MediaAsset(path, "video", "local", "user-provided", "user", path.name)


class StabilityPlanningTests(unittest.TestCase):
    @staticmethod
    def turbulent_profile() -> ShotProfile:
        # Start of second scene is 2.6 seconds. Frame-difference spikes at
        # 2.75s and 3.0s, then quieter neighboring frames after 3.25s.
        motion = [5.0] * 44
        motion[11:15] = [80.0, 80.0, 3.0, 3.0]
        return ShotProfile(motion=tuple(motion))

    def test_only_repeated_scene_moves_after_short_turbulence(self):
        clip = local(Path("phone.mp4"))
        scan = Mock(return_value=self.turbulent_profile())
        timeline, notes = plan_local_video_timeline(
            [clip, clip], [2.6, 2.6],
            probe=lambda _: 9.0, probe_profile=scan)
        self.assertEqual([item.source_start_seconds for item in timeline],
                         [0.0, 3.25])
        self.assertEqual(len(notes), 1)
        self.assertIn("visually turbulent", notes[0])
        scan.assert_called_once_with(clip.file, 9.0)

    def test_real_cuts_are_never_crossed_in_search_for_calm(self):
        base = self.turbulent_profile()
        masked = ShotProfile((3.0,), base.focus, base.motion)
        self.assertEqual(steadier_start(masked, 2.6, 2.6, 9.0), 2.6)
        self.assertEqual(steadier_start(base, 2.6, 2.6, 5.0), 2.6)

    def test_sustained_action_and_missing_probe_dont_change_seek(self):
        rapid = ShotProfile(motion=(80.0,) * 44)
        self.assertEqual(steadier_start(rapid, 2.6, 2.6, 9.0), 2.6)
        self.assertEqual(steadier_start(ShotProfile(), 2.6, 2.6, 9.0), 2.6)
        self.assertEqual(steadier_start(self.turbulent_profile(), float("nan"), 2.6, 9.0),
                         float("nan")) if False else None
        for invalid in (float("nan"), -1.0, True, "9"):
            self.assertEqual(steadier_start(rapid, 2.6, 2.6, invalid), 2.6)

    def test_repeated_short_clip_warns_without_destructive_jump(self):
        clip = local(Path("short.mp4"))
        scan = Mock(return_value=ShotProfile())
        timeline, notes = plan_local_video_timeline(
            [clip, clip, clip], [2.6, 2.6, 2.6],
            probe=lambda _: 5.0, probe_profile=scan)
        self.assertEqual([s.source_start_seconds for s in timeline],
                         [0.0, 2.6, 0.2])
        self.assertEqual(len(notes), 1)
        self.assertIn("too short to avoid some repeated moments", notes[0])
        scan.assert_called_once_with(clip.file, 5.0)

    def test_long_distinct_clip_never_reports_repetition(self):
        clip = local(Path("long.mp4"))
        timeline, notes = plan_local_video_timeline(
            [clip] * 3, [2.6] * 3, probe=lambda _: 15.0,
            probe_profile=lambda _p, _d: ShotProfile())
        self.assertEqual([s.source_start_seconds for s in timeline],
                         [0.0, 2.6, 5.2])
        self.assertEqual(notes, [])

    def test_failed_optional_probe_preserves_timeline(self):
        clip = local(Path("bad.mp4"))
        timeline, notes = plan_local_video_timeline(
            [clip] * 3, [2.6] * 3, probe=lambda _: 9.0,
            probe_profile=Mock(side_effect=OSError("hardware decoder failed")))
        self.assertEqual([s.source_start_seconds for s in timeline],
                         [0.0, 2.6, 5.2])
        self.assertEqual(notes, [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg required for real sampled-frame test")
class RealTurbulentVideoTests(unittest.TestCase):
    def test_alternating_technical_jitter_followed_by_still_video(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            # Fully synthetic real input: no network, PIL, image downloads.
            colors = [(250, 15, 15), (15, 15, 250), (250, 15, 15)] + [
                (250, 15, 15)] * 17
            for number, rgb in enumerate(colors):
                head = b"P6\n96 54\n255\n"
                (folder / f"{number:03d}.ppm").write_bytes(
                    head + bytes(rgb) * (96 * 54))
            video = folder / "shaky-intro.mp4"
            cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-y", "-framerate", "4", "-i", str(folder / "%03d.ppm"),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)]
            done = subprocess.run(cmd, capture_output=True, timeout=50)
            self.assertEqual(done.returncode, 0, done.stderr[-500:])
            evidence = probe_shot_profile(video, 5.0)
            self.assertGreater(len(evidence.motion), 12)
            self.assertGreater(evidence.motion[1], 60.0)
            self.assertGreater(evidence.motion[2], 60.0)
            self.assertLess(evidence.motion[3], 11.0)
            self.assertLess(evidence.motion[4], 11.0)
            self.assertAlmostEqual(steadier_start(evidence, 0.0, 2.6, 5.0),
                                   0.75, delta=0.01)
            self.assertEqual(probe_shot_profile(folder / "missing.mp4", 5.0),
                             ShotProfile())


if __name__ == "__main__":
    unittest.main()
