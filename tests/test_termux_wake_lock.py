"""Explicit Termux anti-sleep support during local Android renders."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from render_wake_lock import RenderWakeLock
from studio_server import Jobs, validate_draft


VALID = {"topic": "Test sommeil Android", "script": "Une scène unique pour la vidéo.",
         "lang": "fr", "aspect": "9:16", "resolution": 360,
         "visuals": "abstract", "tts": "espeak", "speech_rate": 145,
         "mute": True}


class WakeLockCommandTests(unittest.TestCase):
    def test_default_disabled_does_not_access_commands(self):
        with patch("render_wake_lock.shutil.which",
                   side_effect=AssertionError("must not check Termux")):
            lock = RenderWakeLock(False)
            self.assertFalse(lock.acquire())
            lock.release()

    def test_acquire_release_bounded_and_without_shell(self):
        with patch("render_wake_lock.shutil.which",
                   side_effect=lambda command: f"/termux/bin/{command}"), \
             patch("render_wake_lock.subprocess.run") as run:
            lock = RenderWakeLock(True)
            self.assertTrue(lock.acquire())
            self.assertTrue(lock.acquire(), "No second acquisition")
            lock.release()
            lock.release()
            self.assertFalse(lock.active)
            self.assertEqual(run.call_count, 2)
            acquire, release = (c.args[0] for c in run.call_args_list)
            self.assertEqual(acquire, ["/termux/bin/termux-wake-lock"])
            self.assertEqual(release, ["/termux/bin/termux-wake-unlock"])
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["timeout"], 4)
                self.assertTrue(call.kwargs["check"])
                self.assertNotIn("shell", call.kwargs)

    def test_missing_unlock_does_not_acquire_or_release(self):
        with patch("render_wake_lock.shutil.which",
                   side_effect=lambda command: "/termux/bin/lock" if command == "termux-wake-lock" else None), \
             patch("render_wake_lock.subprocess.run") as run:
            lock = RenderWakeLock(True)
            self.assertFalse(lock.acquire())
            lock.release()
            run.assert_not_called()

    def test_failed_or_hanging_acquire_never_releases(self):
        for exception in (OSError("command missing"),
                          subprocess.TimeoutExpired(["lock"], 4),
                          subprocess.CalledProcessError(1, ["lock"])):
            with self.subTest(exception=type(exception).__name__), \
                 patch("render_wake_lock.shutil.which", side_effect=lambda c: f"/bin/{c}"), \
                 patch("render_wake_lock.subprocess.run", side_effect=exception) as run:
                lock = RenderWakeLock(True)
                self.assertFalse(lock.acquire())
                lock.release()
                self.assertEqual(run.call_count, 1)

    def test_failed_unlock_is_best_effort_and_remains_idempotent(self):
        with patch("render_wake_lock.shutil.which", side_effect=lambda c: f"/bin/{c}"), \
             patch("render_wake_lock.subprocess.run",
                   side_effect=[MagicMock(), subprocess.TimeoutExpired(["unlock"], 4)]) as run:
            lock = RenderWakeLock(True)
            self.assertTrue(lock.acquire())
            lock.release()
            lock.release()
            self.assertEqual(run.call_count, 2)
            self.assertFalse(lock.active)


class RenderWakeIntegrationTests(unittest.TestCase):
    def test_wake_option_is_strict_bool_and_defaults_off(self):
        self.assertFalse(validate_draft(VALID)["keep_awake"])
        self.assertTrue(validate_draft({**VALID, "keep_awake": True})["keep_awake"])
        for invalid in ("yes", 1, None, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_draft({**VALID, "keep_awake": invalid})

    def test_success_and_failure_both_release_exactly_one_wake_lock(self):
        for success in (True, False):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as folder:
                jobs = Jobs(Path(folder))
                work = (
                    "import json,os,time;from pathlib import Path;"
                    "root=Path(os.environ['TMPDIR']).parent;"
                    "time.sleep(.30);"
                    "(root/'video.mp4').write_bytes(b'0'*2100);"
                    "(root/'video.json').write_text(json.dumps({'segments': [],'duration_seconds': 1}));"
                    + ("pass" if success else "raise RuntimeError('render failed')")
                )
                instances = []
                def fake_lock(requested):
                    lock = MagicMock()
                    lock.acquire.return_value = True
                    instances.append((requested, lock))
                    return lock
                with patch("studio_server.RenderWakeLock", side_effect=fake_lock), \
                     patch.object(jobs, "_arguments",
                                  return_value=[sys.executable, "-c", work]):
                    job = jobs.submit({**VALID, "keep_awake": True})
                    ident = job["id"]
                    deadline = time.monotonic() + 5
                    saw_live_wake = False
                    while time.monotonic() < deadline:
                        snapshot = jobs.get(ident)
                        if snapshot["status"] == "running" and snapshot["keep_awake_active"]:
                            saw_live_wake = True
                        if snapshot["status"] not in ("queued", "running"):
                            break
                        time.sleep(.01)
                    jobs.worker_thread.join(timeout=6)
                self.assertFalse(jobs.worker_thread.is_alive())
                self.assertTrue(saw_live_wake)
                self.assertEqual(jobs.get(ident)["status"], "done" if success else "error")
                self.assertFalse(jobs.get(ident)["keep_awake_active"])
                self.assertEqual(len(instances), 1)
                requested, lock = instances[0]
                self.assertTrue(requested)
                lock.acquire.assert_called_once_with()
                lock.release.assert_called_once_with()

    def test_disabled_job_uses_no_termux_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            jobs = Jobs(Path(folder))
            with patch("render_wake_lock.shutil.which",
                       side_effect=AssertionError("disabled render called Termux")), \
                 patch.object(jobs, "_arguments",
                              return_value=[sys.executable, "-c", "raise SystemExit(1)"]):
                j = jobs.submit(VALID)
                jobs.worker_thread.join(timeout=6)
            self.assertEqual(jobs.get(j["id"])["status"], "error")


if __name__ == "__main__":
    unittest.main()
