"""Real process-tree cancellation guarantees for long Android/Termux renders."""
from __future__ import annotations

import os
import select
import shutil
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import studio_server
from studio_server import Jobs, validate_draft


VALID = {"topic": "Enregistrement local", "script": "La première scène est terminée.",
         "lang": "fr", "resolution": 360, "visuals": "abstract",
         "aspect": "9:16", "mute": True, "tts": "espeak", "speech_rate": 145}


def job_record(ident: str):
    return {"id": ident, "status": "queued", "created": int(time.time()),
            "finished": None, "error": None, "parameters": validate_draft(VALID),
            "metadata": None}


def alive_process(pid: int) -> bool:
    # Reparented children on Linux may temporarily be zombies; they can no
    # longer run FFmpeg, consume CPU or hold video files open.
    if sys.platform == "linux":
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
        except FileNotFoundError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@unittest.skipUnless(os.name == "posix", "POSIX process groups required")
class RealProcessGroupTests(unittest.TestCase):
    @unittest.skipUnless(all(shutil.which(program)
                             for program in ("ffmpeg", "ffprobe", "espeak")),
                         "Server codec dependencies required")
    def test_termux_server_sigterm_exits_cleanly(self):
        """SIGTERM should close the server, not bypass shutdown as an error."""
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.Popen([
                sys.executable, str(Path(studio_server.__file__).resolve()),
                "--port", "0", "--output-dir", folder],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True)
            try:
                readable, _, _ = select.select([proc.stdout], [], [], 8)
                self.assertTrue(readable, "local server did not start")
                first_line = proc.stdout.readline()
                self.assertIn("Free Studio Web", first_line)
                time.sleep(0.10)
                os.kill(proc.pid, signal.SIGTERM)
                self.assertEqual(proc.wait(timeout=5), 0,
                                 "SIGTERM did not terminate the server cleanly")
            finally:
                if proc.poll() is None:
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    proc.wait(timeout=5)
                proc.communicate(timeout=3)

    def test_failed_render_removes_its_private_scratch_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            protected = root / "private-original.mp4"
            protected.write_bytes(b"retain original")
            jobs = Jobs(root / "out")
            script = (
                "import os,sys,tempfile;from pathlib import Path;"
                "scratch=Path(os.environ['TMPDIR']);"
                "work=Path(tempfile.mkdtemp(prefix='mpt-free-'));"
                "assert work.parent==scratch;"
                "(work/'orphan-large.mp4').write_bytes(b'X'*16384);"
                "sys.exit(37)")
            with patch.object(jobs, "_arguments", return_value=[
                    sys.executable, "-c", script]):
                job = jobs.submit(VALID)
                deadline = time.monotonic() + 5
                while jobs.get(job["id"])["status"] in ("queued", "running") and \
                        time.monotonic() < deadline:
                    time.sleep(.02)
                worker = jobs.worker_thread
                if worker is not None:
                    worker.join(timeout=5)
            self.assertEqual(jobs.get(job["id"])["status"], "error")
            self.assertFalse((root / "out" / job["id"] / ".render-scratch").exists())
            self.assertEqual(protected.read_bytes(), b"retain original")

    def test_cancel_race_before_popen_registration_stops_actual_renderer(self):
        """An HTTP cancel during Popen() startup still stops the new PID."""
        with tempfile.TemporaryDirectory() as folder:
            jobs = Jobs(Path(folder))
            ident = uuid4().hex
            jobs.jobs[ident] = job_record(ident)
            spawned = []
            actual_popen = subprocess.Popen
            def spawn_and_cancel(*args, **kwargs):
                proc = actual_popen(*args, **kwargs)
                spawned.append(proc)
                self.assertEqual(jobs.cancel(ident)["status"], "cancelled")
                return proc
            with patch.object(jobs, "_arguments", return_value=[
                     sys.executable, "-c", "import time; time.sleep(120)"]), \
                 patch("studio_server.subprocess.Popen", side_effect=spawn_and_cancel):
                worker = threading.Thread(target=jobs._worker, daemon=True)
                worker.start()
                worker.join(timeout=7)
                if worker.is_alive():
                    for proc in spawned:
                        try: os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                    worker.join(timeout=5)
                self.assertFalse(worker.is_alive(), "cancelled worker never exited")
            self.assertEqual(len(spawned), 1)
            self.assertIsNotNone(spawned[0].poll())
            self.assertEqual(jobs.get(ident)["status"], "cancelled")
            self.assertFalse((Path(folder) / ident / "video.mp4").exists())
            self.assertEqual(jobs.processes, {})

    def test_render_timeout_is_error_and_kills_stubborn_encoder(self):
        """A max-duration failure must not masquerade as a user cancellation."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobs = Jobs(root / "out")
            code = (
                "import signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "time.sleep(120)")
            with patch.object(jobs, "_arguments", return_value=[
                    sys.executable, "-c", code]), \
                 patch("studio_server.JOB_TIMEOUT", 0.2):
                job = jobs.submit(VALID)
                worker = jobs.worker_thread
                assert worker is not None
                worker.join(timeout=7)
                if worker.is_alive():
                    jobs.shutdown()
                self.assertFalse(worker.is_alive())
                result = jobs.get(job["id"])
                self.assertEqual(result["status"], "error")
                self.assertIn("Délai maximal", result["error"])
                self.assertEqual(jobs.processes, {})
                self.assertFalse((root / "out" / job["id"] / ".render-scratch").exists())

    def test_shutdown_reaps_ffmpeg_like_child_that_ignores_sigterm(self):
        """Closing Termux must not leave an orphan encoder running."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ready = root / "child-ready"
            child_pid_file = root / "child-pid"
            protected = root / "keep-this-video.mp4"
            protected.write_bytes(b"user-export-must-survive")
            child_code = (
                "import signal,sys,time;from pathlib import Path;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "Path(sys.argv[1]).write_text('ready');time.sleep(120)")
            parent_code = (
                "import subprocess,sys,time;from pathlib import Path;"
                "p=subprocess.Popen([sys.executable,'-c',sys.argv[3],sys.argv[1]]);"
                "Path(sys.argv[2]).write_text(str(p.pid));time.sleep(120)")
            jobs = Jobs(root / "out")
            with patch.object(jobs, "_arguments", return_value=[
                     sys.executable, "-c", parent_code, str(ready),
                     str(child_pid_file), child_code]):
                job = jobs.submit(VALID)
                deadline = time.monotonic() + 6
                while not (ready.exists() and child_pid_file.exists()) and time.monotonic() < deadline:
                    time.sleep(.03)
                with jobs.lock:
                    proc = jobs.processes.get(job["id"])
                scratch = root / "out" / job["id"] / ".render-scratch"
                child_pid = int(child_pid_file.read_text()) if child_pid_file.exists() else None
                try:
                    self.assertIsNotNone(child_pid, "grandchild never started")
                    self.assertIsNotNone(proc)
                    self.assertTrue(alive_process(child_pid))
                    self.assertTrue(scratch.is_dir())
                    jobs.shutdown()
                    self.assertFalse(scratch.exists(), "cancelled job kept private temporary files")
                    self.assertEqual(protected.read_bytes(), b"user-export-must-survive")
                    self.assertEqual(jobs.get(job["id"])["status"], "cancelled")
                    self.assertIsNotNone(proc.poll())
                    deadline = time.monotonic() + 3
                    while alive_process(child_pid) and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertFalse(alive_process(child_pid),
                                     "orphan FFmpeg-like grandchild survived server exit")
                    self.assertEqual(jobs.processes, {})
                    with self.assertRaisesRegex(ValueError, "Serveur en cours d'arrêt"):
                        jobs.submit(VALID)
                finally:
                    jobs.shutdown()
                    if proc is not None:
                        try: os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError: pass


class FinalizationRaceTests(unittest.TestCase):
    def test_cancel_during_manifest_read_cannot_restore_done_status(self):
        """A late successful encode cannot override a user cancellation."""
        with tempfile.TemporaryDirectory() as folder:
            jobs = Jobs(Path(folder))
            ident = uuid4().hex
            jobs.jobs[ident] = job_record(ident)
            path = Path(folder) / ident
            path.mkdir()
            (path / "video.mp4").write_bytes(b"0" * 2000)
            (path / "video.json").write_text('{"segments": []}', encoding="utf-8")
            class FinishedProcess:
                pid = 999999999
                def wait(self, timeout=None): return 0
                def poll(self): return 0
            def cancel_on_manifest_read(*args, **kwargs):
                self.assertEqual(jobs.cancel(ident)["status"], "cancelled")
                return {"segments": [], "duration_seconds": 2.0}
            with patch.object(jobs, "_arguments", return_value=["pretend-render"]), \
                 patch("studio_server.subprocess.Popen", return_value=FinishedProcess()), \
                 patch("studio_server.json.loads", side_effect=cancel_on_manifest_read):
                jobs._worker()
            result = jobs.get(ident)
            self.assertEqual(result["status"], "cancelled")
            self.assertIsNone(jobs.jobs[ident]["metadata"])
            self.assertIsNone(jobs.artifact(ident, "video.mp4"))
            self.assertIsNotNone(result["finished"])

    def test_queued_cancellation_never_starts_an_encoder(self):
        with tempfile.TemporaryDirectory() as folder:
            jobs = Jobs(Path(folder))
            ident = uuid4().hex
            jobs.jobs[ident] = job_record(ident)
            self.assertEqual(jobs.cancel(ident)["status"], "cancelled")
            with patch("studio_server.subprocess.Popen") as popen:
                jobs._worker()
            popen.assert_not_called()
            self.assertEqual(jobs.get(ident)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
