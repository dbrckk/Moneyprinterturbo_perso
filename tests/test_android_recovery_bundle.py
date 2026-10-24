"""Android kill between atomic video publish and job checkpoint."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio_server import Jobs


class AndroidPublishRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "render"
        self.jobs = Jobs(self.root)
        self.ident = "d" * 32
        self.directory = self.root / self.ident
        self.state = {"id": self.ident, "created": 100, "finished": None,
                      "status": "running", "error": None,
                      "parameters": {"topic": "Android", "resolution": 360, "media_batch": ""},
                      "metadata": None}
        self.jobs._checkpoint(self.state)

    def make_bundle(self):
        self.directory.joinpath("video.json").write_text(json.dumps({
            "segments": ["Bonjour"], "duration_seconds": 1.2, "visual_mode": "abstract-fallback"}))
        self.directory.joinpath("video.srt").write_text("1\\n00:00:00,000 --> 00:00:01,000\\nBonjour\\n")
        self.directory.joinpath("video.credits.txt").write_text("Original artwork\\n")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=blue:s=180x320:r=12:d=1.2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(self.directory / "video.mp4")],
                       check=True, timeout=30)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_complete_atomic_bundle_is_recovered_without_rerender(self):
        self.make_bundle()
        with patch.object(Jobs, "submit", side_effect=AssertionError("must not restart")):
            recovered = Jobs(self.root)
        status = recovered.get(self.ident)
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["duration_seconds"], 1.2)
        self.assertIsNone(status["error"])
        self.assertIsNotNone(recovered.artifact(self.ident, "video.mp4"))
        self.assertEqual(Jobs(self.root).get(self.ident)["status"], "done")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_missing_part_or_truncated_mp4_remains_interrupted(self):
        self.make_bundle()
        for name in ("video.json", "video.srt", "video.credits.txt", "video.mp4"):
            path = self.directory / name
            original = path.read_bytes()
            path.unlink()
            recovered = Jobs(self.root)
            self.assertEqual(recovered.get(self.ident)["status"], "error", name)
            self.assertIsNone(recovered.artifact(self.ident, "video.mp4"))
            path.write_bytes(original)
            self.jobs._checkpoint(self.state)
        path = self.directory / "video.mp4"
        path.write_bytes(b"invalid" * 500)
        recovered = Jobs(self.root)
        self.assertEqual(recovered.get(self.ident)["status"], "error")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_untrusted_metadata_or_symlinks_cannot_promote_job(self):
        self.make_bundle()
        path = self.directory / "video.json"
        path.write_text(json.dumps({"segments": "not-a-list", "duration_seconds": 1.2}))
        self.assertEqual(Jobs(self.root).get(self.ident)["status"], "error")
        path.write_text(json.dumps({"segments": [], "duration_seconds": 1.2}))
        self.jobs._checkpoint(self.state)
        source = self.directory / "private.mp4"
        (self.directory / "video.mp4").rename(source)
        (self.directory / "video.mp4").symlink_to(source)
        self.assertEqual(Jobs(self.root).get(self.ident)["status"], "error")
        self.assertTrue(source.is_file())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_queued_jobs_never_recovered_from_untrusted_artifacts(self):
        self.make_bundle()
        self.state["status"] = "queued"
        self.jobs._checkpoint(self.state)
        self.assertEqual(Jobs(self.root).get(self.ident)["status"], "error")


if __name__ == "__main__":
    unittest.main()
