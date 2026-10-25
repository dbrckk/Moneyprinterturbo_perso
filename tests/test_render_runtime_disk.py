"""Live Android disk-pressure protection must stop FFmpeg, not user media."""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from studio_server import (Jobs, RENDER_CRITICAL_FREE_BYTES,
                           RENDER_DISK_CHECK_SECONDS, validate_draft)


VALID = {"topic": "Stockage Android", "script": "La vidéo en cours est protégée.",
         "lang": "fr", "aspect": "9:16", "resolution": 360,
         "visuals": "abstract", "tts": "espeak", "speech_rate": 145, "mute": True}
Usage = namedtuple("Usage", "total used free")


def available(free):
    return Usage(8 * 1024**3, 8 * 1024**3 - free, free)


class LiveDiskTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.jobs = Jobs(self.root / "renders")

    def test_emergency_boundary_and_unmeasurable_storage(self):
        self.assertEqual(RENDER_CRITICAL_FREE_BYTES, 96 * 1024**2)
        self.assertGreater(RENDER_DISK_CHECK_SECONDS, 0)
        with patch("studio_server.shutil.disk_usage", return_value=available(RENDER_CRITICAL_FREE_BYTES)) as usage:
            self.jobs._ensure_running_render_disk_space()
            usage.assert_called_once_with(self.jobs.output_dir)
        with patch("studio_server.shutil.disk_usage", return_value=available(RENDER_CRITICAL_FREE_BYTES - 1)):
            with self.assertRaisesRegex(RuntimeError, "Stockage critique"):
                self.jobs._ensure_running_render_disk_space()
        with patch("studio_server.shutil.disk_usage", side_effect=OSError("device detached")):
            with self.assertRaisesRegex(RuntimeError, "impossible à surveiller"):
                self.jobs._ensure_running_render_disk_space()

    def run_waiting_job(self, second):
        ident = "f" * 32
        record = {"id": ident, "status": "queued", "created": 123,
                  "finished": None, "error": None,
                  "parameters": validate_draft(VALID), "metadata": None}
        self.jobs.jobs[ident] = record
        self.jobs._checkpoint(record)
        protected = self.root / "my_original.mov"
        protected.write_bytes(b"personal-original")
        saved = self.jobs.output_dir / ident / "video.mp4"
        saved.write_bytes(b"previous-work-not-deleted")
        # First reading is the queue preflight; the next happens during
        # an actual running subprocess rather than before its launch.
        states = iter((available(1024**3), second))
        created = []
        real_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            created.append(proc)
            return proc

        def disk_probe(_path):
            sample = next(states)
            if isinstance(sample, BaseException):
                raise sample
            return sample

        with patch("studio_server.RENDER_DISK_CHECK_SECONDS", 0.05), \
             patch("studio_server.shutil.disk_usage", side_effect=disk_probe), \
             patch.object(self.jobs, "_arguments", return_value=[
                 sys.executable, "-c", "import time; time.sleep(120)"]), \
             patch("studio_server.subprocess.Popen", side_effect=spawn):
            self.jobs._worker()
        self.assertEqual(len(created), 1, "Real renderer must have started")
        self.assertIsNotNone(created[0].poll(), "Disk pressure must reap renderer")
        self.assertEqual(protected.read_bytes(), b"personal-original")
        self.assertEqual(saved.read_bytes(), b"previous-work-not-deleted")
        self.assertFalse((saved.parent / ".render-scratch").exists())
        self.assertEqual(self.jobs.get(ident)["status"], "error")
        self.assertEqual(Jobs(self.jobs.output_dir).get(ident)["status"], "error")
        return self.jobs.get(ident)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_live_low_space_stops_actual_process_without_touching_media(self):
        result = self.run_waiting_job(available(8 * 1024**2))
        self.assertIn("Stockage critique", result["error"])
        self.assertIn("conservées", result["error"])

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_runtime_storage_probe_failure_stops_actual_process(self):
        result = self.run_waiting_job(OSError("device disconnected"))
        self.assertIn("impossible à surveiller", result["error"])


if __name__ == "__main__":
    unittest.main()
