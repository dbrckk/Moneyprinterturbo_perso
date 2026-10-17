"""Detect missing narration and almost silent scenes in real H.264/AAC files."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import wave

import video_qa


@unittest.skipUnless(all(shutil.which(exe) for exe in ("ffmpeg", "ffprobe")),
                     "FFmpeg required")
class AudibleNarrationTests(unittest.TestCase):
    def make_video(self, folder: Path, amplitudes: tuple[float, float]) -> Path:
        """Build a two-scene MP4: each scene has 1.5 s PCM tone or silence."""
        pcm = folder / "narration.wav"
        sample_rate = 24000
        with wave.open(str(pcm), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            for amp in amplitudes:
                samples = bytearray()
                for index in range(int(sample_rate * 1.5)):
                    value = round(amp * math.sin(index * 2 * math.pi * 440 / sample_rate) * 32767)
                    samples.extend(struct.pack("<h", value))
                wav.writeframes(samples)
        target = folder / "clip.mp4"
        video_qa.execute([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=160x288:r=12:d=3",
            "-i", str(pcm), "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-t", "3", str(target)])
        details = {
            "duration_seconds": 3.0,
            "size": [160, 288],
            "speech": True,
            "speech_engine": "espeak",
            "media": [],
            "segments": [
                {"text": "La première scène.", "start": 0.0, "end": 1.5},
                {"text": "La deuxième scène.", "start": 1.5, "end": 3.0},
            ]
        }
        target.with_suffix(".json").write_text(json.dumps(details))
        target.with_suffix(".srt").write_text(
            "1\\n00:00:00,000 --> 00:00:01,500\\nLa première scène.\\n\\n"
            "2\\n00:00:01,500 --> 00:00:03,000\\nLa deuxième scène.\\n")
        return target

    def test_silent_aac_cannot_pass_narrated_video_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self.make_video(Path(tmp), (0.0, 0.0))
            report = video_qa.audit(target)
            self.assertFalse(report["ok"])
            self.assertEqual(report["checks"]["audio_codec"], "aac")
            self.assertTrue(any("silent AAC" in e for e in report["errors"]), report)
            self.assertGreaterEqual(len(report["audio_loudness"]["quiet_intervals_seconds"]), 1)
            self.assertLessEqual(report["audio_loudness"]["integrated_lufs"], -55)

    def test_only_second_scene_silent_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self.make_video(Path(tmp), (.24, 0.0))
            report = video_qa.audit(target)
            self.assertFalse(report["ok"])
            self.assertTrue(any("Scene 2: narration" in e for e in report["errors"]), report)
            self.assertFalse(any("Scene 1: narration" in e for e in report["errors"]), report)

    def test_both_scenes_audible_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self.make_video(Path(tmp), (.24, .18))
            report = video_qa.audit(target)
            self.assertTrue(report["ok"], report["errors"])
            self.assertFalse(any("silent" in e for e in report["errors"]))
            self.assertIsNotNone(report["audio_loudness"]["integrated_lufs"])


class SilenceFractionTests(unittest.TestCase):
    def test_silence_overlap_across_multiple_intervals(self):
        self.assertAlmostEqual(
            video_qa.silence_share(1.0, 3.0, [[0,1.4], [2.0, 3.5]]), .7)
        self.assertAlmostEqual(
            video_qa.silence_share(0.0, 2.0, [[0,2]]), 1)
        self.assertEqual(video_qa.silence_share(3,3,[[0,4]]),0.0)


if __name__ == "__main__":
    unittest.main()
