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
