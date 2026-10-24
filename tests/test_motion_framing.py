"""Conservative movement-based crop placement, measured with real FFmpeg MP4s."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import fast_media
import free_studio
from media_assets import MediaAsset, local_assets
import motion_framing as framing
import video_qa


def source_asset(name="shot.mp4", *, w=640, h=480, seek=0.) -> MediaAsset:
    return MediaAsset(
        file=Path(name), kind="video", source_url="local",
        license="user-provided", author="user", title=name,
        source_width=w, source_height=h, source_start_seconds=seek)


def fake_motion_frames(x_positions):
    width = 120
    height = 68
    frames = bytearray()
    for x in x_positions:
        frame = bytearray([110] * (width * height))
        for row in range(18, 51):
            for col in range(x, x + 13):
                if 0 <= col < width:
                    frame[row * width + col] = 210
        frames.extend(frame)
    return bytes(frames)


def ffmpeg(*parts, timeout=85):
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         *parts], capture_output=True, timeout=timeout)
    if result.returncode:
        raise AssertionError(result.stderr[-1400:].decode("utf-8", "replace"))
    return result


def pixel(path, x, y, *, seconds=.25):
    raw = ffmpeg(
        "-ss", str(seconds), "-i", str(path), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1", timeout=45).stdout
    if len(raw) != 360 * 640 * 3:
        raise AssertionError("Video frame is missing or wrong-sized")
    i = (y * 360 + x) * 3
    return tuple(raw[i:i+3])


class MotionFramingUnitTests(unittest.TestCase):
    def test_only_known_moderately_wide_local_videos_qualify(self):
        eligible = source_asset()
        self.assertTrue(framing.eligible(eligible, width=360, height=640))
        self.assertFalse(framing.eligible(source_asset(w=1280, h=720),
                                          width=360, height=640))
        self.assertFalse(framing.eligible(source_asset(w=360, h=640),
                                          width=360, height=640))
        self.assertFalse(framing.eligible(source_asset(w=320, h=560),
                                          width=360, height=640))
        self.assertFalse(framing.eligible(source_asset(w=None, h=480),
                                          width=360, height=640))
        self.assertFalse(framing.eligible(source_asset(w=True, h=480),
                                          width=360, height=640))
        self.assertFalse(framing.eligible(replace(eligible, source_url="commons"),
                                          width=360, height=640))
        self.assertFalse(framing.eligible(replace(eligible, kind="image"),
                                          width=360, height=640))

    def test_motion_on_either_side_moves_only_stable_camera_anchor(self):
        right = fake_motion_frames([91, 101, 94, 108, 96, 104])
        left = fake_motion_frames([17, 9, 15, 4, 14, 6])
        r = framing.motion_anchor(right, retention=.421875)
        l = framing.motion_anchor(left, retention=.421875)
        self.assertIsNotNone(r)
        self.assertIsNotNone(l)
        self.assertGreater(r, .69)
        self.assertLess(l, .31)
        self.assertLessEqual(r, .88)
        self.assertGreaterEqual(l, .12)
        a = replace(source_asset(), crop_horizontal_anchor=r)
        self.assertIn("x=(in_w-out_w)*", framing.crop_filter(
            a, width=360, height=640))
        self.assertNotIn("x=(in_w-out_w)", framing.crop_filter(
            source_asset(), width=360, height=640))
        self.assertEqual(framing.crop_filter(a, width=360, height=640),
                         framing.crop_filter(a, width=360, height=640))

    def test_unknown_static_brightness_flicker_or_two_subjects_stay_center(self):
        constant = bytes([110] * (120 * 68 * 5))
        self.assertIsNone(framing.motion_anchor(constant, retention=.42))
        flashes = b"".join(bytes([90 if index % 2 else 145] * (120 * 68))
                           for index in range(6))
        self.assertIsNone(framing.motion_anchor(flashes, retention=.42))
        frames = []
        for index, x in enumerate([5, 11, 7, 14, 6, 13]):
            frame = bytearray([110] * (120 * 68))
            for row in range(18, 50):
                for left in range(x, x+10):
                    frame[row * 120 + left] = 210
                    frame[row * 120 + (119-left)] = 210
            frames.extend(frame)
        self.assertIsNone(framing.motion_anchor(bytes(frames), retention=.42))
        self.assertIsNone(framing.motion_anchor(b"broken", retention=.42))
        self.assertIsNone(framing.motion_anchor(constant, retention=float("nan")))
        self.assertIsNone(framing.motion_anchor(constant, retention=.10))

    def test_stationary_edge_strobe_is_not_mistaken_for_subject_travel(self):
        # A neon sign alternates brightness but never CHANGES LOCATION.
        # Difference-energy alone reports a strong consistent edge signal;
        # the temporal activity centroid must also move >= 3 sample pixels.
        frames = bytearray()
        for index in range(6):
            frame = bytearray([110] * (120 * 68))
            if index % 2:
                for row in range(18, 51):
                    for x in range(94, 108):
                        frame[row * 120 + x] = 210
            frames.extend(frame)
        self.assertIsNone(framing.motion_anchor(bytes(frames), retention=.421875))
        # Actual traveling athletes remain eligible, including both sides.
        self.assertGreater(framing.motion_anchor(
            fake_motion_frames([91, 101, 94, 108, 96, 104]),
            retention=.421875), .70)
        self.assertLess(framing.motion_anchor(
            fake_motion_frames([17, 9, 15, 4, 14, 6]),
            retention=.421875), .30)

    def test_invalid_crop_values_rejected_not_injected_into_ffmpeg(self):
        original = source_asset()
        for candidate in (None, True, float("nan"), -1., 1.0, "0;evil",
                          float("inf")):
            with self.subTest(anchor=candidate):
                asset = replace(original, crop_horizontal_anchor=candidate)
                self.assertEqual(framing.crop_filter(
                    asset, width=360, height=640), "crop=360:640")
        self.assertEqual(framing.crop_filter(
            replace(original, crop_horizontal_anchor=.88),
            width=720, height=1280),
            "crop=720:1280:x=(in_w-out_w)*0.8800:y=(in_h-out_h)/2")

    def test_sampling_is_bounded_and_fail_closed(self):
        source = Path("/tmp/test local source.mp4")
        with patch.object(framing.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = fake_motion_frames(
                [91, 101, 94, 108, 96, 104])
            result = framing.probe_motion(
                source, seek=2.0, duration=3.0, retention=.421875)
            self.assertIsNotNone(result)
            self.assertEqual(run.call_count, 1)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("-frames:v")+1], "6")
            self.assertEqual(args[args.index("-ss")+1], "2.000")
            self.assertIn("scale=120:68", " ".join(args))
            self.assertEqual(args[-1], "pipe:1")
            self.assertEqual(run.call_args.kwargs["timeout"], 18)
            run.return_value.stdout = b"x" * (120*68*7)
            self.assertIsNone(framing.probe_motion(
                source, seek=0., duration=3., retention=.42))
        for seek, duration in ((-1, 2), (0, 8), (0, .3),
                               (float("nan"), 2)):
            with self.subTest(seek=seek, duration=duration):
                self.assertIsNone(framing.probe_motion(
                    source, seek=seek, duration=duration, retention=.42))

    def test_scene_analysis_cached_and_capped_at_four_decode_calls(self):
        samples = [source_asset(f"{i}.mp4") for i in range(7)]
        calls = []
        def probe(path, **kwargs):
            calls.append((path, kwargs))
            return .8
        chosen, notices = framing.plan_motion_crops(
            samples, [2.5] * 7, width=360, height=640, probe=probe)
        self.assertEqual(len(calls), 4)
        self.assertEqual([s.crop_horizontal_anchor for s in chosen[:4]],
                         [.8] * 4)
        self.assertTrue(all(s.crop_horizontal_anchor is None
                            for s in chosen[4:]))
        self.assertEqual(samples[0].crop_horizontal_anchor, None)
        self.assertIn("4 local video", notices[0])
        repeated, _ = framing.plan_motion_crops(
            [samples[0]]*5, [2.5]*5, width=360, height=640, probe=probe)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(s.crop_horizontal_anchor == .8 for s in repeated))

    def test_bad_probe_never_edits_source_or_crashes_render(self):
        original = source_asset()
        for response in (None, float("nan"), .01, "bad", True):
            chosen, info = framing.plan_motion_crops(
                [original], [2.0], width=360, height=640,
                probe=lambda *a, **k: response)
            self.assertEqual(chosen[0], original)
            self.assertEqual(info, [])
        with self.assertRaises(ValueError):
            framing.plan_motion_crops(
                [original], [0], width=360, height=640)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg required")
class RealMotionFramingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motion-framing-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        self.media.mkdir()
        self.source = self.media / "moving-subject.mp4"
        # Gray stationary background and only one red moving block near RIGHT.
        # A center crop of 4:3 to 9:16 loses that block completely.
        ffmpeg(
            "-f", "lavfi", "-i", "color=c=gray:s=640x480:r=12:d=3.5",
            "-f", "lavfi", "-i", "color=c=red:s=70x180:r=12:d=3.5",
            "-filter_complex",
            "[0:v][1:v]overlay=x='520+35*sin(t*4)':y=140:shortest=1,"
            "format=yuv420p",
            "-frames:v", "42", "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "16",
            str(self.source), timeout=45)
        self.original_bytes = self.source.read_bytes()
        assets, notes = local_assets(self.media)
        self.assertFalse(notes)
        self.assertEqual(len(assets), 1)
        self.asset = assets[0]

    def render_direct(self, asset, target):
        command = fast_media.build_onepass_media_command(
            [asset], [3.0], width=360, height=640, fps=12,
            transition="cut", visual_filters=["format=yuv420p"],
            audio=None, output=target, total=3.0)
        ffmpeg(*command[1:])
        info = video_qa.probe(target)
        video = next(stream for stream in info["streams"]
                     if stream["codec_type"] == "video")
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(video["pix_fmt"], "yuv420p")
        return command

    def test_real_right_side_activity_recovered_in_both_encode_paths(self):
        chosen, notices = framing.plan_motion_crops(
            [self.asset], [3.0], width=360, height=640)
        anchor = chosen[0].crop_horizontal_anchor
        self.assertIsNotNone(anchor, notices)
        self.assertGreater(anchor, .75)
        self.assertLessEqual(anchor, .88)
        centered = self.root / "center.mp4"
        smart = self.root / "guided.mp4"
        self.render_direct(self.asset, centered)
        command = self.render_direct(chosen[0], smart)
        graph = command[command.index("-filter_complex")+1]
        self.assertIn("x=(in_w-out_w)*", graph)
        unshifted = pixel(centered, 320, 320)
        recovered = pixel(smart, 320, 320)
        self.assertLess(abs(unshifted[0]-unshifted[1]), 20, unshifted)
        self.assertGreater(recovered[0], 170, recovered)
        self.assertLess(recovered[1], 75, recovered)
        legacy = free_studio.render_scene(
            chosen[0], 3.0, 0, self.root, width=360, height=640, fps=12)
        returned = pixel(legacy, 320, 320)
        self.assertGreater(returned[0], 170, returned)
        self.assertLess(returned[1], 75, returned)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames,r_frame_rate",
             "-of", "json", str(smart)], check=True, capture_output=True,
            text=True, timeout=20)
        video = json.loads(probe.stdout)["streams"][0]
        self.assertEqual(int(video["nb_frames"]), 36)
        self.assertEqual(video["r_frame_rate"], "12/1")
        self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_complete_render_reports_applied_camera_choice(self):
        output = self.root / "full-render.mp4"
        info = free_studio.render(
            "Plan", "Le sujet bouge sur le côté droit de la scène.",
            output, mute=True, fps=12, resolution=360,
            caption_style="classic", media_dir=self.media,
            transition="cut")
        self.assertEqual(info["visual_mode"], "media")
        self.assertEqual(info["media"][0]["render_framing"],
                         "motion-guided-horizontal-crop")
        self.assertGreater(info["media"][0]["crop_horizontal_anchor"], .70)
        self.assertTrue(any("Motion-guided horizontal crop" in warning
                            for warning in info["warnings"]))
        self.assertEqual(json.loads(output.with_suffix(".json").read_text())
                         ["media"][0]["render_framing"], "motion-guided-horizontal-crop")
        self.assertEqual(self.source.read_bytes(), self.original_bytes)


if __name__ == "__main__":
    unittest.main()
