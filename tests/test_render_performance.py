"""Renderer and performance regressions; requires installed FFmpeg."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import free_studio


class PerformanceTests(unittest.TestCase):
    def test_single_scene_skips_remux(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            def fake_scene(_asset, _duration, index, folder_path, **kwargs):
                target = folder_path / f"scene_{index:02}.mp4"
                target.write_bytes(b"pretend encoded media")
                return target
            with patch.object(free_studio, "render_scene", side_effect=fake_scene), \
                 patch.object(free_studio, "execute", side_effect=AssertionError("unexpected FFmpeg concat")):
                out = free_studio.prepare_media_background([None], [3.0], work,
                    width=360, height=640, fps=12, transition="fade")
            self.assertEqual(out.read_bytes(), b"pretend encoded media")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_render_reports_stage_times_and_maintains_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "timed.mp4"
            info = free_studio.render("Performance", "Bonjour tout le monde.", target, lang="fr", fps=12,
                mute=True, resolution=360, caption_style="classic")
            perf = info["render_performance"]
            self.assertGreater(perf["total_seconds"], 0)
            for key in ("audio_seconds", "media_selection_seconds", "scene_encoding_seconds", "final_encoding_seconds"):
                self.assertGreaterEqual(perf[key], 0)
            self.assertGreater(target.stat().st_size, 3000)
            self.assertAlmostEqual(free_studio.duration_seconds(target), info["duration_seconds"], delta=0.2)
            self.assertEqual(json.loads(target.with_suffix(".json").read_text())["render_performance"], perf)


if __name__ == "__main__":
    unittest.main()
