"""Progress sidecar and stage reporting for Android long montage UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import free_studio
from render_progress import PROGRESS_NAME, ProgressReporter, STAGES, read_progress
from studio_server import Jobs, StudioServer
from video_qa import validate, probe


class ProgressSidecarTests(unittest.TestCase):
    def test_atomic_private_stage_updates_with_real_elapsed_time(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / PROGRESS_NAME
            reporter = ProgressReporter(Path(root))
            reporter.started -= 23
            for index, stage in enumerate(STAGES, 1):
                reporter.emit(stage)
                saved = json.loads(path.read_text())
                self.assertEqual(saved["stage"], stage)
                self.assertEqual(saved["step"], index)
                self.assertEqual(saved["steps"], 6)
                self.assertEqual(set(saved), {"stage", "step", "steps", "started", "updated"})
                self.assertLessEqual(path.stat().st_size, 512)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                progress = read_progress(path)
                self.assertEqual(progress["stage"], stage)
                self.assertGreaterEqual(progress["elapsed_seconds"], 23)
                self.assertNotIn("path", json.dumps(progress))
            with self.assertRaisesRegex(ValueError, "backwards"):
                reporter.emit("voice")
            with self.assertRaisesRegex(ValueError, "Unknown"):
                reporter.emit("unknown")

    def test_corrupt_stale_symlink_and_oversized_stage_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            data = folder / PROGRESS_NAME
            private = folder / "private-script.txt"
            private.write_text("private narration", encoding="utf-8")
            for payload in ("{", "", "X" * 1024,
                            json.dumps({"stage": "voice", "step": 42,
                                        "steps": 6, "started": 1, "updated": 2}),
                            json.dumps({"stage": "voice", "step": True,
                                        "steps": 6, "started": 1, "updated": 2}),
                            json.dumps({"stage": "bad", "step": 1,
                                        "steps": 6, "started": 1, "updated": 2}),
                            json.dumps({"stage": "voice", "step": 1,
                                        "steps": 6, "started": 50, "updated": 200}),
                            json.dumps({"stage": "voice", "step": 1,
                                        "steps": 6, "started": 1, "updated": 2,
                                        "private": "user token"})):
                data.write_text(payload, encoding="utf-8")
                self.assertIsNone(read_progress(data, now=100))
            data.unlink()
            data.symlink_to(private)
            self.assertIsNone(read_progress(data))
            self.assertEqual(private.read_text(), "private narration")
            self.assertIsNone(read_progress(folder / "other.json"))

    def test_monitoring_failures_cannot_interrupt_export(self):
        with tempfile.TemporaryDirectory() as root:
            reporter = ProgressReporter(Path(root))
            with patch("render_progress.os.replace", side_effect=OSError("storage full")):
                reporter.emit("voice")
            self.assertEqual(reporter.step, 1)
            self.assertEqual(list(Path(root).iterdir()), [])
            reporter.emit("assets")
            self.assertEqual(read_progress(Path(root) / PROGRESS_NAME)["stage"], "assets")

    def test_real_server_process_publishes_live_stage_and_cleans_on_success(self):
        """Verify sidecar travels from spawned Python child to HTTP polling."""
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs(Path(root) / "out")
            child = ("import json,os,time;from pathlib import Path;"
                     "from render_progress import ProgressReporter;"
                     "root=Path(os.environ['TMPDIR']).parent;"
                     "p=ProgressReporter(root);p.emit('encode');"
                     "time.sleep(.6);"
                     "(root/'video.mp4').write_bytes(b'0'*2100);"
                     "(root/'video.json').write_text(json.dumps({'segments': [],"
                     "'duration_seconds': 1}),encoding='utf-8')")
            from studio_server import validate_draft
            valid = {"topic": "Rendu long", "script": "La mer est calme.",
                     "lang": "fr", "aspect": "9:16", "resolution": 360,
                     "visuals": "abstract", "tts": "espeak", "speech_rate": 145,
                     "mute": True}
            with patch.object(jobs, "_arguments", return_value=[sys.executable, "-c", child]):
                job = jobs.submit(valid)
                ident = job["id"]
                seen = False
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    info = jobs.get(ident)
                    if info["status"] == "running" and info["progress"]:
                        self.assertEqual(info["progress"]["stage"], "encode")
                        self.assertEqual(info["progress"]["step"], 4)
                        seen = True
                    if info["status"] not in ("running", "queued"):
                        break
                    time.sleep(.04)
                self.assertTrue(seen, "HTTP job polling never observed actual child status")
                self.assertEqual(jobs.get(ident)["status"], "done", jobs.get(ident))
                # `done` is published before the worker's general finally
                # cleanup. Join that worker before testing scratch removal.
                # The stage file itself must already be absent at `done`.
                self.assertFalse((jobs.output_dir / ident / PROGRESS_NAME).exists())
                self.assertIsNone(jobs.get(ident)["progress"])
                jobs.worker_thread.join(timeout=5)
                self.assertFalse(jobs.worker_thread.is_alive())
                self.assertFalse((jobs.output_dir / ident / ".render-scratch").exists())
                self.assertTrue((jobs.output_dir / ident / "video.mp4").exists())

    def test_completed_jobs_hide_old_progress_and_running_jobs_expose_allowlisted_status(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs(Path(root) / "out")
            ident = "a" * 32
            directory = jobs.output_dir / ident
            directory.mkdir()
            from studio_server import validate_draft
            record = {"id": ident, "status": "running", "created": 12,
                      "finished": None, "error": None,
                      "parameters": validate_draft({
                          "topic": "Le soleil", "script": "Le soleil brille.",
                          "resolution": 360, "visuals": "abstract", "mute": True,
                          "lang": "fr", "aspect": "9:16", "tts": "espeak",
                          "speech_rate": 145}), "metadata": None}
            jobs.jobs[ident] = record
            reporter = ProgressReporter(directory)
            reporter.emit("encode")
            self.assertEqual(jobs.get(ident)["progress"]["stage"], "encode")
            with StudioServer(("127.0.0.1", 0), jobs,
                              auth_token="test-private-token") as server:
                import threading
                from http.client import HTTPConnection
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=4)
                    conn.request("GET", f"/api/jobs/{ident}", headers={
                        "Authorization": "Bearer test-private-token"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read())
                    self.assertEqual(payload["progress"]["stage"], "encode")
                    self.assertNotIn(str(directory), json.dumps(payload))
                    conn.close()
                finally:
                    server.shutdown()
                    thread.join(timeout=3)
            record["status"] = "done"
            self.assertIsNone(jobs.get(ident)["progress"])
            record["status"] = "cancelled"
            self.assertIsNone(jobs.get(ident)["progress"])
            record["status"] = "error"
            self.assertIsNone(jobs.get(ident)["progress"])


@unittest.skipUnless(all(shutil.which(name) for name in ("ffmpeg", "ffprobe")),
                     "Real FFmpeg required")
class RealRenderStageTests(unittest.TestCase):
    def test_real_two_scene_mp4_reports_every_stage_without_affecting_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "result.mp4"
            stages = []
            info = free_studio.render(
                "Essai Android", "Le soleil brille. La lune apparaît.",
                output, mute=True, resolution=360, fps=12,
                caption_style="classic", transition="fade",
                progress_callback=stages.append)
            self.assertEqual(stages, list(STAGES))
            self.assertTrue(output.is_file())
            self.assertTrue(validate(output, info, output.with_suffix(".srt"),
                                     probe(output))["ok"])
            self.assertFalse((Path(root) / PROGRESS_NAME).exists(),
                             "CLI callback must not create status files unless opted in")


if __name__ == "__main__":
    unittest.main()
