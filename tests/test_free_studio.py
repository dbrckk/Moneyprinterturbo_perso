import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import free_studio as studio


class FreeStudioTests(unittest.TestCase):
    def test_scripts(self):
        self.assertEqual(studio.split_script('Bonjour. Comment allez-vous ?'), ['Bonjour.', 'Comment allez-vous ?'])
        self.assertRaises(ValueError, studio.split_script, ' ')
        self.assertRaises(ValueError, studio.split_script, 'word. ' * 20)
        self.assertIn('exemples', studio.template_script('énergie solaire', 'fr'))
        self.assertIn('ejemplos', studio.template_script('energía solar', 'es'))
        self.assertIn('Beispiele', studio.template_script('Solarenergie', 'de'))

    def test_ollama_rejects_external_url(self):
        for url in ('https://api.openai.com', 'http://8.8.8.8', 'file:///tmp/secret', 'http://user:pass@localhost:11434'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    studio.ollama_script('test', 'fr', 'tiny', url)

    def test_srt_and_dimensions(self):
        self.assertEqual(studio.srt_timestamp(3661.234), '01:01:01,234')
        self.assertEqual(studio.size_for('9:16', 360), (360, 640))
        self.assertEqual(studio.size_for('16:9', 360), (640, 360))
        self.assertEqual(studio.size_for('1:1', 720), (720, 720))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires FFmpeg')
    def test_video_end_to_end_no_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'smoke.mp4'
            info = studio.render('QA', 'Alpha test. Beta test.', target, resolution=360, fps=12, mute=True)
            self.assertTrue(target.exists())
            self.assertGreater(target.stat().st_size, 1500)
            self.assertEqual(info['size'], [360, 640])
            self.assertEqual(len(info['segments']), 2)
            self.assertEqual(json.loads(target.with_suffix('.json').read_text())['speech'], False)
            self.assertGreater(studio.duration_seconds(target), 5)

    @unittest.skipUnless(shutil.which('espeak') and shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires eSpeak and FFmpeg')
    def test_audio_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'speech.mp4'
            info = studio.render('Bonjour', 'Bonjour tout le monde.', target, resolution=360, fps=12)
            self.assertTrue(info['speech'])
            self.assertGreater(studio.duration_seconds(target), 0.5)
            self.assertGreater(target.stat().st_size, 5000)


if __name__ == '__main__':
    unittest.main()

class PiperAudioTests(unittest.TestCase):
    def test_rejects_missing_voice_and_wrong_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "requires a local model"):
                studio.render("QA", "Hi there.", Path(folder) / "a.mp4", lang="en", tts="piper", resolution=360, fps=12)
            with self.assertRaisesRegex(ValueError, "Invalid voice engine"):
                studio.render("QA", "Hi there.", Path(folder) / "a.mp4", lang="en", tts="espeak", piper_model=Path("a.onnx"), resolution=360, fps=12)
            with self.assertRaisesRegex(ValueError, "Scene search queries require"):
                studio.render("QA", "Hi there.", Path(folder) / "a.mp4", lang="en", scene_queries=["planet"], resolution=360, fps=12)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_piper_stdin_integration_using_test_executable(self):
        """Shim checks transport, args and audio validation; not model quality."""
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            fake_model = work / "voice.onnx"
            fake_model.write_bytes(b"fake")
            fake_model.with_suffix(".onnx.json").write_text("{}")
            fake_bin = work / "piper"
            fake_bin.write_text(
                '#!/usr/bin/env python3\n'
                'import sys, wave, math, struct\n'
                'from pathlib import Path\n'
                'args=sys.argv\n'
                'assert args[1]=="--model" and args[3]=="--output_file"\n'
                'text=sys.stdin.read()\n'
                'assert text.strip()\n'
                'with wave.open(args[4], "wb") as w:\n'
                '    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)\n'
                '    w.writeframes(b"\\0\\0"*16000)\n', encoding="utf-8"
            )
            fake_bin.chmod(0o755)
            target = work / "voice-demo.mp4"
            with patch.dict(os.environ, {"PATH": str(work) + os.pathsep + os.environ.get("PATH", "")}):
                info = studio.render("Speech", "A sentence for Piper.", target, lang="en", resolution=360,
                                     fps=12, tts="piper", piper_model=fake_model)
            self.assertEqual(info["speech_engine"], "piper")
            self.assertGreater(target.stat().st_size, 1000)
            self.assertEqual(target.with_suffix(".srt").read_text().count(" --> "), 1)


class PiperAttributionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
    def test_unknown_piper_model_never_claims_cc_by_license(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            fake_model = work / "fr_FR-siwis-medium.onnx"
            fake_model.write_bytes(b"not the verified model")
            fake_model.with_suffix(".onnx.json").write_text("{}")
            fake_bin = work / "piper"
            fake_bin.write_text('#!/usr/bin/env python3\n'
              'import sys, wave\n'
              'with wave.open(sys.argv[4], "wb") as w:\n'
              ' w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\\0\\0"*16000)\n')
            fake_bin.chmod(0o755)
            video=work / "test.mp4"
            with patch.dict(os.environ, {"PATH": str(work)+os.pathsep+os.environ.get("PATH","")}):
                info=studio.render("Voice", "Bonjour le monde.", video, lang="fr", tts="piper", piper_model=fake_model, resolution=360, fps=12)
            self.assertFalse(info["voice_model"]["license_verified"])
            self.assertIn("not automatically verified", video.with_suffix(".credits.txt").read_text())
            self.assertNotIn("License: https://creativecommons", video.with_suffix(".credits.txt").read_text())
            self.assertEqual(info["speech_engine"], "piper")


class ProvenanceFileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_muted_original_graphics_always_write_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'sample.mp4'
            studio.render('Forêt', 'La forêt est magnifique.', video,
                          mute=True, resolution=360, fps=12)
            credits = video.with_suffix('.credits.txt')
            self.assertTrue(credits.is_file())
            body = credits.read_text(encoding='utf-8')
            self.assertIn('Original animated graphic backgrounds', body)
            self.assertIn('Voice: None', body)

    @unittest.skipUnless(all(shutil.which(n) for n in ('espeak','ffmpeg','ffprobe')), 'Audio tools required')
    def test_espeak_original_graphics_attribution_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / 'spoken.mp4'
            studio.render('Forêt', 'La forêt est magnifique.', video,
                          lang='fr', resolution=360, fps=12)
            body = video.with_suffix('.credits.txt').read_text(encoding='utf-8')
            self.assertIn('eSpeak synthetic speech',body)
            self.assertNotIn('SIWIS French medium',body)
