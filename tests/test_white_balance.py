"""Neutral-reference white balance regression, including rendered MP4 RGB pixels."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from media_assets import MediaAsset
import white_balance
import free_studio


def owned(path: Path, url: str = "local") -> MediaAsset:
    return MediaAsset(path, "video", url, "user-provided", "user", path.name)


def encode(*argv: str) -> None:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *argv],
        capture_output=True, timeout=75)
    if process.returncode:
        raise AssertionError(process.stderr[-1800:])


def rgb_pixel(video: Path, at: float) -> tuple[int, int, int]:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-ss", str(at), "-i", str(video), "-vf",
         "crop=80:80:140:240,scale=1:1:flags=area",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, check=True, timeout=30).stdout
    if len(result) != 3:
        raise AssertionError(f"No decoded pixel: {result!r}")
    return tuple(result)


class WhiteBalanceUnitTests(unittest.TestCase):
    def test_mild_opposing_neutral_casts_receive_bounded_gains(self):
        warm = owned(Path("warm.mp4"))
        cool = owned(Path("cool.mp4"))
        probe = Mock(side_effect=lambda p, start, duration:
                     (140, 130, 115) if p == warm.file else (115, 130, 140))
        graded, notes = white_balance.balance_white(
            [warm, cool, warm], [2.6] * 3, probe=probe)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(len(notes), 1)
        self.assertEqual(graded[0].white_balance_gains, graded[2].white_balance_gains)
        self.assertLess(graded[0].white_balance_gains[0], 1)
        self.assertGreater(graded[0].white_balance_gains[2], 1)
        self.assertGreater(graded[1].white_balance_gains[0], 1)
        self.assertLess(graded[1].white_balance_gains[2], 1)
        for asset in graded:
            self.assertTrue(all(.94 <= g <= 1.06 for g in asset.white_balance_gains))
        self.assertEqual(warm.white_balance_gains, (1.0, 1.0, 1.0))
        self.assertIn("colorchannelmixer=", white_balance.white_balance_suffix(graded[0]))
        self.assertEqual(white_balance.white_balance_suffix(warm), "")

    def test_missing_neutral_vivid_night_and_equal_groups_skip(self):
        one, two = owned(Path("one.mp4")), owned(Path("two.mp4"))
        for pair in (
            ((140, 130, 115), None),
            ((140, 130, 115), (220, 180, 155)),
            ((140, 130, 115), (137, 130, 118)),
            ((140, 130, 115), (139, 129, 116)),
            ((185, 180, 160), (120, 90, 70)),
            ((140, 130, 100), (100, 130, 140)),
            ((float("nan"), 130, 110), (115, 130, 140)),
        ):
            with self.subTest(pair=pair):
                result, notes = white_balance.balance_white(
                    [one, two], [2.6, 2.6],
                    probe=lambda path, _start, _scene:
                        pair[0] if path == one.file else pair[1])
                self.assertEqual(result, [one, two])
                self.assertEqual(notes, [])

    def test_unrelated_photos_remote_sources_and_source_limits_untouched(self):
        one, two = owned(Path("1.mp4")), owned(Path("2.mp4"))
        photo = MediaAsset(Path("image.jpg"), "image", "local", "user", "user", "image")
        remote = owned(Path("remote.mp4"), "https://commons.wikimedia.org")
        result, notes = white_balance.balance_white(
            [one, photo, remote, two], [2.6] * 4,
            probe=lambda path, _s, _d: (140, 130, 115) if path == one.file
                  else (115, 130, 140))
        self.assertIs(result[1], photo)
        self.assertIs(result[2], remote)
        self.assertEqual(len(notes), 1)
        many = [owned(Path(f"clip{i}.mp4")) for i in range(9)]
        called = Mock(side_effect=AssertionError("large groups must not probe"))
        original, messages = white_balance.balance_white(
            many, [2.6] * len(many), probe=called)
        called.assert_not_called()
        self.assertEqual(original, many)
        self.assertEqual(messages, [])

    def test_reused_video_changed_color_temperature_keeps_original_grading(self):
        warm, cool = owned(Path("warm.mp4")), owned(Path("cool.mp4"))
        late = replace(warm, source_start_seconds=3.0)
        readings = {(warm.file, 0.0): (140, 130, 115),
                    (warm.file, 3.0): (115, 130, 140),
                    (cool.file, 0.0): (115, 130, 140)}
        observed = Mock(side_effect=lambda path, start, _duration:
                        readings[path, start])
        original, warnings = white_balance.balance_white(
            [warm, cool, late], [2.6] * 3, probe=observed)
        self.assertEqual(original, [warm, cool, late])
        self.assertEqual(warnings, [])
        self.assertEqual(observed.call_count, 3)
        # Consistent repeated scene keeps exactly the same bounded gains.
        readings[warm.file, 3.0] = (141, 130, 115)
        balanced, warnings = white_balance.balance_white(
            [warm, cool, late], [2.6] * 3,
            probe=lambda path, start, _dur: readings[path, start])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(balanced[0].white_balance_gains,
                         balanced[2].white_balance_gains)
        self.assertNotEqual(balanced[0].white_balance_gains, (1.0, 1.0, 1.0))

    def test_excessive_reused_windows_and_bad_timestamps_skip_white_balance(self):
        warm, cool = owned(Path("warm.mp4")), owned(Path("cool.mp4"))
        many = [replace(warm, source_start_seconds=float(i) * 3.0)
                for i in range(12)] + [cool]
        probe = Mock(side_effect=AssertionError("No sample allowed"))
        result, notes = white_balance.balance_white(
            many, [2.6] * len(many), probe=probe)
        self.assertEqual(result, many)
        self.assertEqual(notes, [])
        bad = [warm, replace(cool, source_start_seconds=float("inf"))]
        self.assertEqual(white_balance.balance_white(
            bad, [2.6, 2.6], probe=probe)[1], [])
        probe.assert_not_called()

    def test_reject_invalid_filter_arguments_and_bad_media(self):
        for gains in ((1.1, 1, 1), (1, float("nan"), 1),
                      [1, 1, 1], (True, 1, 1), ("bad", 1, 1)):
            with self.subTest(gains=gains), self.assertRaises(ValueError):
                white_balance.white_balance_suffix(
                    type("Clip", (), {"white_balance_gains": gains})())
        self.assertIsNone(white_balance.sample_neutral_rgb(
            Path("missing.mp4"), 0.0, 2.6))
        self.assertIsNone(white_balance.sample_neutral_rgb(
            Path("missing.mp4"), float("nan"), 2.6))
        with self.assertRaises(ValueError):
            white_balance.balance_white([owned(Path("one.mp4"))], [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg and FFprobe needed")
class WhiteBalanceRealMediaTests(unittest.TestCase):
    def fixtures(self, root: Path) -> Path:
        inputs = root / "media"
        inputs.mkdir()
        for name, color in (
            ("clip1.mp4", "0x8c8273"),
            ("clip2.mp4", "0x73828c"),
        ):
            encode("-f", "lavfi", "-i", f"color=c={color}:s=180x320:r=12:d=4",
                   "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   str(inputs / name))
        return inputs

    def test_actual_neutral_frames_provide_opposing_color_casts(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = self.fixtures(Path(tmp))
            a = white_balance.sample_neutral_rgb(inputs / "clip1.mp4", 0, 2.6)
            b = white_balance.sample_neutral_rgb(inputs / "clip2.mp4", 0, 2.6)
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            self.assertGreater(a[0] - a[2], 15)
            self.assertLess(b[0] - b[2], -15)
            self.assertLess(abs(a[1] - b[1]), 8)

    def test_real_reused_mp4_with_changing_white_balance_preserves_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reused = root / "reused.mp4"
            other = root / "reference.mp4"
            encode("-f", "lavfi", "-i",
                   "color=c=0x8c8273:s=180x320:r=12:d=3",
                   "-f", "lavfi", "-i",
                   "color=c=0x73828c:s=180x320:r=12:d=3",
                   "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                   "-map", "[v]", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", str(reused))
            encode("-f", "lavfi", "-i",
                   "color=c=0x828282:s=180x320:r=12:d=6",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(other))
            first = white_balance.sample_neutral_rgb(reused, 0.0, 2.0)
            second = white_balance.sample_neutral_rgb(reused, 3.1, 2.0)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertGreater(abs(first[0]/first[1] - second[0]/second[1]), .10)
            original = owned(reused)
            later = replace(original, source_start_seconds=3.1)
            reference = owned(other)
            saved = (reused.read_bytes(), other.read_bytes())
            graded, messages = white_balance.balance_white(
                [original, reference, later], [2.0] * 3)
            self.assertEqual(graded, [original, reference, later])
            self.assertEqual(messages, [])
            self.assertEqual((reused.read_bytes(), other.read_bytes()), saved)

    def test_real_optimized_and_legacy_mp4_reduce_warm_cool_cast_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = self.fixtures(root)
            for optimized in (True, False):
                with self.subTest(optimized=optimized):
                    corrected = root / f"corrected-{optimized}.mp4"
                    original = root / f"baseline-{optimized}.mp4"
                    options = dict(media_dir=inputs, mute=True, resolution=360,
                                   fps=12, caption_style="classic",
                                   transition="cut", use_media_onepass=optimized)
                    info = free_studio.render(
                        "Couleurs", "Première scène. Deuxième scène.",
                        corrected, **options)
                    with patch.object(free_studio, "balance_white",
                                      side_effect=lambda slots, durations: (slots, [])):
                        free_studio.render(
                            "Couleurs", "Première scène. Deuxième scène.",
                            original, **options)
                    self.assertEqual(len(info["media"]), 2)
                    self.assertNotEqual(info["media"][0]["white_balance_gains"],
                                        (1.0, 1.0, 1.0))
                    self.assertTrue(any("neutral color casts" in message
                                        for message in info["warnings"]))
                    def difference(path: Path) -> float:
                        a, b = rgb_pixel(path, 1.0), rgb_pixel(path, 3.6)
                        return abs((a[0] - a[2]) - (b[0] - b[2]))
                    before, after = difference(original), difference(corrected)
                    self.assertGreater(before, 25)
                    self.assertLess(after, before - 8,
                                    (optimized, before, after))


if __name__ == "__main__":
    unittest.main()
