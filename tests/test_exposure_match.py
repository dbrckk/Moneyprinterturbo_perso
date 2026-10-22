"""Actual FFmpeg video tests for mild automated exposure normalization."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import exposure_match
import free_studio
from media_assets import MediaAsset


def source(path: Path, source_url: str = "local") -> MediaAsset:
    return MediaAsset(path, "video", source_url, "user-provided", "user", path.name)


def ffmpeg(*args: str) -> None:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, timeout=80)
    if process.returncode:
        raise AssertionError(process.stderr[-1500:])


def frame_luminance(path: Path, second: float) -> float:
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(second), "-i",
         str(path), "-vf", "crop=80:80:140:240,scale=1:1:flags=area",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, timeout=35, check=True).stdout
    if len(out) != 3:
        raise AssertionError(f"Unexpected RGB output: {out!r}")
    return (54 * out[0] + 183 * out[1] + 19 * out[2]) / 256


class ExposureUnitTests(unittest.TestCase):
    def test_only_mild_comparable_video_exposures_are_corrected(self):
        a, b = source(Path("a.mp4")), source(Path("b.mp4"))
        probe = Mock(side_effect=lambda p, start, dur:
                     95 if p == a.file else 150)
        balanced, notes = exposure_match.balance_exposure(
            [a, b, a], [2.6] * 3, probe=probe)
        self.assertEqual(probe.call_count, 2)
        self.assertGreater(balanced[0].exposure_shift, 0.02)
        self.assertLess(balanced[1].exposure_shift, -0.02)
        self.assertEqual(balanced[2].exposure_shift, balanced[0].exposure_shift)
        self.assertEqual(a.exposure_shift, 0.0)
        self.assertEqual(len(notes), 1)
        self.assertIn("brightness=", exposure_match.exposure_filter(balanced[0]))

    def test_night_day_missing_measurement_and_single_clip_are_preserved(self):
        a, b = source(Path("a.mp4")), source(Path("b.mp4"))
        for readings in ((12, 190), (60, 200), (95, None),
                         (float("nan"), 150), (80, 85), (220, 120)):
            with self.subTest(readings=readings):
                sequence, notes = exposure_match.balance_exposure(
                    [a, b], [2.6, 2.6],
                    probe=lambda path, _start, _duration:
                        readings[0] if path == a.file else readings[1])
                self.assertEqual([x.exposure_shift for x in sequence], [0, 0])
                self.assertEqual(notes, [])
        self.assertEqual(exposure_match.balance_exposure(
            [a, a], [2.6, 2.6],
            probe=Mock(side_effect=AssertionError("should not probe")))[1], [])

    def test_never_grade_images_remote_media_or_partially_measured_groups(self):
        a, b = source(Path("a.mp4")), source(Path("b.mp4"))
        photo = MediaAsset(Path("photo.jpg"), "image", "local", "user", "user", "photo")
        remote = source(Path("commons.mp4"), source_url="https://wikimedia")
        result, notes = exposure_match.balance_exposure(
            [a, photo, remote, b], [2.6] * 4,
            probe=lambda p, _s, _d: 100 if p == a.file else 130)
        self.assertIs(result[1], photo)
        self.assertIs(result[2], remote)
        self.assertEqual(result[2].exposure_shift, 0)
        self.assertEqual(len(notes), 1)
        too_many = [source(Path(f"source{i}.mp4")) for i in range(9)]
        probe = Mock(side_effect=AssertionError("too many files"))
        result, notes = exposure_match.balance_exposure(
            too_many, [2.6] * len(too_many), probe=probe)
        self.assertEqual(notes, [])
        probe.assert_not_called()

    def test_reused_source_lighting_changes_fail_closed_and_stable_shots_match(self):
        a, b = source(Path("a.mp4")), source(Path("b.mp4"))
        late = replace(a, source_start_seconds=3.0)
        readings = {(a.file, 0.0): 95, (a.file, 3.0): 149,
                    (b.file, 0.0): 135}
        probe = Mock(side_effect=lambda path, start, _duration: readings[path, start])
        planned, messages = exposure_match.balance_exposure(
            [a, b, late], [2.6] * 3, probe=probe)
        self.assertEqual(planned, [a, b, late])
        self.assertEqual(messages, [])
        # Source A's two time windows already prove unstable lighting.
        # Fail closed before probing unrelated source B.
        self.assertEqual(probe.call_count, 2)
        self.assertEqual([c.args[1] for c in probe.call_args_list], [0.0, 3.0])
        # Small variations within one recording may still use the normal
        # mild adjustment. The same source receives matching fixed gains.
        readings[a.file, 3.0] = 99
        revised, notes = exposure_match.balance_exposure(
            [a, b, late], [2.6] * 3,
            probe=lambda path, start, _duration: readings[path, start])
        self.assertEqual(len(notes), 1)
        self.assertEqual(revised[0].exposure_shift, revised[2].exposure_shift)
        self.assertNotEqual(revised[0].exposure_shift, 0.0)

    def test_repeated_window_limit_avoids_unbounded_phone_decoding(self):
        a, b = source(Path("a.mp4")), source(Path("b.mp4"))
        scenes = [replace(a, source_start_seconds=float(i) * 3.0)
                  for i in range(12)] + [b]
        probe = Mock(side_effect=AssertionError("No expensive decoding"))
        result, notes = exposure_match.balance_exposure(
            scenes, [2.6] * len(scenes), probe=probe)
        self.assertEqual(result, scenes)
        self.assertEqual(notes, [])
        probe.assert_not_called()
        bad = [a, replace(b, source_start_seconds=float("nan"))]
        self.assertEqual(
            exposure_match.balance_exposure(bad, [2.6] * 2,
                                            probe=probe)[1], [])
        probe.assert_not_called()

    def test_rejects_invalid_filter_parameters_and_failed_sampling(self):
        for value in (0.051, -0.051, float("nan"), float("inf"), True, "0.2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                exposure_match.exposure_filter(
                    type("Video", (), {"exposure_shift": value})())
        self.assertIsNone(exposure_match.sample_luminance(
            Path("missing.mp4"), 0.0, 2.6))
        self.assertIsNone(exposure_match.sample_luminance(
            Path("missing.mp4"), -1.0, 2.6))
        with self.assertRaises(ValueError):
            exposure_match.balance_exposure([source(Path("a.mp4"))], [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg required for real media proof")
class ExposureRealVideoTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> Path:
        folder = root / "media"
        folder.mkdir()
        for name, level in (("clip1.mp4", 95), ("clip2.mp4", 150)):
            rgb = f"0x{level:02x}{level:02x}{level:02x}"
            ffmpeg("-f", "lavfi", "-i", f"color=c={rgb}:s=180x320:r=12:d=4",
                   "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   str(folder / name))
        return folder

    def test_real_ffmpeg_probes_respect_actual_luminance(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = self.make_fixture(Path(temp))
            values = [exposure_match.sample_luminance(folder / name, 0.0, 2.6)
                      for name in ("clip1.mp4", "clip2.mp4")]
            self.assertTrue(all(v is not None for v in values))
            self.assertGreater(values[1] - values[0], 40)
            self.assertLess(abs(values[0] - 95), 6)
            self.assertLess(abs(values[1] - 150), 6)

    def test_real_reused_video_with_daylight_change_is_not_graded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "reused.mp4"
            other = root / "other.mp4"
            ffmpeg("-f", "lavfi", "-i",
                   "color=c=0x626262:s=180x320:r=12:d=3",
                   "-f", "lavfi", "-i",
                   "color=c=0x969696:s=180x320:r=12:d=3",
                   "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                   "-map", "[v]", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", str(path))
            ffmpeg("-f", "lavfi", "-i",
                   "color=c=0x828282:s=180x320:r=12:d=5",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(other))
            self.assertGreater(
                exposure_match.sample_luminance(path, 3.2, 2.0) -
                exposure_match.sample_luminance(path, 0.0, 2.0), 30)
            first = source(path)
            subsequent = replace(first, source_start_seconds=3.1)
            foreign = source(other)
            originals = (path.read_bytes(), other.read_bytes())
            corrected, notices = exposure_match.balance_exposure(
                [first, foreign, subsequent], [2.0] * 3)
            self.assertEqual(corrected, [first, foreign, subsequent])
            self.assertEqual(notices, [])
            self.assertEqual((path.read_bytes(), other.read_bytes()), originals)

    def test_actual_optimized_and_legacy_render_reduce_scene_luminance_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = self.make_fixture(root)
            for optimized in (True, False):
                with self.subTest(optimized=optimized):
                    corrected = root / f"graded-{optimized}.mp4"
                    original = root / f"baseline-{optimized}.mp4"
                    opts = dict(media_dir=folder, mute=True, resolution=360, fps=12,
                                caption_style="classic", transition="cut",
                                use_media_onepass=optimized)
                    info = free_studio.render(
                        "Images", "Premier paysage. Deuxième paysage.",
                        corrected, **opts)
                    with patch.object(free_studio, "balance_exposure",
                                      side_effect=lambda assets, durations: (assets, [])):
                        free_studio.render("Images",
                            "Premier paysage. Deuxième paysage.", original, **opts)
                    self.assertEqual(len(info["media"]), 2)
                    self.assertGreater(info["media"][0]["exposure_shift"], 0)
                    self.assertLess(info["media"][1]["exposure_shift"], 0)
                    # Validate decoded pixels, rather than checking only
                    # the generated FFmpeg command or saved manifest.
                    graded_delta = abs(frame_luminance(corrected, 1.0) -
                                       frame_luminance(corrected, 3.6))
                    baseline_delta = abs(frame_luminance(original, 1.0) -
                                         frame_luminance(original, 3.6))
                    self.assertGreater(baseline_delta, 25)
                    self.assertLess(graded_delta, baseline_delta - 5,
                                    (optimized, graded_delta, baseline_delta))


if __name__ == "__main__":
    unittest.main()
