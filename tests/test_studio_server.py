"""Integration tests for the mobile web studio without cloud services."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest

from studio_server import Jobs, StudioServer, validate_draft


VALID = {"topic": "Énergie solaire", "script": "Le soleil produit de la lumière.",
         "lang": "fr", "aspect": "9:16", "resolution": 360,
         "visuals": "abstract", "tts": "espeak", "speech_rate": 145, "mute": True}


class ValidationTests(unittest.TestCase):
    def test_input_contract(self):
        self.assertEqual(len(validate_draft(VALID)["sentences"]), 1)
        for replacement in ({"resolution": True}, {"resolution": "360"}, {"lang": "xyz"},
                            {"topic": "\nabc"}, {"aspect": "2:7"}, {"tts": "cloud"},
                            {"visuals": "file:///"}, {"scene_queries": "a\nb"},
                            {"speech_rate": -100}, {"mute": "true"}, {"script": ""}):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                validate_draft({**VALID, **replacement})

    def test_scene_override(self):
        data = {**VALID, "visuals": "commons", "scene_queries": "soleil"}
        self.assertEqual(validate_draft(data)["scene_queries"], ["soleil"])
        with self.assertRaises(ValueError):
            validate_draft({**data, "scene_queries": "soleil|attaque"})


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="studio-test-")
        self.jobs = Jobs(Path(self.temp.name) / "output")
        self.server = StudioServer(("127.0.0.1", 0), self.jobs, auth_token="a-safe-secret-for-tests")
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        # Finish worker before deleting its output folder.
        deadline = time.monotonic() + 20
        while self.jobs.running and time.monotonic() < deadline:
            time.sleep(.05)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=20)
        header = {"Authorization": "Bearer a-safe-secret-for-tests"}
        if headers: header.update(headers)
        if body is not None:
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            header["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=header)
        response = conn.getresponse()
        data = response.read()
        outcome = response.status, dict(response.getheaders()), data
        conn.close()
        return outcome

    def test_ui_security_and_draft(self):
        status, headers, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"MONEYPRINTER", payload)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        status, headers, js = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"loadVideo", js)
        status, _, _ = self.request("GET", "/not-a-file/../secrets")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/api/jobs", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(status, 401)
        status, _, _ = self.request("POST", "/api/jobs", VALID,
                                    headers={"Origin": "https://attacker.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/jobs", headers={"Host": "evil.test"})
        self.assertEqual(status, 403)
        status, _, data = self.request("POST", "/api/draft", {"topic": "Mers de France", "lang": "fr"})
        self.assertEqual(status, 200)
        draft = json.loads(data)
        self.assertEqual(draft["mode"], "deterministic-template-not-ai")
        self.assertIn("Mers de France", draft["script"])
        status, _, _ = self.request("POST", "/api/jobs", {**VALID, "script": ""})
        self.assertEqual(status, 400)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
    def test_render_job_to_video_and_download(self):
        status, _, data = self.request("POST", "/api/jobs", VALID)
        self.assertEqual(status, 202, data)
        job = json.loads(data)
        self.assertRegex(job["id"], r"^[a-f0-9]{32}$")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status, _, data = self.request("GET", "/api/jobs/" + job["id"])
            self.assertEqual(status, 200)
            result = json.loads(data)
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(.1)
        self.assertEqual(result["status"], "done", result)
        self.assertGreater(result["duration_seconds"], 2)
        for filename, marker in (("video.mp4", b"ftyp"), ("video.srt", b"-->"), ("video.json", b"segments")):
            status, headers, data = self.request("GET", f"/api/jobs/{job['id']}/{filename}")
            self.assertEqual(status, 200)
            self.assertIn(marker, data)
            self.assertEqual(int(headers["Content-Length"]), len(data))
        status, _, _ = self.request("GET", f"/api/jobs/{job['id']}/video.credits.txt")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", f"/api/jobs/{job['id']}/../../etc/passwd")
        self.assertEqual(status, 404)

    def test_queue_rejection_cancel_and_no_file_read(self):
        status, _, data = self.request("POST", "/api/jobs", {**VALID, "visuals": "local"})
        self.assertEqual(status, 400)
        status, _, data = self.request("POST", "/api/jobs", {**VALID, "tts": "piper", "mute": False})
        self.assertEqual(status, 400)
        status, _, data = self.request("GET", "/api/jobs/" + "a" * 32 + "/video.mp4")
        self.assertEqual(status, 404)
        status, _, data = self.request("POST", "/api/jobs/" + "a" * 32 + "/cancel")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
