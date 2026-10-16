"""Delivery QA regressions using real FFmpeg assets and deliberately bad metadata."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import video_qa


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class VideoQualityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.video = self.root / 'clip.mp4'
        video_qa.execute([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=s=160x288:r=12:d=3',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=3',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-shortest', str(self.video),
        ])
        self.metadata = {
            'duration_seconds': 3.0, 'size': [160, 288], 'speech': True,
            'speech_engine': 'espeak', 'media': [],
            'segments': [{'text':'Une scène.', 'start':0.0, 'end':1.5},
                         {'text':'La suite.', 'start':1.5, 'end':3.0}],
        }
        self.subtitle = self.video.with_suffix('.srt')
        self.subtitle.write_text('1\n00:00:00,000 --> 00:00:01,500\nUne scène.\n\n2\n00:00:01,500 --> 00:00:03,000\nLa suite.\n', encoding='utf-8')
        self.video.with_suffix('.json').write_text(json.dumps(self.metadata), encoding='utf-8')

    def test_creates_real_two_scene_contact_sheet(self):
        result = video_qa.audit(self.video)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['checks']['video_codec'], 'h264')
        self.assertEqual(result['checks']['audio_codec'], 'aac')
        self.assertEqual(result['storyboard']['scenes'], 2)
        image = self.video.with_suffix('.storyboard.png')
        self.assertGreater(image.stat().st_size, 2000)
        self.assertTrue(json.loads(self.video.with_suffix('.quality.json').read_text())['ok'])
        result_image = video_qa.probe(image)
        stream = result_image['streams'][0]
        self.assertEqual(stream['width'], 3*180 + 4*8)
        self.assertEqual(stream['height'], 320 + 2*8)

    def test_detects_duration_and_resolution_mismatch(self):
        self.metadata['duration_seconds'] = 1.0
        self.metadata['size'] = [100, 100]
        errors = video_qa.validate(self.video, self.metadata, self.subtitle, video_qa.probe(self.video))['errors']
        self.assertTrue(any('drift' in e for e in errors))
        self.assertTrue(any('resolution mismatch' in e for e in errors))

    def test_rejects_invalid_subtitle_alignment(self):
        self.subtitle.write_text('1\n00:00:00,500 --> 00:00:02,500\nWrong.\n', encoding='utf-8')
        errors = video_qa.validate(self.video, self.metadata, self.subtitle, video_qa.probe(self.video))['errors']
        self.assertTrue(any('Subtitle cue count' in e for e in errors))

    def test_rejects_wrong_scene_timeline(self):
        self.metadata['segments'][1]['start'] = 1.8
        errors = video_qa.validate(self.video, self.metadata, self.subtitle, video_qa.probe(self.video))['errors']
        self.assertTrue(any('discontinuous' in e for e in errors))

    def test_rejects_nonvideo_and_missing_metadata(self):
        self.assertRaises(ValueError, video_qa.read_metadata, self.root / 'missing.json')
        self.assertRaises(ValueError, video_qa.audit, self.root / 'not-there.mp4')

    def test_detects_unexpected_audio_for_muted_video(self):
        self.metadata['speech'] = False
        errors = video_qa.validate(self.video, self.metadata, self.subtitle, video_qa.probe(self.video))['errors']
        self.assertTrue(any('unexpectedly contains' in e for e in errors))

    def test_cli_writes_failed_report(self):
        self.metadata['duration_seconds'] = 5.0
        self.video.with_suffix('.json').write_text(json.dumps(self.metadata), encoding='utf-8')
        result = video_qa.main([str(self.video)])
        self.assertEqual(result, 1)
        self.assertFalse(json.loads(self.video.with_suffix('.quality.json').read_text())['ok'])


if __name__ == '__main__':
    unittest.main()