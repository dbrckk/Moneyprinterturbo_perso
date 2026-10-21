"""End-to-end FFmpeg proof of conservative black-intro removal."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

import free_studio
from media_assets import MediaAsset
from shot_quality import probe_black_intro
from video_timeline import plan_local_video_timeline


def clip(path: Path) -> MediaAsset:
    return MediaAsset(path, "video", "local", "user-provided", "user", path.name)


def ffmpeg(*args: str) -> None:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         *args], capture_output=True, text=True, timeout=65)
    if process.returncode:
        raise AssertionError(process.stderr[-1500:])


class BlackIntroPlanningTests(unittest.TestCase):
    def test_first_real_video_probe_is_cached_per_clip(self):
        path = Path("phone.mp4")
        probe_duration = Mock(return_value=9.0)
        probe_open = Mock(return_value=1.25)
        slots, notes = plan_local_video_timeline(
            [clip(path)] * 3, [1.0] * 3,
            probe=probe_duration, probe_intro=probe_open)
        self.assertEqual([source.source_start_seconds for source in slots],
                         [1.25, 2.25, 3.25])
        probe_duration.assert_called_once_with(path)
        probe_open.assert_called_once_with(path, 9.0)
        self.assertEqual(len(notes), 1)
        self.assertIn("near-black opening", notes[0])

    def test_invalid_or_absurd_quality_probe_has_no_effect(self):
        for invalid in (-1, 8.95, 999.0, float("nan"), True):
            with self.subTest(invalid=invalid):
                source = clip(Path("source.mp4"))
                slots, notices = plan_local_video_timeline(
                    [source, source], [1.5, 1.5], probe=lambda _: 9.0,
                    probe_intro=lambda _p, _t: invalid)
                self.assertEqual([s.source_start_seconds for s in slots],
                                 [0.0, 1.5])
                self.assertEqual(notices, [])

    def test_intro_aware_seek_does_not_loop_across_black_opening(self):
        source = clip(Path("footage.mp4"))
        slots, notes = plan_local_video_timeline(
            [source] * 3, [2.0] * 3,
            probe=lambda _: 5.0,
            probe_intro=lambda _p, _t: 1.0)
        # Third scene would have started at 1+4%4 = 1.0; second
        # would overlap EOF, so replay the usable red frames instead.
        self.assertEqual([s.source_start_seconds for s in slots],
                         [1.0, 1.0, 1.0])
        self.assertEqual(len(notes), 1)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg required")
class BlackIntroRealVideoTests(unittest.TestCase):
    def make_intro(self, filename: Path) -> None:
        ffmpeg("-f", "lavfi", "-i", "color=c=black:s=160x120:r=12:d=1.5",
               "-f", "lavfi", "-i", "color=c=red:s=160x120:r=12:d=4",
               "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
               "-map", "[v]", "-an", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", str(filename))

    def test_real_intro_is_skipped_but_nonblack_and_allblack_are_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dark = root / "black-then-red.mp4"
            self.make_intro(dark)
            skipped = probe_black_intro(dark, 5.5)
            self.assertAlmostEqual(skipped, 1.5, delta=0.26)
            for color in ("black", "red"):
                path = root / (color + ".mp4")
                ffmpeg("-f", "lavfi", "-i",
                       f"color=c={color}:s=160x120:r=12:d=5",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
                self.assertEqual(probe_black_intro(path, 5.0), 0.0)
            self.assertEqual(probe_black_intro(dark, float("nan")), 0.0)
            self.assertEqual(probe_black_intro(root / "missing.mp4", 5.5), 0.0)

    def test_actual_render_first_scene_excludes_dark_intro(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            footage = root / "media"
            footage.mkdir()
            self.make_intro(footage / "001_phone.mp4")
            out = root / "result.mp4"
            manifest = free_studio.render(
                "Voyage en vidéo", "Une belle découverte.",
                out, media_dir=footage, mute=True, resolution=360,
                fps=12, caption_style="classic", transition="cut")
            self.assertTrue(out.is_file())
            self.assertTrue(any("near-black opening" in s
                                for s in manifest["warnings"]))
            self.assertAlmostEqual(
                manifest["media"][0]["source_start_seconds"], 1.5,
                delta=0.26)
            # Read actual output frames from the first scene. The first
            # 0.9 second must show red material, not the black source slate.
            sample = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error",
                 "-ss", "0.9", "-i", str(out),
                 "-vf", "crop=80:80:100:80,scale=1:1:flags=area",
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "pipe:1"], capture_output=True, timeout=40, check=True).stdout
            self.assertEqual(len(sample), 3)
            self.assertGreater(sample[0], 3 * max(sample[1], sample[2]) + 20,
                               list(sample))


if __name__ == "__main__":
    unittest.main()
