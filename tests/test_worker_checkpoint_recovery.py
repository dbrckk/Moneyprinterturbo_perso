"""A failed pre-render checkpoint must not strand the queue."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from studio_server import Jobs, validate_draft


OPTIONS = {"topic": "Test", "script": "Une phrase de test.", "lang": "fr",
           "resolution": 360, "visuals": "abstract", "aspect": "9:16",
           "mute": True, "tts": "espeak", "speech_rate": 145}


class CheckpointRecoveryTests(unittest.TestCase):
    def test_failed_running_checkpoint_does_not_stop_other_queued_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs(Path(root))
            params = validate_draft(OPTIONS)
            def new_record(ident):
                return {"id": ident, "created": int(time.time()),
                        "finished": None, "status": "queued", "error": None,
                        "metadata": None, "parameters": dict(params)}
            one, two = "a" * 32, "b" * 32
            jobs.jobs[one] = new_record(one)
            jobs.jobs[two] = new_record(two)
            original = jobs._checkpoint
            attempts = []
            def checkpoint(record):
                attempts.append((record["id"], record["status"]))
                if record["id"] == one and record["status"] == "running":
                    raise OSError("no space left")
                return original(record)
            # Second project fails normally because child returns an error;
            # critical assertion is that it was actually started and reaped.
            with patch.object(jobs, "_checkpoint", side_effect=checkpoint), \
                 patch.object(jobs, "_arguments", return_value=[
                     "python3", "-c", "import sys;sys.exit(2)"]):
                jobs._worker()
            self.assertEqual(jobs.get(one)["status"], "error")
            self.assertIn("Stockage", jobs.get(one)["error"])
            self.assertEqual(jobs.get(two)["status"], "error")
            self.assertIn((two, "running"), attempts)
            self.assertFalse(jobs.running)
            self.assertFalse(jobs.processes)
            self.assertIsNotNone(jobs.get(one)["finished"])


if __name__ == "__main__":
    unittest.main()
