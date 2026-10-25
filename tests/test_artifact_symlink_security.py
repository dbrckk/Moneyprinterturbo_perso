"""Export download boundary: never read a redirected Termux private file."""
from __future__ import annotations

from http.client import HTTPConnection
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from studio_server import Jobs, StudioHandler, StudioServer


class ArtifactDownloadSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jobs = Jobs(self.root / "out")
        self.ident = "a" * 32
        self.folder = self.jobs.output_dir / self.ident
        self.folder.mkdir()
        self.video = self.folder / "video.mp4"
        self.video.write_bytes(b"public-MP4" * 350)
        self.subtitle = self.folder / "video.srt"
        self.subtitle.write_bytes(b"public subtitles\n")
        self.secret = self.root / "private-termux-token.txt"
        self.secret.write_bytes(b"SECRET_NOT_FOR_BROWSER" * 100)
        with self.jobs.lock:
            self.jobs.jobs[self.ident] = {"status": "done"}
        self.server = StudioServer(("127.0.0.1", 0), self.jobs,
                                   auth_token="local-test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_server)

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=4)

    def get(self, filename, range_value=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = {"Authorization": "Bearer local-test-token"}
        if range_value is not None:
            headers["Range"] = range_value
        conn.request("GET", f"/api/jobs/{self.ident}/{filename}", headers=headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_symlinked_mp4_and_subtitle_are_not_served(self):
        for name in ("video.mp4", "video.srt", "video.json", "video.credits.txt",
                     "video.publish.txt"):
            with self.subTest(name=name):
                path = self.folder / name
                path.unlink(missing_ok=True)
                path.symlink_to(self.secret)
                self.assertIsNone(self.jobs.artifact(self.ident, name))
                code, _, body = self.get(name, "bytes=0-12")
                self.assertEqual(code, 404)
                self.assertNotIn(b"SECRET_NOT_FOR_BROWSER", body)
                path.unlink()
        self.assertEqual(self.secret.read_bytes(), b"SECRET_NOT_FOR_BROWSER" * 100)

    def test_job_folder_symlink_to_external_folder_rejected(self):
        self.folder.rename(self.root / "saved-original-job")
        external = self.root / "external-job"
        external.mkdir()
        (external / "video.mp4").write_bytes(b"SECRET_NOT_FOR_BROWSER")
        self.folder.symlink_to(external, target_is_directory=True)
        self.assertIsNone(self.jobs.artifact(self.ident, "video.mp4"))
        status, _, data = self.get("video.mp4")
        self.assertEqual(status, 404)
        self.assertNotIn(b"SECRET_NOT_FOR_BROWSER", data)

    def test_link_swap_between_lookup_and_open_rejected(self):
        original = self.jobs.artifact

        def swapped(ident, name):
            path = original(ident, name)
            self.assertIsNotNone(path)
            path.unlink()
            path.symlink_to(self.secret)
            return path

        with patch.object(self.jobs, "artifact", side_effect=swapped):
            status, _, data = self.get("video.mp4", "bytes=0-12")
        self.assertEqual(status, 404)
        self.assertNotIn(b"SECRET_NOT_FOR_BROWSER", data)

    def test_open_descriptor_remains_original_after_atomic_replacement(self):
        with StudioHandler._open_artifact_no_follow(self.video) as stream:
            replacement = self.folder / "candidate.tmp"
            replacement.write_bytes(b"changed" * 600)
            os.replace(replacement, self.video)
            self.assertEqual(stream.read(), b"public-MP4" * 350)
        self.assertEqual(self.video.read_bytes(), b"changed" * 600)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO required")
    def test_fifo_swap_does_not_block_or_get_served(self):
        original = self.jobs.artifact

        def swapped(ident, name):
            path = original(ident, name)
            path.unlink()
            os.mkfifo(path)
            return path

        with patch.object(self.jobs, "artifact", side_effect=swapped):
            status, _, data = self.get("video.mp4")
        self.assertEqual(status, 404)
        self.assertNotIn(b"SECRET_NOT_FOR_BROWSER", data)

    def test_normal_and_range_downloads_remain_byte_exact(self):
        code, headers, data = self.get("video.mp4", "bytes=13-250")
        self.assertEqual(code, 206)
        self.assertEqual(data, (b"public-MP4" * 350)[13:251])
        self.assertEqual(int(headers["Content-Length"]), len(data))
        code, headers, data = self.get("video.srt", "bytes=0-2")
        self.assertEqual(code, 200)
        self.assertEqual(data, b"public subtitles\n")
        self.assertEqual(int(headers["Content-Length"]), len(data))


if __name__ == "__main__":
    unittest.main()
