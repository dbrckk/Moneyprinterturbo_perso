"""Android restart recovery must remain visible even with read-only storage."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio_server import Jobs


class RestartReadOnlyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "renders"
        self.ident = "a" * 32
        self.jobs = Jobs(self.root)
        self.folder = self.root / self.ident
        self.running = {"id": self.ident, "created": 100, "finished": None,
                        "status": "running", "error": None, "metadata": None,
                        "parameters": {"topic": "Projet original", "resolution": 360,
                                       "media_batch": ""}}
        self.jobs._checkpoint(self.running)

    def check_no_writes_and_restore(self):
        with patch.object(Jobs, "_checkpoint", side_effect=OSError("read-only filesystem")) as checkpoint:
            restored = Jobs(self.root)
        self.assertTrue(checkpoint.called)
        return restored

    def test_incomplete_export_remains_visible_with_writes_blocked(self):
        protected = self.folder / "video.mp4"
        protected.write_bytes(b"original incomplete")
        scratch = self.folder / ".render-scratch"
        scratch.mkdir()
        (scratch / "orphan.tmp").write_bytes(b"scratched")
        recovered = self.check_no_writes_and_restore()
        self.assertEqual(recovered.get(self.ident)["status"], "error")
        self.assertIn("interrompu", recovered.get(self.ident)["error"])
        self.assertIsNone(recovered.artifact(self.ident, "video.mp4"))
        self.assertEqual(protected.read_bytes(), b"original incomplete")
        self.assertFalse(scratch.exists())
        # The stale durable checkpoint stays untouched until writes return.
        self.assertEqual(json.loads((self.folder / ".job_state.json").read_text())["status"], "running")
        # A later restart can persist the error without re-running FFmpeg.
        self.assertEqual(Jobs(self.root).get(self.ident)["status"], "error")
        self.assertEqual(json.loads((self.folder / ".job_state.json").read_text())["status"], "error")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Actual MP4 and FFprobe required")
    def test_completed_mp4_survives_failed_recovery_checkpoint(self):
        (self.folder / "video.json").write_text(json.dumps({
            "segments": [{"start": 0, "end": 1.2}], "duration_seconds": 1.2,
            "visual_mode": "abstract-fallback"}))
        (self.folder / "video.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nBonjour\n")
        (self.folder / "video.credits.txt").write_text("Production originale\n")
        mp4 = self.folder / "video.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=blue:s=180x320:r=12:d=1.2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(mp4)],
                       timeout=30, check=True)
        original_bytes = mp4.read_bytes()
        recovered = self.check_no_writes_and_restore()
        self.assertEqual(recovered.get(self.ident)["status"], "done")
        self.assertEqual(recovered.get(self.ident)["duration_seconds"], 1.2)
        self.assertEqual(recovered.artifact(self.ident, "video.mp4"), mp4)
        self.assertEqual(mp4.read_bytes(), original_bytes)
        self.assertEqual(json.loads((self.folder / ".job_state.json").read_text())["status"], "running")
        again = Jobs(self.root)
        self.assertEqual(again.get(self.ident)["status"], "done")
        self.assertEqual(json.loads((self.folder / ".job_state.json").read_text())["status"], "done")

    def test_corrupt_final_manifest_is_shown_as_error_not_lost(self):
        self.running["status"] = "done"
        self.jobs._checkpoint(self.running)
        (self.folder / "video.mp4").write_bytes(b"X" * 2100)
        (self.folder / "video.json").write_text("{broken json")
        recovered = self.check_no_writes_and_restore()
        self.assertEqual(recovered.get(self.ident)["status"], "error")
        self.assertIn("Métadonnées", recovered.get(self.ident)["error"])
        self.assertIsNone(recovered.artifact(self.ident, "video.mp4"))
        self.assertEqual(Jobs(self.root).get(self.ident)["status"], "error")


if __name__ == "__main__":
    unittest.main()
