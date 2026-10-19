"""Deterministic chronology and actual decoded FFmpeg video-frame coverage."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from media_assets import MediaAsset
import free_studio
import fast_media
import video_qa
from video_timeline import (
    plan_local_video_timeline, probe_clip_duration, source_seek_seconds,
)


def local(path: Path) -> MediaAsset:
    return MediaAsset(path, "video", "local", "user-provided", "user", path.name)


def ffmpeg(*args: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        text=True, capture_output=True, timeout=90,
    )
    if result.returncode:
        raise AssertionError(result.stderr[-2000:])


class TimelinePlannerTests(unittest.TestCase):
    def test_repeated_clip_is_sequential_even_when_other_media_intervenes(self):
        a = local(Path("a.mp4"))
        b = local(Path("b.mp4"))
        still = MediaAsset(Path("still.jpg"), "image", "local", "user", "user", "still")
        clips = [a, still, a, b, a, b, a]
        probe = Mock(side_effect=lambda p: 9.0 if p == a.file else 20.0)
        planned, notes = plan_local_video_timeline(
            clips, [2.6, 3.0, 2.6, 2.6, 2.6, 2.6, 2.6], probe=probe)
        self.assertEqual([round(source_seek_seconds(s), 3) for s in planned],
                         [0.0, 0.0, 2.6, 0.0, 5.2, 2.6, 7.8])
        self.assertIs(planned[1], still)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(notes, [])
        self.assertEqual(a.source_start_seconds, 0.0)  # frozen source unchanged

    def test_short_video_wraps_without_seeking_past_end(self):
        clip = local(Path("short.mp4"))
        planned, notes = plan_local_video_timeline(
            [clip] * 4, [2.6] * 4, probe=lambda _: 3.0)
        self.assertEqual([s.source_start_seconds for s in planned],
                         [0.0, 2.6, 2.2, 1.8])
        self.assertEqual(notes, [])

    def test_failed_duration_probe_preserves_unrelated_assets_and_warns_once(self):
        source = local(Path("unknown.mp4"))
        still = MediaAsset(Path("a.jpg"), "image", "local", "user", "user", "still")
        probe = Mock(side_effect=OSError("invalid moov"))
        planned, notes = plan_local_video_timeline(
            [source, still, source], [2.6, 3.0, 2.6], probe=probe)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual([s.source_start_seconds for s in (planned[0], planned[2])],
                         [0.0, 0.0])
        self.assertIs(planned[1], still)
        self.assertEqual(len(notes), 1)
        self.assertIn("unknown.mp4", notes[0])

    def test_remote_photographs_are_never_searched_for_clip_duration(self):
        remote = MediaAsset(Path("photo.jpg"), "image", "https://commons.wikimedia.org",
                            "CC0", "author", "photo")
        other = MediaAsset(Path("clip.mp4"), "video", "https://remote.example",
                           "CC0", "author", "clip")
        probe = Mock(side_effect=AssertionError("No probes for remote media"))
        result, notes = plan_local_video_timeline(
            [remote, other, None], [2.6] * 3, probe=probe)
        self.assertEqual(result, [remote, other, None])
        self.assertEqual(notes, [])
        probe.assert_not_called()

    def test_invalid_seek_and_timeline_rejected(self):
        for bad in (float("nan"), float("inf"), -0.1, 86401, True, "1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                source_seek_seconds(type("Clip", (), {"source_start_seconds": bad})())
        source = local(Path("a.mp4"))
        for broken in ([0], [float("nan")], [True]):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                plan_local_video_timeline([source], broken, probe=lambda _: 4)
        with self.assertRaises(ValueError):
            plan_local_video_timeline([source], [2.6, 3.0])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg and ffprobe required")
class RealTimelineVideoTests(unittest.TestCase):
    def test_probed_duration_and_actual_rgb_frames_across_engines(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_dir = root / "media"
            source_dir.mkdir()
            source = source_dir / "one-continuous-take.mp4"
            args = []
            for color in ("red", "green", "blue"):
                args += ["-f", "lavfi", "-i",
                         f"color=c={color}:s=320x240:r=12:d=3"]
            ffmpeg(*args, "-filter_complex",
                   "[0:v][1:v][2:v]concat=n=3:v=1:a=0[out]",
                   "-map", "[out]", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", str(source))
            self.assertAlmostEqual(probe_clip_duration(source), 9.0, delta=0.1)

            for use_onepass in (True, False):
                with self.subTest(use_onepass=use_onepass):
                    video = root / f"result-{use_onepass}.mp4"
                    info = free_studio.render(
                        "Continuité visuelle",
                        "Première séquence. Deuxième séquence. Troisième séquence.",
                        video, media_dir=source_dir, mute=True, resolution=360,
                        fps=12, caption_style="classic", transition="cut",
                        use_media_onepass=use_onepass)
                    offsets = [m["source_start_seconds"] for m in info["media"]]
                    self.assertEqual(offsets, [0.0, 2.6, 5.2])
                    expected = ("direct-media-onepass" if use_onepass else "cut-concat")
                    self.assertEqual(
                        info["render_performance"]["compositing_pipeline"], expected)
                    self.assertTrue(video_qa.validate(
                        video, info, video.with_suffix(".srt"),
                        video_qa.probe(video))["ok"])
                    saved = json.loads(video.with_suffix(".json").read_text())
                    self.assertEqual([m["source_start_seconds"] for m in saved["media"]],
                                     offsets)
                    # Actual output pixels, not merely command arguments:
                    # scene 1 from 0–3s = red; scene 2 from 3–6s = green;
                    # scene 3 from 6–9s = blue.
                    for index, channel in enumerate((0, 1, 2)):
                        point = (float(info["segments"][index]["start"]) +
                                 float(info["segments"][index]["end"])) / 2
                        frame = subprocess.run(
                            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel",
                             "error", "-ss", f"{point:.3f}", "-i", str(video),
                             "-vf", "scale=1:1:flags=area", "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                            capture_output=True, timeout=45, check=True).stdout
                        self.assertEqual(len(frame), 3)
                        self.assertGreater(frame[channel], max(
                            frame[(channel + 1) % 3], frame[(channel + 2) % 3]) * 1.4,
                            (use_onepass, index, list(frame)))


if __name__ == "__main__":
    unittest.main()
