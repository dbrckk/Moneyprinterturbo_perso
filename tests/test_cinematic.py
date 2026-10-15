"""Tests for FFmpeg cinematic motion graphics and real rendered outputs."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import cinematic
import free_studio


class CinematicTests(unittest.TestCase):
    def test_deterministic_gradient(self):
        a = cinematic.gradient_source(0, 360, 640, 12)
        self.assertIn("gradients=", a)
        self.assertIn("n=3", a)
        self.assertNotIn("random", a)
        self.assertEqual(a, cinematic.gradient_source(0, 360, 640, 12))
        self.assertNotEqual(a, cinematic.gradient_source(1, 360, 640, 12))
        with self.assertRaises(ValueError):
            cinematic.gradient_source(-1, 360, 640, 12)

    def test_safe_titles_written_to_files(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            scenes = [{"text": "Bonjour. drawtext=x=888", "start": 0, "end": 3}]
            filters = cinematic.build_filters(scenes, [None], work,
                                              width=360, height=640, total=3)
            self.assertTrue((work / "heading_0.txt").exists())
            self.assertNotIn("drawtext=x=888", ",".join(filters))
            self.assertIn("CHAPTER  01", (work / "chapter_0.txt").read_text())
            self.assertIn("01  /  01", (work / "counter_0.txt").read_text())

    def test_media_scene_is_not_obscured_by_duplicate_title(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            scenes = [{"text": "Photo réelle", "start": 0, "end": 2.5}]
            filters = cinematic.build_filters(scenes, [object()], work,
                                              width=360, height=640, total=2.5)
            self.assertFalse((work / "heading_0.txt").exists())
            self.assertTrue((work / "counter_0.txt").exists())
            self.assertTrue(any("vignette=" in f for f in filters))

    def test_rejects_invalid_style_and_preserves_classic(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            scenes = [{"text": "Test", "start": 0, "end": 3}]
            with self.assertRaisesRegex(ValueError, "Unknown visual style"):
                cinematic.build_filters(scenes, [None], work, width=360, height=640, total=3, style="other")
            legacy = cinematic.build_filters(scenes, [None], work,
                                             width=360, height=640, total=3, style="classic")
            self.assertTrue(any("drawgrid=" in f for f in legacy))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_actual_cinematic_render_in_landscape_and_square(self):
        with tempfile.TemporaryDirectory() as folder:
            for aspect, dims in [("16:9", [640, 360]), ("1:1", [360, 360])]:
                with self.subTest(aspect=aspect):
                    target = Path(folder) / ("wide.mp4" if aspect == "16:9" else "square.mp4")
                    info = free_studio.render("Paysage", "Bonjour. Voici la montagne.", target,
                                              aspect=aspect, resolution=360, fps=12, mute=True)
                    self.assertEqual(info["size"], dims)
                    self.assertEqual(info["visual_style"], "cinematic")
                    self.assertGreater(target.stat().st_size, 2500)
                    self.assertGreater(free_studio.duration_seconds(target), 4)
                    self.assertTrue(target.with_suffix(".srt").is_file())
                    self.assertEqual(json.loads(target.with_suffix(".json").read_text())["visual_style"], "cinematic")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_classic_export_still_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "classic.mp4"
            info = free_studio.render("QA", "Un exemple.", target,
                                      resolution=360, fps=12, mute=True, visual_style="classic")
            self.assertEqual(info["visual_style"], "classic")
            self.assertGreater(target.stat().st_size, 2000)


if __name__ == "__main__":
    unittest.main()
