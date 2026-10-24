"""End-to-end longitudinal crop safety, with real decoded nine-second MP4s.

The synthetic athlete travels between opposing edges. A fixed crop inferred
from only the first 3s will discard the second part, so the renderer should
choose full-footage soft matte rather than pretending to track a person.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import fast_media
import free_studio
from media_assets import local_assets
from motion_framing import plan_motion_crops
from scene_framing import needs_video_soft_matte


def run(*args: str, timeout: int = 120) -> bytes:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         *args], capture_output=True, timeout=timeout, check=False)
    if process.returncode:
        raise AssertionError(process.stderr[-2000:].decode("utf-8", "replace"))
    return process.stdout


def marker_fraction(path: Path, seconds: float) -> float:
    pixels = run("-ss", f"{seconds:.3f}", "-i", str(path), "-frames:v", "1",
                 "-vf", "scale=90:160:flags=area,format=rgb24",
                 "-f", "rawvideo", "pipe:1", timeout=45)
    if len(pixels) != 90 * 160 * 3:
        raise AssertionError("Missing exact decoded test frame")
    # Synthetic red block only; no inference about real-world humans.
    hits = sum(pixels[i] > 140 and pixels[i + 1] < 100
               and pixels[i + 2] < 100
               for i in range(0, len(pixels), 3))
    return hits / (90 * 160)


def encoded_frames(path: Path) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,nb_frames,avg_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=25, check=True)
    props = json.loads(result.stdout)["streams"][0]
    if (props["codec_name"] != "h264" or props["width"] != 360
            or props["height"] != 640 or props["avg_frame_rate"] != "12/1"):
        raise AssertionError(f"Invalid H.264 test stream: {props}")
    return int(props["nb_frames"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg/FFprobe required")
class RealLongMotionFramingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mpt-long-crop-")
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.media = self.work / "local"
        self.media.mkdir()
        self.switching = self.media / "switching-athlete.mp4"
        run("-f", "lavfi", "-i", "color=c=gray:s=640x480:r=12:d=9",
            "-f", "lavfi", "-i", "color=c=red:s=70x180:r=12:d=9",
            "-filter_complex",
            "[0:v][1:v]overlay=x='if(lt(t,4.5),520+35*sin(t*4),"
            "45+30*sin(t*4))':y=140:shortest=1,format=yuv420p[v]",
            "-map", "[v]", "-frames:v", "108", "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
            str(self.switching), timeout=60)
        self.original = self.switching.read_bytes()
        assets, warnings = local_assets(self.media)
        self.assertEqual(warnings, [])
        self.assertEqual(len(assets), 1)
        self.asset = assets[0]

    def test_end_to_end_reversal_preserves_both_edge_subjects(self):
        choices, notes = plan_motion_crops(
            [self.asset], [9.0], width=360, height=640)
        adaptive = choices[0]
        self.assertTrue(adaptive.preserve_full_video_frame, notes)
        self.assertTrue(needs_video_soft_matte(adaptive, width=360, height=640))
        self.assertIsNone(adaptive.crop_horizontal_anchor)
        self.assertTrue(any("Preserved full video frame" in n for n in notes))
        self.assertFalse(self.asset.preserve_full_video_frame)

        center = self.work / "old-center.mp4"
        full = self.work / "new-full.mp4"
        for asset, destination in ((self.asset, center), (adaptive, full)):
            cmd = fast_media.build_onepass_media_command(
                [asset], [9.0], width=360, height=640, fps=12,
                transition="cut", visual_filters=["format=yuv420p"],
                audio=None, output=destination, total=9.)
            graph = cmd[cmd.index("-filter_complex") + 1]
            if asset is adaptive:
                self.assertIn("split=2", graph)
                self.assertIn("overlay=(W-w)/2:(H-h)/2", graph)
            else:
                self.assertNotIn("split=2", graph)
            run(*cmd[1:], timeout=120)
            self.assertEqual(encoded_frames(destination), 108)

        for second in (1.0, 7.0):
            with self.subTest(time=second):
                before = marker_fraction(center, second)
                after = marker_fraction(full, second)
                self.assertLess(before, .001, (second, before))
                self.assertGreater(after, .012, (second, after))
        legacy = free_studio.render_scene(
            adaptive, 9.0, 0, self.work, width=360, height=640, fps=12)
        self.assertEqual(encoded_frames(legacy), 108)
        for second in (1.0, 7.0):
            self.assertGreater(marker_fraction(legacy, second), .012)
        self.assertEqual(self.switching.read_bytes(), self.original)

    def test_long_scene_analyzer_is_still_bounded_to_two_small_windows(self):
        calls = []
        def fake_probe(path, *, seek, duration, retention):
            calls.append((path, seek, duration, retention))
            return .8 if seek < 5 else .2
        choices, _ = plan_motion_crops(
            [self.asset] * 3, [9.] * 3,
            width=360, height=640, probe=fake_probe)
        self.assertEqual(len(calls), 2)
        self.assertEqual([p[1] for p in calls], [0., 6.])
        self.assertTrue(all(asset.preserve_full_video_frame for asset in choices))


if __name__ == "__main__":
    unittest.main()
