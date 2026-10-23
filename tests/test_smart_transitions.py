"""Real FFmpeg verification of conservative per-boundary smart transitions."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

import free_studio
from cinematic import xfade_transition_spec
from media_assets import MediaAsset
from smart_transitions import boundary_features, boundary_rgb, choose_boundaries
from video_qa import probe as video_probe


def video(name: str, start: float = 0.0) -> MediaAsset:
    return MediaAsset(Path(name), "video", "local", "user-provided", "user",
                      Path(name).name, source_start_seconds=start)


def ffmpeg(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel",
                        "error", "-y", *args], capture_output=True, timeout=70)
    if r.returncode:
        raise AssertionError(r.stderr[-1500:])


class SmartTransitionUnitTests(unittest.TestCase):
    def test_contiguous_local_video_uses_clean_cut_without_probe(self):
        source = video("phone.mp4")
        second = replace(source, source_start_seconds=2.6)
        probe = Mock(side_effect=AssertionError("unneeded probe"))
        result, notes = choose_boundaries([source, second], [2.6, 2.6], probe=probe)
        self.assertEqual(result, ("cut",))
        self.assertEqual(len(notes), 1)
        probe.assert_not_called()

    def test_similar_but_distinct_sources_cut_different_sources_fade(self):
        a, b, c = video("a.mp4"), video("b.mp4"), video("c.mp4")
        shades = {a.file: (120.0, 124.0, 125.0),
                  b.file: (135.0, 134.0, 132.0),
                  c.file: (65.0, 100.0, 205.0)}
        probe = Mock(side_effect=lambda asset, _t: shades[asset.file])
        transitions, notes = choose_boundaries([a, b, c], [2.6] * 3, probe=probe)
        self.assertEqual(transitions, ("cut", "fade"))
        self.assertEqual(len(notes), 1)
        self.assertEqual(probe.call_count, 4)

    def test_spatial_layout_blocks_false_cuts_with_matching_mean_rgb(self):
        a, b = video("a.mp4"), video("b.mp4")
        # Opposite left/right brightness, despite IDENTICAL average colors.
        left = (40.0,) * 9 + (215.0,) * 9
        right = (215.0,) * 9 + (40.0,) * 9
        means = (127.5, 127.5, 127.5)
        features = {a.file: (*means, left), b.file: (*means, right)}
        modes, notes = choose_boundaries(
            [a, b], [2.6, 2.6],
            probe=lambda asset, _t: features[asset.file],
        )
        self.assertEqual(modes, ("fade",))
        self.assertEqual(notes, [])
        features[b.file] = (*means, left)
        self.assertEqual(choose_boundaries(
            [a, b], [2.6, 2.6],
            probe=lambda asset, _t: features[asset.file])[0], ("cut",))
        features[b.file] = (*means, (float("nan"),) * 18)
        self.assertEqual(choose_boundaries(
            [a, b], [2.6, 2.6],
            probe=lambda asset, _t: features[asset.file])[0], ("fade",))
        features[b.file] = (*means, (0.0,) * 3)
        self.assertEqual(choose_boundaries(
            [a, b], [2.6, 2.6],
            probe=lambda asset, _t: features[asset.file])[0], ("fade",))

    def test_no_unjustified_cut_for_missing_remote_images_or_ambiguity(self):
        a = video("a.mp4")
        b = video("b.mp4")
        image = MediaAsset(Path("photo.jpg"), "image", "local", "user",
                           "user", "photo")
        remote = replace(b, source_url="https://commons.wikimedia.org")
        for second in (b, image, remote, None):
            output, _ = choose_boundaries([a, second], [2.6, 2.6],
                                          probe=lambda _a, _t: None)
            self.assertEqual(output, ("fade",))
        no_data = Mock(side_effect=OSError("decoder unavailable"))
        output, notes = choose_boundaries([a, b], [2.6, 2.6], probe=no_data)
        self.assertEqual(output, ("fade",))
        self.assertEqual(notes, [])
        dark, _ = choose_boundaries([a, b], [2.6, 2.6],
                                    probe=lambda _a, _t: (3.0, 3.0, 3.0))
        self.assertEqual(dark, ("fade",))
        self.assertEqual(choose_boundaries([a], [2.6])[0], ())
        with self.assertRaises(ValueError):
            choose_boundaries([a, b], [2.6])
        with self.assertRaises(ValueError):
            choose_boundaries([a, b], [2.6, float("nan")])

    def test_only_supported_xfade_options_are_embedded(self):
        self.assertIn("transition=custom", xfade_transition_spec("cut"))
        self.assertIn("if(lte(P,0.5),B,A)", xfade_transition_spec("cut"))
        self.assertEqual(xfade_transition_spec("fade"), "transition=fade")
        self.assertEqual(xfade_transition_spec("wipeleft"), "transition=wipeleft")
        for name in ("auto", "dissolve", "custom;delete", ""):
            with self.assertRaises(ValueError):
                xfade_transition_spec(name)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Requires FFmpeg/ffprobe real footage tests")
class SmartTransitionRealVideoTests(unittest.TestCase):
    @staticmethod
    def make_movie(path: Path, rgb: str) -> None:
        ffmpeg("-f", "lavfi", "-i",
               f"color=c={rgb}:s=180x320:r=12:d=4.5",
               "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))

    def test_real_color_means_choose_two_different_transitions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tones = ("0x808078", "0x85857e", "0x3c70d0")
            clips = [root / f"clip{i}.mp4" for i in range(3)]
            for clip, color in zip(clips, tones):
                self.make_movie(clip, color)
            modes, notes = choose_boundaries(
                [video(str(path)) for path in clips], [2.6] * 3)
            self.assertEqual(modes, ("cut", "fade"))
            self.assertEqual(len(notes), 1)
            self.assertIsNone(boundary_rgb(video(str(root / "missing.mp4")), 0))

    def test_real_opposing_brightness_regions_use_fade_not_false_cut(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = root / "bright-left.mp4"
            b = root / "bright-right.mp4"
            for path, first, second in ((a, "white", "black"),
                                        (b, "black", "white")):
                ffmpeg("-f", "lavfi", "-i",
                       "color=c=gray:s=360x640:r=12:d=4.5",
                       "-vf", ("drawbox=x=0:y=0:w=180:h=640:color="
                               + first + ":t=fill,"
                               "drawbox=x=180:y=0:w=180:h=640:color="
                               + second + ":t=fill"),
                       "-an", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", str(path))
            first_features = boundary_features(video(str(a)), 2.37)
            second_features = boundary_features(video(str(b)), 0.18)
            self.assertIsNotNone(first_features)
            self.assertIsNotNone(second_features)
            self.assertLess(
                sum(abs(x - y) for x, y in zip(
                    first_features[:3], second_features[:3])) / 3, 24)
            self.assertGreater(
                sum(abs(x - y) for x, y in zip(
                    first_features[3], second_features[3])) / 18, 26)
            self.assertEqual(
                choose_boundaries([video(str(a)), video(str(b))],
                                  [2.6, 2.6])[0], ("fade",))
            self.assertEqual(len(boundary_rgb(video(str(a)), 1.0)), 3)
            self.assertIsNone(boundary_features(video(str(root / "missing.mp4")), 0))

    def test_both_onepass_and_legacy_render_mixed_cut_fade_and_keep_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "media"
            media.mkdir()
            for name, rgb in (("001_a.mp4", "0x808078"),
                              ("002_b.mp4", "0x85857e"),
                              ("003_c.mp4", "0x3c70d0")):
                self.make_movie(media / name, rgb)
            for fast in (True, False):
                with self.subTest(fast=fast):
                    result = root / f"output-{fast}.mp4"
                    details = free_studio.render(
                        "Trois paysages", "Premier plan. Deuxième plan. Troisième plan.",
                        result, media_dir=media, mute=True, resolution=360, fps=12,
                        caption_style="classic", transition="auto",
                        visual_style="cinematic", use_media_onepass=fast,
                        use_fused_xfade=fast)
                    self.assertEqual(details["scene_transitions"], ["cut", "fade"])
                    self.assertEqual(details["scene_transition"], "fade")
                    self.assertEqual(len(details["media"]), 3)
                    self.assertTrue(result.is_file())
                    measured = video_probe(result)
                    self.assertAlmostEqual(float(measured["format"]["duration"]),
                                           float(details["duration_seconds"]), delta=0.15)
                    saved = json.loads(result.with_suffix(".json").read_text())
                    self.assertEqual(saved["scene_transitions"], ["cut", "fade"])

    def test_custom_cut_chooses_outgoing_then_incoming_pixels_at_center(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one, two, output = (root / x for x in ("red.mp4", "blue.mp4", "mix.mp4"))
            self.make_movie(one, "red")
            self.make_movie(two, "blue")
            ffmpeg("-i", str(one), "-i", str(two),
                   "-filter_complex_threads", "1", "-filter_complex",
                   "[0:v][1:v]xfade=transition=custom:expr='if(lte(P,0.5),B,A)':"
                   "duration=0.333333:offset=2.833333[out]",
                   "-map", "[out]", "-t", "5.666667",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output))
            def sample(t: float):
                data = subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(t), "-i",
                     str(output), "-vf", "scale=1:1:flags=area",
                     "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                    capture_output=True, check=True, timeout=25).stdout
                self.assertEqual(len(data), 3)
                return tuple(data)
            before, after = sample(2.8), sample(3.1)
            self.assertGreater(before[0], before[2] + 80)
            self.assertGreater(after[2], after[0] + 80)


if __name__ == "__main__":
    unittest.main()
