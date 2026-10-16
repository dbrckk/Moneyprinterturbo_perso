"""Real-world proof of abstract single-pass composition and compatibility."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import fast_abstract
import free_studio
import video_qa


class OnePassTests(unittest.TestCase):
    def test_invalid_dimensions_and_missing_overlays(self):
        kwargs = dict(durations=[2.0, 2.0], width=360, height=640,
                      fps=12, transition="fade", visual_filters=["format=yuv420p"],
                      audio=None, output=Path("ignored.mp4"), total=4.0)
        with self.assertRaises(ValueError):
            fast_abstract.build_onepass_abstract_command(**(kwargs | {"width": 361}))
        with self.assertRaises(ValueError):
            fast_abstract.build_onepass_abstract_command(**(kwargs | {"visual_filters": []}))
        with self.assertRaises(ValueError):
            fast_abstract.build_onepass_abstract_command(**(kwargs | {"total": 6.0}))

    def test_command_does_not_encode_intermediate_video(self):
        cmd = fast_abstract.build_onepass_abstract_command(
            [1.7, 2.3, 1.5], width=360, height=640, fps=12,
            transition="wipeleft", visual_filters=["format=yuv420p"],
            audio=None, output=Path("output.mp4"), total=5.5)
        self.assertEqual(cmd.count("-c:v"), 1)
        self.assertEqual(cmd.count("-filter_complex"), 1)
        self.assertIn("xfade=", " ".join(cmd))
        self.assertNotIn("scene_00.mp4", " ".join(cmd))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg needed")
    def test_real_cuts_fades_and_wipes_preserve_duration_and_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            for transition in ("cut", "fade", "wipeleft"):
                with self.subTest(transition=transition):
                    video = Path(tmp) / f"{transition}.mp4"
                    data = free_studio.render(
                        "Paysage", "La forêt est belle. La mer est calme. Le soleil brille.",
                        video, resolution=360, fps=12, mute=True, transition=transition)
                    self.assertEqual(data["render_performance"]["compositing_pipeline"],
                                     "direct-abstract-onepass")
                    report = video_qa.validate(video, data, video.with_suffix(".srt"),
                                               video_qa.probe(video))
                    self.assertTrue(report["ok"], report["errors"])
                    self.assertGreater(video.stat().st_size, 3000)
                    self.assertEqual(video.with_suffix(".srt").read_text().count(" --> "), 3)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("espeak"), "FFmpeg & eSpeak required")
    def test_real_spoken_onepass_kinetic_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "spoken.mp4"
            data = free_studio.render(
                "QA", "Découvrez les paysages français. La lumière traverse les montagnes.",
                video, resolution=360, fps=12, transition="fade", caption_style="kinetic")
            self.assertEqual(data["render_performance"]["compositing_pipeline"],
                             "direct-abstract-onepass")
            self.assertTrue(video.with_suffix(".ass").is_file())
            report = video_qa.validate(video, data, video.with_suffix(".srt"),
                                       video_qa.probe(video))
            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["checks"]["audio_codec"], "aac")


if __name__ == "__main__":
    unittest.main()
