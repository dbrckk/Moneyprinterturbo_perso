"""Standalone regressions for the fast WAV and single-load neural voice path."""
from pathlib import Path
import tempfile
import types
import unittest
import wave
from unittest.mock import patch

import tts_batch


class WAVTimingTests(unittest.TestCase):
    def test_exact_riff_pcm_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "speech.wav"
            with wave.open(str(wav), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(b"\\x00\\x00" * 24000)
            self.assertEqual(tts_batch.wav_duration_seconds(wav), 1.5)

    def test_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "bad.wav"
            wav.write_bytes(b"not a wave")
            with self.assertRaises((wave.Error, EOFError)):
                tts_batch.wav_duration_seconds(wav)


class PiperBatchTests(unittest.TestCase):
    def test_one_model_load_for_multiple_scenes(self):
        calls = {"loads": 0, "texts": []}
        class FakeVoice:
            @staticmethod
            def load(model):
                calls["loads"] += 1
                return FakeVoice()
            def synthesize_wav(self, text, writer):
                calls["texts"].append(text)
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(b"\\x00\\x00" * 8000)
        fake = types.SimpleNamespace(PiperVoice=FakeVoice)
        with tempfile.TemporaryDirectory() as tmp, patch.object(tts_batch.importlib, "import_module", return_value=fake):
            work = Path(tmp)
            self.assertTrue(tts_batch.synthesize_piper_batch(
                ["Bonjour.", "Le soleil brille.", "La mer est calme."], work / "model.onnx", work))
            self.assertEqual(calls["loads"], 1)
            self.assertEqual(len(calls["texts"]), 3)
            self.assertEqual(len(list(work.glob("voice_*.wav"))), 3)
            self.assertTrue(all(tts_batch.wav_duration_seconds(p)==0.5 for p in work.glob("voice_*.wav")))

    def test_missing_python_package_keeps_cli_fallback(self):
        def missing(name):
            raise ModuleNotFoundError("No module named 'piper'", name="piper")
        with tempfile.TemporaryDirectory() as tmp, patch.object(tts_batch.importlib, "import_module", side_effect=missing):
            self.assertFalse(tts_batch.synthesize_piper_batch(["Bonjour."], Path(tmp)/"fake.onnx", Path(tmp)))

    def test_bad_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                tts_batch.synthesize_piper_batch([""], Path(tmp)/"voice.onnx", Path(tmp))


if __name__ == "__main__":
    unittest.main()
