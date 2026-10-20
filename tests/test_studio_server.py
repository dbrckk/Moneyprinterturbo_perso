"""Integration tests for the mobile web studio without cloud services."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import subprocess
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

    def send_binary(self, batch: str, name: str, data: bytes, *, headers=None):
        from urllib.parse import quote
        conn = HTTPConnection("127.0.0.1", self.port, timeout=25)
        base = {"Authorization": "Bearer a-safe-secret-for-tests",
                "Content-Type": "application/octet-stream",
                "X-Media-Name": quote(name, safe="")}
        if headers:
            base.update(headers)
        conn.request("POST", f"/api/media/batches/{batch}/files",
                     body=data, headers=base)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_failed_submit_does_not_seal_uploaded_media(self):
        status, _, payload = self.request("POST", "/api/media/batches", {})
        self.assertEqual(status, 201)
        batch = json.loads(payload)["id"]
        # Simulate a valid already-imported file without encoding media:
        # the submission validation must run before the batch is sealed.
        folder = self.jobs.upload_root / batch
        (folder / "001_example.mp4").write_bytes(b"existing imported content")
        status, _, response = self.request("POST", "/api/jobs", {
            **VALID, "visuals": "local", "media_batch": batch, "tts": "piper"})
        self.assertEqual(status, 400, response)
        status, _, details = self.request("GET", f"/api/media/batches/{batch}")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(details)["sealed"])

    def test_upload_guards_reject_broken_or_unsafe_media(self):
        status, _, payload = self.request("POST", "/api/media/batches", {})
        self.assertEqual(status, 201, payload)
        batch = json.loads(payload)["id"]
        self.assertEqual(self.send_binary(batch, "../escape.mp4", b"invalid")[0], 400)
        self.assertEqual(self.send_binary(batch, "bad.exe", b"invalid")[0], 400)
        self.assertEqual(self.send_binary(batch, "movie.mp4", b"not a real video")[0], 400)
        self.assertEqual(self.send_binary(batch, "movie.mp4", b"data",
                                          headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.send_binary(batch, "movie.mp4", b"data",
                                          headers={"Authorization": "Bearer wrong"})[0], 401)
        status, _, details = self.request("GET", f"/api/media/batches/{batch}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(details)["files"], [])
        self.assertFalse((self.jobs.output_dir / "escape.mp4").exists())
        status, _, deleted = self.request("POST", f"/api/media/batches/{batch}/delete", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(deleted)["deleted"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg and ffprobe required")
    def test_real_phone_video_upload_and_render_are_isolated_by_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "clip2.mp4"
            result = subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=red:s=320x240:r=12:d=4",
                 "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(first)],
                capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            status, _, created = self.request("POST", "/api/media/batches", {})
            self.assertEqual(status, 201)
            batch = json.loads(created)["id"]
            status, imported = self.send_binary(batch, "clip2.mp4", first.read_bytes())
            self.assertEqual(status, 201, imported)
            self.assertEqual(json.loads(imported)["name"], "001_clip2.mp4")
            status, _, data = self.request("GET", f"/api/media/batches/{batch}")
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(data)["files"]), 1)
            status, _, response = self.request("POST", "/api/jobs", {
                **VALID, "visuals": "local", "media_batch": batch})
            self.assertEqual(status, 202, response)
            job = json.loads(response)
            self.assertEqual(self.send_binary(batch, "clip10.mp4", first.read_bytes())[0], 400)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                _, _, data = self.request("GET", f"/api/jobs/{job['id']}")
                snapshot = json.loads(data)
                if snapshot["status"] not in ("queued", "running"):
                    break
                time.sleep(.15)
            self.assertEqual(snapshot["status"], "done", snapshot)
            status, _, manifest = self.request("GET", f"/api/jobs/{job['id']}/video.json")
            self.assertEqual(status, 200)
            self.assertTrue(any(
                "001_clip2.mp4" in media.get("title", "")
                for media in json.loads(manifest)["media"]))
            status, _, deleted = self.request("POST", f"/api/media/batches/{batch}/delete", {})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(deleted)["deleted"])

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
        status, headers, credits = self.request("GET", f"/api/jobs/{job['id']}/video.credits.txt")
        self.assertEqual(status, 200)
        self.assertIn(b"ASSET & VOICE CREDITS", credits)
        self.assertIn(b"Original animated graphic backgrounds", credits)
        self.assertIn(b"Voice: None", credits)
        self.assertEqual(int(headers["Content-Length"]), len(credits))
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
