"""Only abandoned private render scratch is reclaimed after Android kills Termux."""
from pathlib import Path
import tempfile
import unittest

from render_progress import PROGRESS_NAME
from studio_server import Jobs


class AndroidOrphanCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "output"
        self.jobs = Jobs(self.root)
        self.ident = "e" * 32
        self.jobdir = self.root / self.ident
        self.job = {"id": self.ident, "created": 100, "finished": None,
                    "status": "running", "error": None,
                    "parameters": {"topic": "Interruption", "resolution": 360,
                                   "media_batch": ""}, "metadata": None}
        self.jobs._checkpoint(self.job)

    def test_restart_reclaims_only_private_scratch_and_progress(self):
        scratch = self.jobdir / ".render-scratch"
        scratch.mkdir()
        (scratch / "unbounded-orphan.tmp").write_bytes(b"X" * 8000)
        (self.jobdir / PROGRESS_NAME).write_text('{"stage":"encode"}')
        original = self.jobdir / "script.txt"
        original.write_text("Private original")
        video = self.jobdir / "video.mp4"
        video.write_bytes(b"partial but preserved")
        batch = self.root / "_uploaded_media" / ("a" * 32)
        batch.mkdir(parents=True)
        source = batch / "phone.mp4"
        source.write_bytes(b"original footage")
        restored = Jobs(self.root)
        self.assertEqual(restored.get(self.ident)["status"], "error")
        self.assertFalse(scratch.exists())
        self.assertFalse((self.jobdir / PROGRESS_NAME).exists())
        self.assertEqual(original.read_text(), "Private original")
        self.assertEqual(video.read_bytes(), b"partial but preserved")
        self.assertEqual(source.read_bytes(), b"original footage")
        self.assertEqual(Jobs(self.root).get(self.ident)["status"], "error")

    def test_symlinked_scratch_does_not_delete_external_user_files(self):
        external = Path(self.temp.name) / "user-files"
        external.mkdir()
        user_file = external / "original.mp4"
        user_file.write_bytes(b"never remove")
        scratch = self.jobdir / ".render-scratch"
        scratch.symlink_to(external, target_is_directory=True)
        restored = Jobs(self.root)
        self.assertEqual(restored.get(self.ident)["status"], "error")
        self.assertTrue(scratch.is_symlink())
        self.assertEqual(user_file.read_bytes(), b"never remove")

    def test_queued_orphan_is_cleaned_without_starting_render(self):
        self.job["status"] = "queued"
        self.jobs._checkpoint(self.job)
        scratch = self.jobdir / ".render-scratch"
        scratch.mkdir()
        (scratch / "unneeded.tmp").write_bytes(b"x" * 100)
        recovered = Jobs(self.root)
        self.assertEqual(recovered.get(self.ident)["status"], "error")
        self.assertFalse(scratch.exists())
        self.assertFalse(recovered.running)


if __name__ == "__main__":
    unittest.main()
