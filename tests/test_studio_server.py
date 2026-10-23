"""Integration tests for the mobile web studio without cloud services."""
from __future__ import annotations

from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio_server import Jobs, StudioServer, parse_video_range, validate_draft


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


class ByteRangeTests(unittest.TestCase):
    def test_single_range_edges_are_bounded_and_valid(self):
        cases = {
            "bytes=0-0": (0, 0),
            "bytes=50-99": (50, 99),
            "bytes=50-": (50, 99),
            "bytes=-10": (90, 99),
            "bytes=-1000": (0, 99),
            "bytes=0-200": (0, 99),
            "bytes=00-009": (0, 9),
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                self.assertEqual(parse_video_range(header, 100), expected)

    def test_malformed_or_unsatisfiable_ranges_rejected(self):
        for header in ("bytes=100-", "bytes=1000-2000", "bytes=7-6",
                       "bytes=-0", "bytes=-", "bytes=0-5,10-14",
                       "items=0-3", "bytes=foo-bar", "bytes=-10-20",
                       "bytes=--", "bytes= 1-5", "bytes=1 - 5"):
            with self.subTest(header=header):
                self.assertIsNone(parse_video_range(header, 100))
        self.assertIsNone(parse_video_range("bytes=0-", 0))
        self.assertIsNone(parse_video_range("bytes=0-", -5))


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

    def test_rejected_jobs_never_seal_existing_media_uploads(self):
        status, _, response = self.request("POST", "/api/media/batches", {})
        self.assertEqual(status, 201, response)
        batch = json.loads(response)["id"]
        folder = self.jobs.upload_root / batch
        # The metadata and capacity gate must be exercised on a populated
        # batch; the media decoder is independently tested in upload tests.
        (folder / "001_existing.mp4").write_bytes(b"already-imported")
        options = {**VALID, "visuals": "local", "media_batch": batch}
        # Unsupported voice must be rejected before the batch is sealed.
        code, _, payload = self.request("POST", "/api/jobs", {
            **options, "tts": "piper", "mute": False})
        self.assertEqual(code, 400, payload)
        self.assertFalse((folder / ".sealed").exists())
        # Queue capacity rejection must preserve the same import as well.
        with patch("studio_server.MAX_JOBS", 1):
            with self.jobs.lock:
                self.jobs.jobs["occupied"] = {"status": "queued"}
            code, _, payload = self.request("POST", "/api/jobs", options)
            self.assertEqual(code, 400, payload)
        self.assertFalse((folder / ".sealed").exists())
        self.assertEqual(self.request("GET", f"/api/media/batches/{batch}")[0], 200)

    def test_large_phone_upload_does_not_block_status_and_guards_active_batch(self):
        """A slow phone upload must never stall unrelated HTTP status requests."""
        class PausedUpload:
            def __init__(self, data):
                self.payload = data
                self.position = 0
                self.ready = threading.Event()
                self.release = threading.Event()
            def read(self, limit):
                if self.position:
                    self.ready.set()
                    if not self.release.wait(8):
                        raise TimeoutError("test did not resume stream")
                block = self.payload[self.position:self.position + min(limit, 300)]
                self.position += len(block)
                return block

        first = self.jobs.create_media_batch()
        second = self.jobs.create_media_batch()
        payload = b"streamed-phone-footage" * 60
        paused = PausedUpload(payload)
        results = []
        errors = []
        def upload():
            try:
                results.append(self.jobs.save_media(first, "footage.mp4",
                                                    len(payload), paused))
            except Exception as exc:
                errors.append(exc)
        with patch("studio_server.media_dimensions", return_value=(320, 240)):
            worker = threading.Thread(target=upload, daemon=True)
            worker.start()
            try:
                self.assertTrue(paused.ready.wait(3), "phone stream did not pause")
                started = time.monotonic()
                status, _, snapshot = self.request("GET", "/api/media/storage")
                self.assertEqual(status, 200)
                self.assertLess(time.monotonic() - started, 3.0,
                                "slow upload blocked another HTTP request")
                entries = {x["id"]: x for x in json.loads(snapshot)["batches"]}
                self.assertTrue(entries[first]["uploading"])
                self.assertFalse(entries[second]["uploading"])
                self.assertEqual(self.jobs.batch_info(first)["files"], [])
                self.assertEqual(self.request("POST",
                    f"/api/media/batches/{first}/delete", {})[0], 409)
                with self.assertRaisesRegex(ValueError, "Import en cours"):
                    self.jobs.submit({**VALID, "visuals": "local",
                                      "media_batch": first})
                self.assertFalse((self.jobs.upload_root / first / ".sealed").exists())
                with self.assertRaisesRegex(ValueError, "Import déjà en cours"):
                    self.jobs.save_media(first, "duplicate.mp4", 10,
                                         io.BytesIO(b"0" * 10))
                # Concurrent uploads on *different* batches must reserve
                # their full declared size, even while bytes are in flight.
                with patch("studio_server.MAX_MEDIA_STORAGE", len(payload) + 99):
                    with self.assertRaisesRegex(ValueError, "Stockage média local plein"):
                        self.jobs.save_media(second, "over-capacity.mp4", 100,
                                             io.BytesIO(b"0" * 100))
                committed = self.jobs.save_media(
                    second, "independent.mp4", 10, io.BytesIO(b"1234567890"))
                self.assertEqual(committed["count"], 1)
            finally:
                paused.release.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["count"], 1)
        self.assertEqual((self.jobs.upload_root / first /
                          "001_footage.mp4").read_bytes(), payload)
        self.assertEqual(self.jobs._uploads_in_progress, {})
        self.assertFalse(any(batch["uploading"] for batch in
                             self.jobs.media_storage()["batches"]))
        self.assertTrue(self.jobs.delete_media_batch(first))

    def test_failed_stream_releases_capacity_and_partial_files(self):
        batch = self.jobs.create_media_batch()
        folder = self.jobs.upload_root / batch
        with patch("studio_server.media_dimensions", return_value=(320, 240)):
            with self.assertRaisesRegex(ValueError, "Transfert incomplet"):
                self.jobs.save_media(batch, "interrupted.mp4", 60,
                                     io.BytesIO(b"truncated"))
            self.assertEqual(self.jobs._uploads_in_progress, {})
            self.assertEqual(list(folder.iterdir()), [])
            with patch("studio_server.MAX_MEDIA_STORAGE", 60):
                # Entire original reservation must have been relinquished.
                result = self.jobs.save_media(batch, "retry.mp4", 60,
                                              io.BytesIO(b"R" * 60))
                self.assertEqual(result["bytes"], 60)
        self.assertEqual(len(self.jobs._batch_files(folder)), 1)

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

    def test_restart_recovers_interrupted_jobs_without_restarting_render(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Jobs(Path(temp) / "render")
            job_id = "a" * 32
            pending = {"id": job_id, "created": 100, "finished": None,
                       "status": "running", "error": None, "metadata": None,
                       "parameters": {"topic": "Test interruption", "resolution": 360,
                                      "media_batch": ""}}
            original._checkpoint(pending)
            recovered = Jobs(original.output_dir)
            snapshot = recovered.get(job_id)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["status"], "error")
            self.assertIn("interrompu", snapshot["error"])
            self.assertFalse(recovered.running)
            self.assertFalse(recovered.processes)
            self.assertEqual(Jobs(original.output_dir).get(job_id)["status"], "error")

    def test_bad_or_missing_completed_output_is_not_offered_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Jobs(Path(temp) / "render")
            job_id = "b" * 32
            old = {"id": job_id, "created": 100, "finished": 101,
                   "status": "done", "error": None, "metadata": None,
                   "parameters": {"topic": "Export incomplet", "resolution": 360,
                                  "media_batch": ""}}
            original._checkpoint(old)
            recovered = Jobs(original.output_dir)
            self.assertEqual(recovered.get(job_id)["status"], "error")
            self.assertIsNone(recovered.artifact(job_id, "video.mp4"))

    def test_invalid_render_request_never_seals_user_uploaded_media(self):
        batch = self.jobs.create_media_batch()
        folder = self.jobs.upload_root / batch
        (folder / "001_test.mp4").write_bytes(b"file for queue validation")
        with self.assertRaises(ValueError):
            self.jobs.submit({**VALID, "visuals": "local",
                              "media_batch": batch, "tts": "piper", "mute": False})
        self.assertFalse((folder / ".sealed").exists())
        self.assertFalse(self.jobs.running)

    def test_media_storage_and_cleanup_keep_final_exports(self):
        batch_done = self.jobs.create_media_batch()
        completed_folder = self.jobs.upload_root / batch_done
        (completed_folder / "001_phone.mp4").write_bytes(b"media-source" * 30)
        (completed_folder / ".sealed").touch()

        job_id = "c" * 32
        job_dir = self.jobs.output_dir / job_id
        saved = {"id": job_id, "created": 100, "finished": 101,
                 "status": "done", "error": None, "metadata": None,
                 "parameters": {"topic": "Vidéo finale", "resolution": 360,
                                "media_batch": batch_done}}
        self.jobs._checkpoint(saved)
        # A completed checkpoint without deliverable files is not enough to
        # destroy the source footage, even on an explicit cleanup request.
        self.assertEqual(self.jobs.media_storage()["reclaimable_count"], 0)
        (job_dir / "video.mp4").write_bytes(b"ftyp" + b"X" * 1600)
        (job_dir / "video.json").write_text('{"segments": [1]}', encoding="utf-8")

        batch_draft = self.jobs.create_media_batch()
        draft_dir = self.jobs.upload_root / batch_draft
        (draft_dir / "001_saved.mp4").write_bytes(b"keep me")
        batch_error = self.jobs.create_media_batch()
        error_dir = self.jobs.upload_root / batch_error
        (error_dir / "001_error.mp4").write_bytes(b"keep failed footage")
        (error_dir / ".sealed").touch()
        batch_active = self.jobs.create_media_batch()
        active_dir = self.jobs.upload_root / batch_active
        (active_dir / "001_active.mp4").write_bytes(b"keep rendering")
        (active_dir / ".sealed").touch()
        active_job_id = "d" * 32
        active = {"id": active_job_id, "created": 102, "finished": None,
                  "status": "queued", "error": None, "metadata": None,
                  "parameters": {"topic": "En cours", "resolution": 360,
                                 "media_batch": batch_active}}
        self.jobs.jobs[active_job_id] = active

        usage = self.jobs.media_storage()
        self.assertEqual(usage["batch_count"], 4)
        self.assertEqual(usage["reclaimable_count"], 1)
        self.assertGreater(usage["reclaimable_bytes"], 0)
        status, _, response = self.request("GET", "/api/media/storage")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)["reclaimable_count"], 1)

        status, _, response = self.request("POST", "/api/media/cleanup",
                                           {"confirm": True})
        self.assertEqual(status, 400)
        self.assertTrue(completed_folder.exists())
        status, _, response = self.request("POST", "/api/media/cleanup", {})
        self.assertEqual(status, 200, response)
        result = json.loads(response)
        self.assertEqual(result["deleted_batches"], [batch_done])
        self.assertFalse(completed_folder.exists())
        self.assertEqual(result["storage"]["reclaimable_count"], 0)
        self.assertTrue(draft_dir.exists())
        self.assertTrue(error_dir.exists())
        self.assertTrue(active_dir.exists())
        self.assertTrue((job_dir / "video.mp4").exists())
        self.assertTrue((job_dir / "video.json").exists())
        status, _, _ = self.request("POST", "/api/media/cleanup", {})
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/api/media/storage",
                                    headers={"Authorization": "Bearer invalid"})
        self.assertEqual(status, 401)
        status, _, _ = self.request("POST", "/api/media/cleanup", {},
                                    headers={"Origin": "https://example.evil"})
        self.assertEqual(status, 403)

    def test_prune_abandoned_empty_uploads_only(self):
        empty_batch = self.jobs.create_media_batch()
        folder_empty = self.jobs.upload_root / empty_batch
        old = time.time() - 2 * 86400
        os.utime(folder_empty, (old, old))

        protected = self.jobs.create_media_batch()
        protected_dir = self.jobs.upload_root / protected
        (protected_dir / "001_keep.jpg").write_bytes(b"USER PHOTO")
        os.utime(protected_dir, (old, old))

        sealed = self.jobs.create_media_batch()
        sealed_dir = self.jobs.upload_root / sealed
        (sealed_dir / ".sealed").touch()
        os.utime(sealed_dir, (old, old))

        interrupted = self.jobs.create_media_batch()
        partial_dir = self.jobs.upload_root / interrupted
        partial = partial_dir / ".receiving-stuck.mp4"
        partial.write_bytes(b"partial")
        os.utime(partial, (old, old))
        (partial_dir / "001_keep.mp4").write_bytes(b"USER VIDEO")
        os.utime(partial_dir, (old, old))

        fresh = self.jobs.create_media_batch()
        fresh_dir = self.jobs.upload_root / fresh
        self.jobs.create_media_batch()
        self.assertFalse(folder_empty.exists())
        self.assertTrue(protected_dir.exists())
        self.assertTrue((protected_dir / "001_keep.jpg").exists())
        self.assertTrue(sealed_dir.exists())
        self.assertTrue(fresh_dir.exists())
        self.assertFalse(partial.exists())
        self.assertTrue((partial_dir / "001_keep.mp4").exists())

    def test_storage_capacity_counts_interrupted_temporary_media(self):
        batch = self.jobs.create_media_batch()
        folder = self.jobs.upload_root / batch
        (folder / ".receiving-stuck.mp4").write_bytes(b"X" * 32)
        with patch("studio_server.MAX_MEDIA_STORAGE", 40):
            info = self.jobs.media_storage()
            self.assertEqual(info["bytes"], 32)
            with self.assertRaisesRegex(ValueError, "Stockage média local plein"):
                self.jobs.save_media(batch, "another.mp4", 16,
                                     io.BytesIO(b"x" * 16))

    def test_abandoned_media_cleanup_protects_active_current_and_recent(self):
        old = time.time() - 10 * 86400

        def batch(sealed=False, old_file=True):
            ident = self.jobs.create_media_batch()
            directory = self.jobs.upload_root / ident
            source = directory / "001_source.mp4"
            source.write_bytes(b"original footage")
            if sealed:
                (directory / ".sealed").touch()
            if old_file:
                for child in directory.iterdir():
                    os.utime(child, (old, old))
                os.utime(directory, (old, old))
            return ident, directory

        old_draft, draft_dir = batch()
        selected_draft, selected_dir = batch()
        recent_draft, recent_dir = batch(old_file=False)
        old_failed, failed_dir = batch(sealed=True)
        recent_failed, recent_failed_dir = batch(sealed=True)
        unknown_sealed, unknown_sealed_dir = batch(sealed=True)
        done_id, done_dir = batch(sealed=True)
        active_id, active_dir = batch(sealed=True)

        def checkpoint(ident, suffix, status, *, old_state):
            job_id = suffix * 32
            record = {"id": job_id, "created": 100, "finished": 101,
                      "status": status, "error": None, "metadata": None,
                      "parameters": {"topic": "Montage test", "resolution": 360,
                                     "media_batch": ident}}
            self.jobs._checkpoint(record)
            if old_state:
                state_file = self.jobs.output_dir / job_id / ".job_state.json"
                os.utime(state_file, (old, old))
            return record

        # Old references must be found even when evicted from in-memory history.
        checkpoint(old_failed, "a", "error", old_state=True)
        checkpoint(recent_failed, "b", "error", old_state=False)
        checkpoint(done_id, "c", "done", old_state=True)
        active = checkpoint(active_id, "d", "queued", old_state=True)
        self.jobs.jobs[active["id"]] = active

        stats = self.jobs.media_storage()
        eligible = {p["id"] for p in stats["batches"] if p["abandoned"]}
        self.assertEqual(eligible, {old_draft, selected_draft, old_failed})
        self.assertEqual(stats["abandoned_count"], 3)
        self.assertEqual(sum(p["bytes"] for p in stats["batches"]),
                         stats["bytes"])

        status, _, body = self.request("GET", "/api/media/storage")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["abandoned_count"], 3)
        status, _, body = self.request("POST", "/api/media/cleanup-abandoned",
                                       {"protect_batch": "invalid"})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/media/cleanup-abandoned",
                                    {"unapproved": True})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/media/cleanup-abandoned", {},
                                    headers={"Authorization": "Bearer invalid"})
        self.assertEqual(status, 401)
        status, _, _ = self.request("POST", "/api/media/cleanup-abandoned", {},
                                    headers={"Origin": "https://attacker.example"})
        self.assertEqual(status, 403)

        status, _, data = self.request("POST", "/api/media/cleanup-abandoned",
                                      {"protect_batch": selected_draft})
        self.assertEqual(status, 200, data)
        deleted = json.loads(data)
        self.assertEqual(set(deleted["deleted_batches"]), {old_draft, old_failed})
        self.assertFalse(draft_dir.exists())
        self.assertFalse(failed_dir.exists())
        for directory in (selected_dir, recent_dir, recent_failed_dir,
                          unknown_sealed_dir, done_dir, active_dir):
            self.assertTrue(directory.is_dir())
            self.assertTrue((directory / "001_source.mp4").is_file())
        self.assertEqual(self.jobs.media_storage()["abandoned_count"], 1)
        self.assertEqual(self.jobs.cleanup_abandoned_media(
            protect_batch=selected_draft)["deleted_batches"], [])
        self.assertTrue(selected_dir.exists())

    def test_hidden_user_content_blocks_automatic_empty_batch_prune(self):
        batch = self.jobs.create_media_batch()
        directory = self.jobs.upload_root / batch
        (directory / ".notes").write_text("do not remove", encoding="utf-8")
        old = time.time() - 10 * 86400
        os.utime(directory / ".notes", (old, old))
        os.utime(directory, (old, old))
        self.jobs.create_media_batch()
        self.assertTrue((directory / ".notes").exists())

    def test_ui_security_and_draft(self):
        status, headers, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"MONEYPRINTER", payload)
        self.assertIn(b"media-files", payload)
        self.assertIn(b"R\xc3\xa9essayer l'import", payload)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        status, headers, js = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"loadVideo", js)
        self.assertIn(b"$('media-files').addEventListener('change'", js)
        self.assertIn(b"$('media-files').disabled = true", js)
        self.assertIn(b"$('media-files').disabled = false", js)
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
        # A real Android/Termux process restart clears Python memory, but
        # completed videos, their QA metadata and download links must survive.
        saved = self.jobs.output_dir / job["id"] / ".job_state.json"
        summary = json.loads(saved.read_text(encoding="utf-8"))
        self.assertNotIn("script", summary)
        self.assertEqual(summary["status"], "done")
        recovered = Jobs(self.jobs.output_dir)
        self.assertEqual(recovered.get(job["id"])["status"], "done")
        self.assertAlmostEqual(recovered.get(job["id"])["duration_seconds"],
                               result["duration_seconds"])
        self.assertEqual(recovered.artifact(job["id"], "video.mp4"),
                         self.jobs.artifact(job["id"], "video.mp4"))
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

    def test_native_mp4_byte_ranges_stream_and_protect_completed_jobs(self):
        """Seek into a published MP4 without fetching the whole video."""
        job_id = "e" * 32
        folder = self.jobs.output_dir / job_id
        folder.mkdir()
        video = (b"\x00\x00\x00\x18ftypisom" +
                 bytes(range(256)) * 550)
        (folder / "video.mp4").write_bytes(video)
        (folder / "video.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nTest\n")
        with self.jobs.lock:
            self.jobs.jobs[job_id] = {"status": "done"}
        endpoint = f"/api/jobs/{job_id}/video.mp4"
        status, headers, body = self.request("GET", endpoint)
        self.assertEqual(status, 200)
        self.assertEqual(body, video)
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertIn("inline", headers["Content-Disposition"])
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertEqual(int(headers["Content-Length"]), len(video))
        for value, start, end in (
            ("bytes=0-0", 0, 0),
            ("bytes=5-500", 5, 500),
            ("bytes=500-", 500, len(video) - 1),
            ("bytes=-32", len(video) - 32, len(video) - 1),
            ("bytes=0-9999999", 0, len(video) - 1),
        ):
            with self.subTest(header=value):
                status, headers, result = self.request("GET", endpoint,
                                                     headers={"Range": value})
                self.assertEqual(status, 206)
                self.assertEqual(result, video[start:end + 1])
                self.assertEqual(headers["Content-Range"],
                                 f"bytes {start}-{end}/{len(video)}")
                self.assertEqual(int(headers["Content-Length"]), len(result))
                self.assertEqual(headers["Accept-Ranges"], "bytes")
        for value in ("bytes=9999999-", "bytes=40-20", "bytes=-0",
                      "bytes=0-1,3-4", "bogus=0-10"):
            with self.subTest(malformed=value):
                status, headers, body = self.request("GET", endpoint,
                                                    headers={"Range": value})
                self.assertEqual(status, 416)
                self.assertEqual(body, b"")
                self.assertEqual(headers["Content-Range"],
                                 f"bytes */{len(video)}")
        # Non-MP4 artifacts retain their ordinary, non-range download contract.
        status, headers, body = self.request("GET", f"/api/jobs/{job_id}/video.srt",
                                            headers={"Range": "bytes=0-1"})
        self.assertEqual(status, 200)
        self.assertIn(b"Test", body)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertNotIn("Content-Range", headers)
        for auth in ({"Authorization": "Bearer wrong"},
                     {"Origin": "https://untrusted.example"}):
            status, _, response = self.request("GET", endpoint,
                                               headers={**auth, "Range": "bytes=0-5"})
            self.assertIn(status, (401, 403))
            self.assertNotIn(b"ftyp", response)
        with self.jobs.lock:
            self.jobs.jobs[job_id]["status"] = "running"
        self.assertEqual(self.request("GET", endpoint,
                                      headers={"Range": "bytes=0-0"})[0], 404)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg and FFprobe required")
    def test_real_mp4_range_stream_supports_remote_decoder_seeks(self):
        """Verify HTTP 206 delivery can be decoded and sought by real FFmpeg."""
        job_id = "f" * 32
        folder = self.jobs.output_dir / job_id
        folder.mkdir()
        video = folder / "video.mp4"
        command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-f", "lavfi", "-i", "testsrc2=s=180x320:r=12:d=5",
                   "-an", "-c:v", "libx264", "-preset", "ultrafast",
                   "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(video)]
        result = subprocess.run(command, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        original = video.read_bytes()
        with self.jobs.lock:
            self.jobs.jobs[job_id] = {"status": "done"}
        url = f"http://127.0.0.1:{self.port}/api/jobs/{job_id}/video.mp4"
        headers = "Authorization: Bearer a-safe-secret-for-tests\r\n"
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-headers", headers, "-ss", "3", "-i", url,
            "-frames:v", "1", "-vf", "scale=18:32", "-pix_fmt", "gray",
            "-f", "rawvideo", "pipe:1"], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-1200:])
        self.assertEqual(len(result.stdout), 18 * 32)
        self.assertEqual(video.read_bytes(), original)

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
