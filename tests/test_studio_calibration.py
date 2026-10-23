"""One-tap local encoder calibration: guarded server API and real FFmpeg."""
from __future__ import annotations

from http.client import HTTPConnection
import io
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio_calibration import LocalCalibration
from studio_server import Jobs, StudioServer


def advice(profile="detail"):
    return {"suggested_profile": profile,
            "measured_encoded_fps": {"balanced": 48.0, "detail": 35.0,
                                     "premium": 18.0},
            "applies_automatically": False,
            "warning": "Synthetic short-run only"}


class CalibrationUiWiringTests(unittest.TestCase):
    def test_mobile_page_controls_and_explicit_selection(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        for element in ('id="encoder-profile"', 'id="calibrate-encoder"',
                        'id="apply-calibration"', 'id="encoder-calibration-note"'):
            self.assertIn(element, page)
        self.assertIn("encoder_profile: $('encoder-profile').value", js)
        self.assertIn("post(calibrationApi, {resolution, fps: 24})", js)
        self.assertIn("$('apply-calibration').addEventListener('click'", js)
        self.assertIn("data.advice?.applies_automatically !== false", js)
        self.assertIn("clearTimeout(encoderPollTimer)", js)


class CalibrationWorkerTests(unittest.TestCase):
    def test_explicit_work_and_no_silent_encoder_change(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = LocalCalibration(Path(temp))
            with patch.object(worker, "_measure", return_value=advice()):
                status = worker.start(resolution=360)
                self.assertIn(status["status"], ("running", "done"))
                worker._thread.join(timeout=3)
            report = worker.status()
            self.assertEqual(report["status"], "done")
            self.assertEqual(report["suggested_profile"], "detail")
            self.assertEqual(report["advice"]["applies_automatically"], False)
            self.assertEqual(report["fps"], 24)
            worker.shutdown()

    def test_duplicate_workers_and_invalid_requests_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = LocalCalibration(Path(temp))
            for res, fps in ((True, 24), (360, True), (1080, 24),
                             (361, 24), (360, 12), (720, 30)):
                with self.subTest(res=res, fps=fps), self.assertRaises(ValueError):
                    worker.start(resolution=res, fps=fps)
            signal = threading.Event()
            proceed = threading.Event()
            def slow_measure(*args):
                signal.set()
                self.assertTrue(proceed.wait(timeout=3))
                return advice()
            with patch.object(worker, "_measure", side_effect=slow_measure):
                worker.start(resolution=360)
                self.assertTrue(signal.wait(2))
                self.assertTrue(worker.is_running())
                with self.assertRaises(RuntimeError):
                    worker.start(resolution=360)
                proceed.set()
                worker._thread.join(timeout=3)
            self.assertFalse(worker.is_running())
            worker.shutdown()
            with self.assertRaises(RuntimeError):
                worker.start(resolution=360)

    def test_failed_measure_preserves_default_and_failure_state(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = LocalCalibration(Path(temp))
            with patch.object(worker, "_measure",
                              side_effect=RuntimeError("encodage interrompu")):
                worker.start(resolution=720)
                worker._thread.join(timeout=3)
            report = worker.status()
            self.assertEqual(report["status"], "error")
            self.assertIsNone(report["suggested_profile"])
            self.assertIn("encodage interrompu", report["error"])
            worker.shutdown()


class CalibrationServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.jobs = Jobs(Path(self.temp.name) / "outputs")
        self.server = StudioServer(("127.0.0.1", 0), self.jobs,
                                   auth_token="test-local-calibration-token")
        self.port = self.server.server_port
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.calibration.shutdown()
        self.jobs.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=4)
        self.temp.cleanup()

    def request(self, method, path, payload=None, token="test-local-calibration-token",
                origin=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=12)
        headers = {"Authorization": "Bearer " + token}
        if origin:
            headers["Origin"] = origin
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        res = conn.getresponse()
        status = res.status
        result = json.loads(res.read().decode("utf-8"))
        conn.close()
        return status, result

    def test_access_requires_existing_studio_auth(self):
        for method, payload in (("GET", None), ("POST", {"resolution": 360, "fps": 24})):
            with self.subTest(method=method):
                status, _ = self.request(method, "/api/encoder/calibration",
                                         payload, token="wrong")
                self.assertEqual(status, 401)
                status, _ = self.request(method, "/api/encoder/calibration",
                                         payload, origin="https://evil.example")
                self.assertEqual(status, 403)
        self.assertEqual(self.request("GET", "/api/encoder/calibration")[1]["status"],
                         "idle")

    def test_bad_calibration_request_cannot_start_worker(self):
        for payload in ({}, {"resolution": 1080, "fps": 24},
                        {"resolution": 360, "fps": "24"},
                        {"resolution": 360, "fps": 24, "x": "intrusion"},
                        {"resolution": True, "fps": 24}):
            with self.subTest(payload=payload):
                status, _ = self.request("POST", "/api/encoder/calibration", payload)
                self.assertEqual(status, 400)
        self.assertEqual(self.server.calibration.status()["status"], "idle")

    def test_one_tap_produces_safe_advice_and_blocks_parallel_render(self):
        entered, release = threading.Event(), threading.Event()
        def slow(resolution, fps):
            entered.set()
            self.assertTrue(release.wait(timeout=4))
            return advice("premium")
        with patch.object(self.server.calibration, "_measure", side_effect=slow):
            status, first = self.request(
                "POST", "/api/encoder/calibration", {"resolution": 360, "fps": 24})
            self.assertEqual(status, 202)
            self.assertEqual(first["status"], "running")
            self.assertTrue(entered.wait(2))
            code, _ = self.request(
                "POST", "/api/encoder/calibration", {"resolution": 360, "fps": 24})
            self.assertEqual(code, 400)
            status, state = self.request("GET", "/api/encoder/calibration")
            self.assertEqual(status, 200)
            self.assertEqual(state["status"], "running")
            code, payload = self.request("POST", "/api/jobs", {
                "topic": "Test", "script": "Une petite vidéo.",
                "lang": "fr", "aspect": "9:16", "resolution": 360,
                "visuals": "abstract", "tts": "espeak",
                "speech_rate": 145, "mute": True})
            self.assertEqual(code, 400)
            self.assertIn("Calibration H.264", payload["error"])
            batch = self.jobs.create_media_batch()
            with self.assertRaisesRegex(ValueError, "Calibration H.264"):
                self.jobs.save_media(batch, "image.jpg", 4, io.BytesIO(b"bad!"))
            release.set()
            self.server.calibration._thread.join(timeout=3)
            _, completed = self.request("GET", "/api/encoder/calibration")
            self.assertEqual(completed["status"], "done")
            self.assertFalse(completed["advice"]["applies_automatically"])

    def test_active_render_blocks_calibration_without_starting_worker(self):
        with self.jobs.lock:
            self.jobs.jobs["b" * 32] = {
                "id": "b" * 32, "status": "queued",
                "created": int(time.time()), "finished": None,
                "error": None, "metadata": None,
                "parameters": {"topic": "Active", "resolution": 360,
                               "media_batch": ""}}
        code, response = self.request("POST", "/api/encoder/calibration",
                                      {"resolution": 360, "fps": 24})
        self.assertEqual(code, 409)
        self.assertIn("rendu", response["error"])
        self.assertEqual(self.server.calibration.status()["status"], "idle")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real local FFmpeg comparison required")
class CalibrationRealFFmpegTests(unittest.TestCase):
    def test_real_synthetic_360p_24fps_experiment(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = LocalCalibration(Path(folder))
            result = worker.start(resolution=360, fps=24)
            self.assertEqual(result["resolution"], 360)
            worker._thread.join(timeout=240)
            self.assertFalse(worker._thread.is_alive(), worker.status())
            report = worker.status()
            self.assertEqual(report["status"], "done", report)
            self.assertIn(report["suggested_profile"], ("balanced", "detail", "premium"))
            self.assertFalse(report["advice"]["applies_automatically"])
            self.assertEqual(report["advice"]["throughput_basis"],
                             "slowest-encoded-scenario-fps")
            self.assertEqual(len(list(worker.output.glob("videos/*.mp4"))), 8)
            self.assertTrue((worker.output / "metrics.json").is_file())
            worker.shutdown()


if __name__ == "__main__":
    unittest.main()
