"""Preserve entire moving local landscape videos without freezing the footage.

Checks deterministic framing policy, fail-closed unknown metadata, real H.264
pixels and frame counts through BOTH single-pass and legacy media rendering.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import fast_media
import free_studio
import scene_framing
from media_assets import local_assets


def ffmpeg(*parts: str, timeout: int = 100):
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         *parts], capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise AssertionError(result.stderr[-1700:].decode("utf-8", errors="replace"))
    return result


def decoded_frame(video: Path, at: float) -> bytes:
    return ffmpeg(
        "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
        "-vf", "scale=360:640:flags=bicubic,format=rgb24",
        "-f", "rawvideo", "pipe:1", timeout=50).stdout


def pixel(data: bytes, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * 360 + x) * 3
    return tuple(data[offset:offset + 3])


class VideoFramingPolicyTests(unittest.TestCase):
    @staticmethod
    def asset(width, height, *, kind="video"):
        return SimpleNamespace(kind=kind, source_width=width, source_height=height,
                               crop_retention_estimate=None)

    def test_ultra_wide_or_tall_local_video_uses_matte(self):
        for w, h, dest_w, dest_h in (
            (960, 180, 360, 640),
            (1280, 720, 360, 640),
            (360, 640, 640, 360),
        ):
            with self.subTest(source=(w, h), output=(dest_w, dest_h)):
                self.assertTrue(scene_framing.needs_video_soft_matte(
                    self.asset(w, h), width=dest_w, height=dest_h))
        for w, h in ((360, 640), (720, 1280), (640, 720), (720, 960)):
            self.assertFalse(scene_framing.needs_video_soft_matte(
                self.asset(w, h), width=360, height=640))
        self.assertFalse(scene_framing.needs_video_soft_matte(
            self.asset(960, 180, kind="image"), width=360, height=640))
        self.assertFalse(scene_framing.needs_soft_matte(
            self.asset(960, 180)), "Photo-based matte heuristic remains separate")

    def test_invalid_unverified_dimensions_fail_closed_without_ffmpeg(self):
        bad = [(None, 180), (960, None), (True, 180), ("960", 180),
               (960, 0), (float("nan"), 200), (999999, 180)]
        for w, h in bad:
            with self.subTest(dimensions=(w, h)):
                self.assertFalse(scene_framing.needs_video_soft_matte(
                    self.asset(w, h), width=360, height=640))
        for w, h in ((True, 640), (360, "640"), (361, 640), (360, 0)):
            self.assertFalse(scene_framing.needs_video_soft_matte(
                self.asset(960, 180), width=w, height=h))

    def test_graph_uses_single_video_fps_clock_and_no_photo_zoom(self):
        graph = scene_framing.video_soft_matte_graph(
            0, width=360, height=640, fps=12,
            prefix="vid0", output_name="s0",
            tail_filters="trim=end_frame=30,setpts=N/(12*TB),fps=12")
        self.assertEqual(len(graph), 3)
        self.assertIn("[0:v]fps=12,setsar=1,split=2", graph[0])
        self.assertIn("scale=32:40", graph[1])
        self.assertIn("overlay=(W-w)/2:(H-h)/2:shortest=1", graph[2])
        self.assertNotIn("zoompan", ";".join(graph))
        self.assertTrue(graph[2].endswith("[s0]"))
        for bad in ("a;b", "a[0]", "a b", "", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                scene_framing.video_soft_matte_graph(
                    0, width=360, height=640, fps=12,
                    prefix=bad, output_name="end")
        for overrides in (
            {"source_index": True}, {"width": 361}, {"height": -1},
            {"fps": 100}, {"color_filters": "eq=x;[evil]"},
            {"tail_filters": "scale=1:1;[evil]"},
        ):
            options = dict(source_index=0, width=360, height=640,
                           fps=12, prefix="safe", output_name="done")
            options.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                scene_framing.video_soft_matte_graph(**options)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg/FFprobe required")
class RealVideoMatteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.media = self.work / "sources"
        self.media.mkdir()
        self.source = self.media / "ultra-wide.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=960x180:r=12:d=3.2",
               "-vf", "drawbox=x=0:y=0:w=250:h=180:color=blue:t=fill,"
                      "drawbox=x=710:y=0:w=250:h=180:color=green:t=fill",
               "-an", "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", str(self.source))
        self.original = self.source.read_bytes()
        assets, problems = local_assets(self.media)
        self.assertFalse(problems)
        self.assertEqual(len(assets), 1)
        self.asset = assets[0]

    def assert_video_pixels(self, movie: Path, *, frames: int):
        meta = json.loads(subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt,nb_frames,avg_frame_rate",
            "-of", "json", str(movie)], capture_output=True,
            text=True, timeout=20, check=True).stdout)["streams"][0]
        self.assertEqual(meta["codec_name"], "h264")
        self.assertEqual(meta["pix_fmt"], "yuv420p")
        self.assertEqual(int(meta["nb_frames"]), frames)
        self.assertEqual(meta["avg_frame_rate"], "12/1")
        first = decoded_frame(movie, 0.25)
        later = decoded_frame(movie, 1.5)
        self.assertEqual(len(first), 360 * 640 * 3)
        # Original first and last 250 source columns must remain in the
        # output's central hero image (not only the softly blurred backdrop).
        blue = pixel(first, 55, 320)
        green = pixel(first, 300, 320)
        self.assertGreater(blue[2], 110, blue)
        self.assertGreater(green[1], 65, green)
        self.assertLess(blue[1], 80, blue)
        self.assertLess(green[2], 90, green)
        # Fine content is still moving: no freeze from photo-only zoompan.
        x1, y1, x2, y2 = 130, 295, 225, 345
        total = 0
        for y in range(y1, y2, 3):
            for x in range(x1, x2, 3):
                i = (y * 360 + x) * 3
                total += sum(abs(first[i+j] - later[i+j]) for j in range(3))
        self.assertGreater(total, 2000, "Foreground video unexpectedly froze")

    def test_singlepass_and_legacy_preserve_full_scene_and_input(self):
        self.assertEqual((self.asset.source_width, self.asset.source_height),
                         (960, 180))
        self.assertTrue(scene_framing.needs_video_soft_matte(
            self.asset, width=360, height=640))
        single = self.work / "onepass.mp4"
        command = fast_media.build_onepass_media_command(
            [self.asset], [2.5], width=360, height=640, fps=12,
            transition="cut", visual_filters=["format=yuv420p"],
            audio=None, output=single, total=2.5)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("fps=12,setsar=1,split=2", graph)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", graph)
        ffmpeg(*command[1:])
        self.assert_video_pixels(single, frames=30)
        legacy = free_studio.render_scene(
            self.asset, 2.5, 0, self.work, width=360, height=640, fps=12)
        self.assert_video_pixels(legacy, frames=30)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_two_moving_clips_fade_remains_frame_accurate(self):
        output = self.work / "two-fades.mp4"
        command = fast_media.build_onepass_media_command(
            [self.asset, self.asset], [1.5, 1.5],
            width=360, height=640, fps=12, transition="fade",
            visual_filters=["format=yuv420p"], audio=None,
            output=output, total=3)
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(graph.count("split=2"), 2)
        self.assertEqual(graph.count("xfade="), 1)
        ffmpeg(*command[1:])
        self.assertGreater(output.stat().st_size, 5000)
        profile = json.loads(subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=nb_frames", "-of", "json", str(output)
        ], capture_output=True, text=True, timeout=20, check=True).stdout)
        self.assertEqual(int(profile["streams"][0]["nb_frames"]), 36)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_centered_portrait_video_keeps_existing_singlepass_path(self):
        portrait = self.media / "portrait.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=180x320:r=12:d=2",
               "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(portrait))
        assets, notes = local_assets(self.media)
        self.assertFalse(notes)
        vertical = next(x for x in assets if x.file == portrait.resolve())
        self.assertFalse(scene_framing.needs_video_soft_matte(
            vertical, width=360, height=640))
        command = fast_media.build_onepass_media_command(
            [vertical], [1.5], width=360, height=640, fps=12,
            transition="cut", visual_filters=["format=yuv420p"],
            audio=None, output=self.work / "portrait-out.mp4", total=1.5)
        graph = command[command.index("-filter_complex") + 1]
        self.assertNotIn("split=2", graph)
        self.assertIn("crop=360:640", graph)
        before = portrait.read_bytes()
        ffmpeg(*command[1:])
        self.assertEqual(portrait.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
