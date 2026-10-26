"""Guarded motion-onset alignment for reused phone footage.

The detector recognizes sustained *pixel changes*, not sport semantics.
Tests include real decoded FFmpeg footage and reject flashes/cuts/shake.
"""
from pathlib import Path
import math
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from media_assets import MediaAsset
from shot_cuts import ShotProfile, action_onset_start, probe_shot_profile
from video_timeline import plan_local_video_timeline


def local(path: Path) -> MediaAsset:
    return MediaAsset(path, "video", "local", "user-provided", "user", path.name)


class MotionOnsetUnitTests(unittest.TestCase):
    @staticmethod
    def onset_profile() -> ShotProfile:
        values = [0.0] * 44
        values[13:16] = [14.0, 17.0, 13.0]
        return ShotProfile(motion=tuple(values))

    def test_only_later_reused_scene_moves_to_sustained_motion(self):
        source = local(Path("workout.mp4"))
        scan = Mock(return_value=self.onset_profile())
        planned, notes = plan_local_video_timeline(
            [source, source], [2.6, 2.6], probe=lambda _: 10.0,
            probe_profile=scan,
        )
        self.assertEqual([x.source_start_seconds for x in planned], [0.0, 3.25])
        self.assertTrue(any("sustained visible motion" in message for message in notes))
        scan.assert_called_once_with(source.file, 10.0)
        self.assertEqual(source.source_start_seconds, 0.0)

    def test_flash_hard_cut_camera_shake_and_existing_action_are_unchanged(self):
        start = 2.6
        baseline = self.onset_profile()
        variants = [
            ShotProfile(motion=tuple([16.0] * 44)),  # already active
            ShotProfile(motion=tuple([0.0] * 13 + [60.0, 0.0, 0.0] + [0.0] * 28)),
            ShotProfile(cuts=(3.0,), motion=baseline.motion),
            ShotProfile(motion=tuple([0.0] * 13 + [65.0] * 3 + [0.0] * 28)),
            ShotProfile(motion=tuple([0.0] * 13 + [14.0, 2.0, 15.0] + [0.0] * 28)),
            ShotProfile(),
        ]
        for profile in variants:
            with self.subTest(profile=profile):
                self.assertEqual(action_onset_start(profile, start, 2.6, 10.0), start)
        # A high-quality hard cut is preferred by the timeline planner.
        source = local(Path("sports.mp4"))
        with_cut = ShotProfile(cuts=(3.0,), motion=baseline.motion)
        result, notes = plan_local_video_timeline(
            [source, source], [2.6, 2.6], probe=lambda _: 10.0,
            probe_profile=lambda _p, _d: with_cut)
        self.assertEqual([p.source_start_seconds for p in result], [0.0, 3.0])
        self.assertFalse(any("sustained visible motion" in n for n in notes))

    def test_clip_end_truncated_profile_and_invalid_inputs_are_safe(self):
        good = self.onset_profile()
        for duration in (5.1, 5.2, float("nan"), -1.0, True):
            with self.subTest(duration=duration):
                self.assertEqual(action_onset_start(good, 2.6, 2.6, duration), 2.6)
        for profile in (ShotProfile(motion=(0.0,) * 4),
                        ShotProfile(motion=(float("nan"),) * 44)):
            self.assertEqual(action_onset_start(profile, 2.6, 2.6, 10.0), 2.6)
        self.assertEqual(action_onset_start(good, 29.0, 2.6, 40.0), 29.0)
        self.assertEqual(action_onset_start(good, -1.0, 2.6, 10.0), -1.0)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg is required")
class RealMotionOnsetTests(unittest.TestCase):
    def test_real_10_second_workout_motion_is_detected_without_reordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Unmoving foreground until 3.25s; bright marker then repeatedly
            # moves left/right. 4fps is the shot analyzer's actual sample rate.
            for index in range(40):
                image = bytearray(bytes((40, 42, 50)) * (96 * 54))
                phase = (12 * (index - 12)) % 96 if index >= 13 else 0
                x = phase if phase <= 48 else 96 - phase
                for y in range(7, 47):
                    start = (y * 96 + x) * 3
                    image[start:start + 48 * 3] = bytes((230, 70, 65)) * 48
                (root / f"{index:03d}.ppm").write_bytes(
                    b"P6\n96 54\n255\n" + bytes(image))
            clip = root / "moving-marker.mp4"
            cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-y", "-framerate", "4", "-i", str(root / "%03d.ppm"),
                   "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)]
            produced = subprocess.run(cmd, capture_output=True, timeout=45)
            self.assertEqual(produced.returncode, 0, produced.stderr[-1200:])
            original = clip.read_bytes()
            report = probe_shot_profile(clip, 10.0)
            self.assertGreater(len(report.motion), 30)
            self.assertLess(max(report.motion[11:13]), 3.5)
            self.assertGreater(min(report.motion[13:16]), 8.0)
            self.assertEqual(action_onset_start(report, 2.6, 2.6, 10.0), 3.25)
            planned, notes = plan_local_video_timeline(
                [local(clip)] * 2, [2.6, 2.6],
                probe=lambda _: 10.0, probe_profile=probe_shot_profile)
            self.assertEqual([item.source_start_seconds for item in planned],
                             [0.0, 3.25])
            self.assertTrue(any("sustained visible motion" in n for n in notes))
            self.assertEqual(clip.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
