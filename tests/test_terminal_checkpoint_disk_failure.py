"""A full Android filesystem must not strand encoders or the next queued job."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio_server import Jobs, validate_draft


VALID = {"topic": "Rendu après saturation", "script": "Ce film est préservé.",
         "lang": "fr", "aspect": "9:16", "resolution": 360,
         "visuals": "abstract", "tts": "espeak", "speech_rate": 145, "mute": True}

FAKE_SUCCESS = (
    "import json,os;from pathlib import Path;"
    "p=Path(os.environ['TMPDIR']).parent;"
    "(p/'video.mp4').write_bytes(b'X'*2400);"
    "(p/'video.json').write_text(json.dumps({'segments':[],"
    "'duration_seconds':1.0}),encoding='utf-8')"
)


class TerminalCheckpointFailureTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.jobs = Jobs(self.root / "out")
        self.parameters = validate_draft(VALID)

    def add_job(self, ident):
        record = {"id": ident, "status": "queued", "created": int(time.time()),
                  "finished": None, "error": None, "parameters": dict(self.parameters),
                  "metadata": None}
        self.jobs.jobs[ident] = record
        self.jobs._checkpoint(record)
        return record

    def test_done_checkpoint_failure_keeps_mp4_and_renders_next_job(self):
        one, two = "a" * 32, "b" * 32
        self.add_job(one)
        self.add_job(two)
        original = self.jobs._checkpoint
        failures = []

        def checkpoint(record):
            if record["id"] == one and record["status"] == "done":
                failures.append(record["status"])
                raise OSError("ENOSPC on final state")
            return original(record)

        with patch.object(self.jobs, "_checkpoint", side_effect=checkpoint), \
             patch.object(self.jobs, "_arguments", return_value=[
                 sys.executable, "-c", FAKE_SUCCESS]):
            self.jobs._worker()
        self.assertEqual(len(failures), 2)
        self.assertEqual(self.jobs.get(one)["status"], "done")
        self.assertEqual(self.jobs.get(two)["status"], "done")
        self.assertIsNotNone(self.jobs.artifact(one, "video.mp4"))
        self.assertEqual(self.jobs.artifact(one, "video.mp4").stat().st_size, 2400)
        self.assertFalse(self.jobs.running)
        self.assertFalse(self.jobs.processes)
        self.assertEqual(Jobs(self.jobs.output_dir).get(two)["status"], "done")

    def test_error_checkpoint_failure_does_not_strand_next_queued_job(self):
        one, two = "c" * 32, "d" * 32
        self.add_job(one)
        self.add_job(two)
        original = self.jobs._checkpoint
        failures = []

        def checkpoint(record):
            if record["id"] == one and record["status"] == "error":
                failures.append(record["status"])
                raise OSError("read-only storage on failure")
            return original(record)

        with patch.object(self.jobs, "_checkpoint", side_effect=checkpoint), \
             patch.object(self.jobs, "_arguments", side_effect=lambda j, d: [
                 sys.executable, "-c", "import sys;sys.exit(9)"
                 if j["id"] == one else FAKE_SUCCESS]):
            self.jobs._worker()
        self.assertEqual(len(failures), 2)
        self.assertEqual(self.jobs.get(one)["status"], "error")
        self.assertEqual(self.jobs.get(two)["status"], "done")
        self.assertIsNotNone(self.jobs.get(one)["finished"])
        self.assertFalse(self.jobs.running)
        self.assertFalse(self.jobs.processes)
        self.assertFalse((self.jobs.output_dir / one / ".render-scratch").exists())

    def test_unremovable_progress_file_does_not_strand_render_queue(self):
        from render_progress import PROGRESS_NAME
        one, two = "1" * 32, "2" * 32
        self.add_job(one)
        self.add_job(two)
        original_unlink = Path.unlink
        denied_path = self.jobs.output_dir / one / PROGRESS_NAME

        def guarded_unlink(path, missing_ok=False):
            if path == denied_path:
                raise OSError("readonly stage file")
            return original_unlink(path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", guarded_unlink), \
             patch.object(self.jobs, "_arguments", return_value=[
                 sys.executable, "-c", FAKE_SUCCESS]):
            self.jobs._worker()
        self.assertEqual(self.jobs.get(one)["status"], "error")
        self.assertIn("readonly stage file", self.jobs.get(one)["error"])
        self.assertEqual(self.jobs.get(two)["status"], "done")
        self.assertFalse(self.jobs.running)
        self.assertFalse(self.jobs.processes)

    @unittest.skipUnless(os.name == "posix", "Real isolated process groups required")
    def test_cancel_still_kills_renderer_when_checkpoint_cannot_be_written(self):
        ident = "e" * 32
        self.add_job(ident)
        original = self.jobs._checkpoint

        def checkpoint(record):
            if record["id"] == ident and record["status"] == "cancelled":
                raise OSError("read-only filesystem")
            return original(record)

        failures = []

        def run_worker():
            try:
                self.jobs._worker()
            except BaseException as exc:
                failures.append(exc)

        with patch.object(self.jobs, "_checkpoint", side_effect=checkpoint), \
             patch.object(self.jobs, "_arguments", return_value=[
                 sys.executable, "-c", "import time;time.sleep(120)"]):
            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
            deadline = time.monotonic() + 5
            proc = None
            while time.monotonic() < deadline and proc is None:
                with self.jobs.lock:
                    proc = self.jobs.processes.get(ident)
                if proc is None:
                    time.sleep(.02)
            try:
                self.assertIsNotNone(proc, "renderer never started")
                snapshot = self.jobs.cancel(ident)
                self.assertEqual(snapshot["status"], "cancelled")
                worker.join(timeout=8)
                self.assertFalse(worker.is_alive(), "cancellation stranded worker")
                self.assertFalse(failures, failures)
                self.assertIsNotNone(proc.poll(), "encoder was left alive")
                self.assertFalse(self.jobs.processes)
                self.assertEqual(self.jobs.get(ident)["status"], "cancelled")
            finally:
                if worker.is_alive():
                    self.jobs.shutdown()
                    worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
