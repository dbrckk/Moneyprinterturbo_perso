"""Disk-space floor protects Android renders and imported sources."""
from __future__ import annotations

from collections import namedtuple
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from studio_server import Jobs, RENDER_MIN_FREE_BYTES, StudioServer, validate_draft


VALID = {"topic": "Rendu mobile", "script": "La vidéo dure plusieurs scènes.",
         "lang": "fr", "aspect": "9:16", "resolution": 360,
         "visuals": "abstract", "tts": "espeak", "speech_rate": 145,
         "mute": True}
Usage = namedtuple("Usage", "total used free")


def available(bytes_free: int) -> Usage:
    return Usage(8 * 1024**3, 8 * 1024**3 - bytes_free, bytes_free)


class DiskPreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.jobs = Jobs(Path(temp.name) / "render")

    def test_resolution_thresholds_and_exact_boundary(self):
        self.assertEqual(RENDER_MIN_FREE_BYTES, {
            360: 256 * 1024**2, 720: 512 * 1024**2,
            1080: 1024 * 1024**2})
        for resolution, floor in RENDER_MIN_FREE_BYTES.items():
            with self.subTest(resolution=resolution):
                with patch("studio_server.shutil.disk_usage", return_value=available(floor)) as usage:
                    self.jobs._ensure_render_disk_space(resolution)
                    usage.assert_called_once_with(self.jobs.output_dir)
                with patch("studio_server.shutil.disk_usage", return_value=available(floor - 1)):
                    with self.assertRaisesRegex(ValueError, f"{resolution}p"):
                        self.jobs._ensure_render_disk_space(resolution)

    def test_rejected_submission_preserves_upload_and_creates_no_render(self):
        batch = self.jobs.create_media_batch()
        original = self.jobs.upload_root / batch / "001_camera.mp4"
        original.write_bytes(b"original phone footage")
        with patch("studio_server.shutil.disk_usage", return_value=available(17 * 1024**2)):
            with self.assertRaisesRegex(ValueError, "Espace insuffisant"):
                self.jobs.submit({**VALID, "resolution": 720, "visuals": "local",
                                  "media_batch": batch})
        self.assertEqual(original.read_bytes(), b"original phone footage")
        self.assertFalse((original.parent / ".sealed").exists())
        self.assertFalse(self.jobs.jobs)
        self.assertFalse(self.jobs.running)
        self.assertFalse(any(p.name == ".job_state.json"
                             for p in self.jobs.output_dir.rglob(".job_state.json")))

    def test_unmeasurable_storage_rejected_before_checkpoint(self):
        with patch("studio_server.shutil.disk_usage", side_effect=OSError("no statvfs")):
            with self.assertRaisesRegex(ValueError, "impossible à vérifier"):
                self.jobs.submit(VALID)
        self.assertFalse(self.jobs.jobs)
        self.assertFalse(self.jobs.running)

    def test_space_exhausted_while_queued_becomes_persisted_error_without_ffmpeg(self):
        ident = "d" * 32
        record = {"id": ident, "status": "queued", "created": 123,
                  "finished": None, "error": None,
                  "parameters": validate_draft(VALID), "metadata": None}
        self.jobs.jobs[ident] = record
        self.jobs._checkpoint(record)
        original = self.jobs.output_dir / ident / "video.mp4"
        original.write_bytes(b"preserve my preexisting output")
        with patch("studio_server.shutil.disk_usage", return_value=available(0)), \
             patch.object(self.jobs, "_arguments", side_effect=AssertionError("render was launched")):
            self.jobs._worker()
        result = self.jobs.get(ident)
        self.assertEqual(result["status"], "error")
        self.assertIn("Espace insuffisant", result["error"])
        self.assertEqual(original.read_bytes(), b"preserve my preexisting output")
        self.assertFalse((original.parent / ".render-scratch").exists())
        self.assertEqual(Jobs(self.jobs.output_dir).get(ident)["status"], "error")

    def test_http_error_is_actionable_and_does_not_enqueue(self):
        server = StudioServer(("127.0.0.1", 0), self.jobs,
                              auth_token="preflight-test-secret")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("studio_server.shutil.disk_usage", return_value=available(8 * 1024**2)):
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                payload = json.dumps(VALID).encode()
                conn.request("POST", "/api/jobs", body=payload, headers={
                    "Authorization": "Bearer preflight-test-secret",
                    "Content-Type": "application/json"})
                response = conn.getresponse()
                error = json.loads(response.read())
                self.assertEqual(response.status, 400)
                self.assertIn("Libérez de la place", error["error"])
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertFalse(self.jobs.jobs)


if __name__ == "__main__":
    unittest.main()
