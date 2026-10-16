"""End-to-end stable loudness and single-AAC-encode contract."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest

import free_studio
import video_qa


@unittest.skipUnless(all(shutil.which(p) for p in ("ffmpeg", "ffprobe", "espeak")),
                     "FFmpeg and eSpeak required")
class MobileAudioMasteringTests(unittest.TestCase):
    def test_real_french_video_has_measured_mobile_loudness_and_aac(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/"mobile-narration.mp4"
            meta=free_studio.render(
                "Paysages français",
                "Le soleil se lève sur les montagnes. La rivière traverse la forêt.",
                out, lang="fr", resolution=360, fps=12, tts="espeak",
                visual_style="cinematic", caption_style="classic",
                transition="fade")
            self.assertTrue(out.exists())
            self.assertEqual(meta["audio_mastering"]["target_integrated_lufs"], -16)
            self.assertEqual(meta["audio_mastering"]["audio_encoding"],
                             "aac-160k-48kHz-stream-copy")
            report=video_qa.audit(out)
            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["checks"]["audio_codec"], "aac")
            self.assertIn("audio_loudness", report)
            measurement=report["audio_loudness"]
            self.assertIsNotNone(measurement["integrated_lufs"])
            self.assertGreater(measurement["integrated_lufs"], -21)
            self.assertLess(measurement["integrated_lufs"], -11)
            self.assertIsNotNone(measurement["true_peak_dbfs"])
            self.assertLess(measurement["true_peak_dbfs"], -0.1)
            saved=json.loads(out.with_suffix(".quality.json").read_text())
            self.assertEqual(saved["audio_loudness"], measurement)
            self.assertAlmostEqual(free_studio.duration_seconds(out),
                                   meta["duration_seconds"], delta=0.30)

    def test_silent_video_does_not_claim_mastering(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/"silent.mp4"
            meta=free_studio.render("Landscape", "A beautiful forest.",
                                    out, mute=True, resolution=360, fps=12)
            self.assertIsNone(meta["audio_mastering"])
            report=video_qa.audit(out)
            self.assertNotIn("audio_loudness", report)
            self.assertTrue(report["ok"], report["errors"])


if __name__ == "__main__":
    unittest.main()


class SilenceGuardTests(unittest.TestCase):
    def test_16_bit_silent_wav_does_not_feed_loudnorm(self):
        import wave
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"silence.wav"
            with wave.open(str(source),"wb") as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000)
                out.writeframes(bytes(2)*16000)
            self.assertTrue(free_studio._silent_pcm_wav(source))
            with wave.open(str(source),"wb") as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000)
                out.writeframes(bytes(2)*100 + b"\\x11\\x00" + bytes(2)*100)
            self.assertFalse(free_studio._silent_pcm_wav(source))

    def test_unsigned_8_bit_wav_silence(self):
        import wave
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"unsigned.wav"
            with wave.open(str(source),"wb") as out:
                out.setnchannels(1);out.setsampwidth(1);out.setframerate(8000)
                out.writeframes(bytes([128])*8000)
            self.assertTrue(free_studio._silent_pcm_wav(source))
