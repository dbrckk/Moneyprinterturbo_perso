"""Termux output must not expose narration or render logs via umask/symlinks."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from studio_server import Jobs


VALID = {"topic": "Confidentiel", "script": "Texte personnel du narrateur.",
         "lang": "fr", "aspect": "9:16", "resolution": 360,
         "visuals": "abstract", "tts": "espeak", "speech_rate": 145,
         "mute": True}


class PrivateRenderFilesTests(unittest.TestCase):
    def wait_for(self, jobs, ident):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = jobs.get(ident)
            if result["status"] not in ("running", "queued"):
                return result
            time.sleep(.02)
        self.fail("Render did not finish")

    def test_private_files_even_with_public_umask(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs(Path(root))
            original_umask = os.umask(0)
            try:
                with patch.object(jobs, "_arguments", return_value=[sys.executable,
                         "-c", "import sys; sys.exit(37)"]):
                    job = jobs.submit(VALID)
                    self.assertEqual(self.wait_for(jobs, job["id"])["status"], "error")
                    jobs.worker_thread.join(timeout=5)
            finally:
                os.umask(original_umask)
            dest = Path(root) / job["id"]
            for name in ("script.txt", "render.log"):
                file = dest / name
                self.assertTrue(file.is_file(), name)
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual((dest / "script.txt").read_text(), VALID["script"])

    def test_existing_symlink_does_not_overwrite_outside_file(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs(Path(root) / "out")
            victim = Path(root) / "outside.txt"
            victim.write_text("important private data")
            with patch.object(jobs, "_arguments", return_value=[sys.executable,
                     "-c", "import sys; sys.exit(37)"]):
                # Pre-seed a queued record with a symlink so the test is
                # deterministic and does not race a background worker.
                ident = "b" * 32
                queued = {"id": ident, "status": "queued", "created": 1,
                          "finished": None, "error": None,
                          "parameters": VALID.copy(), "metadata": None}
                folder = jobs.output_dir / ident
                folder.mkdir()
                (folder / "render.log").symlink_to(victim)
                with jobs.lock:
                    jobs.jobs[ident] = queued
                jobs._worker()
                self.assertEqual(jobs.get(ident)["status"], "error")
                self.assertEqual(victim.read_text(), "important private data")


if __name__ == "__main__":
    unittest.main()
