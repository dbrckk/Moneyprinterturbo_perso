import json
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch
import forced_alignment
import caption_motion
import free_studio

class AlignmentTests(unittest.TestCase):
    def test_acoustic_words_are_used(self):
        sentences=[{"text":"Bonjour le monde.", "start":0.0, "end":2.0}]
        result={"segments":[{"words":[{"word":" Bonjour","start":0.1,"end":0.5},{"word":" le","start":0.7,"end":0.9},{"word":" monde.","start":1.1,"end":1.7}]}]}
        words,warnings=forced_alignment.verify_word_scenes(sentences,result)
        self.assertEqual(warnings,[])
        with tempfile.TemporaryDirectory() as folder:
            info=caption_motion.generate_karaoke_ass(sentences,Path(folder),width=360,height=640,word_times=words)
            self.assertEqual(info["timing_method"],"stable-ts")
            text=(Path(folder)/"captions.ass").read_text()
            self.assertIn("0:00:00.10",text)
            self.assertIn(r"{\kf60}",text)

    def test_mismatch_falls_back_with_clear_warning(self):
        segments=[{"text":"Bonjour le monde.", "start":0.0,"end":2.0}]
        words,warnings=forced_alignment.verify_word_scenes(segments,{"segments":[{"words":[{"word":"wrong","start":0,"end":1}]}]})
        self.assertEqual(words,[None]);self.assertTrue(warnings)

    def test_missing_package_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            audio=Path(folder)/"voice.wav";audio.write_bytes(b"audio")
            with patch.dict("sys.modules", {"stable_whisper": None}):
                with self.assertRaisesRegex(RuntimeError,"stable-ts"):
                    forced_alignment.align_narration(audio,[{"text":"Bonjour.","start":0,"end":1}],language="fr")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg required")
    def test_real_render_with_mocked_acoustic_word_times(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/"forced.mp4"
            timestamps=[[{"start":0.1,"end":0.45},{"start":0.65,"end":0.85},{"start":1.0,"end":1.45}]]
            with patch.object(free_studio,"align_narration",return_value=(timestamps,[])):
                info=free_studio.render("Test","Bonjour le monde.",dest, resolution=360,fps=12,
                                          mute=False, word_alignment="forced")
            self.assertEqual(info["caption_timing"],"forced-audio-word-onsets")
            self.assertTrue(dest.with_suffix(".alignment.json").exists())
            self.assertEqual(json.loads(dest.with_suffix(".alignment.json").read_text())["aligned_scenes"],1)
            self.assertGreater(dest.stat().st_size,1000)

    def test_incompatible_modes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            for mute,style in ((True,"kinetic"),(False,"classic")):
                with self.assertRaisesRegex(ValueError,"Forced word alignment"):
                    free_studio.render("Test","Bonjour.",Path(folder)/"test.mp4",
                                       mute=mute,caption_style=style,word_alignment="forced",
                                       resolution=360,fps=12)
