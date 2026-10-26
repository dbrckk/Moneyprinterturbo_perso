"""Regression coverage for Android batch replacement during render admission."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio_server import Jobs, validate_draft


class MediaBatchRenderSymlinkGuardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.jobs = Jobs(self.root / "outputs")
        self.ident = self.jobs.create_media_batch()
        self.batch = self.jobs.upload_root / self.ident
        self.batch.joinpath("001_source.mp4").write_bytes(b"source video")
        self.outside = self.root / "private"
        self.outside.mkdir()
        self.outside.joinpath("personal.mp4").write_bytes(b"do not touch")

    def draft(self):
        return {"topic": "Un montage local", "script": "Une premiere scene a monter.",
                "visuals": "local", "media_batch": self.ident}

    def replace_batch_with_symlink(self):
        self.batch.rename(self.root / "moved-original")
        self.batch.symlink_to(self.outside, target_is_directory=True)

    def test_submit_rejects_symlink_without_sealing_private_folder(self):
        self.replace_batch_with_symlink()
        with self.assertRaisesRegex(ValueError, "Importez au moins un média"):
            self.jobs.submit(self.draft())
        self.assertFalse((self.outside / ".sealed").exists())
        self.assertFalse(self.jobs.jobs)

    def test_submit_rechecks_after_checkpoint_before_sealing(self):
        original_checkpoint = self.jobs._checkpoint

        def swap_during_checkpoint(job):
            original_checkpoint(job)
            self.replace_batch_with_symlink()

        with patch.object(self.jobs, "_checkpoint", side_effect=swap_during_checkpoint):
            with self.assertRaisesRegex(ValueError, "Lot de médias modifié"):
                self.jobs.submit(self.draft())
        self.assertFalse((self.outside / ".sealed").exists())
        self.assertFalse(self.jobs.jobs)
        self.assertEqual(list(self.jobs.output_dir.glob("*/.job_state.json")), [])

    def test_submit_rejects_symlinked_seal_marker(self):
        private_marker = self.outside / "marker"
        private_marker.write_bytes(b"keep")
        (self.batch / ".sealed").symlink_to(private_marker)
        with self.assertRaises(OSError):
            self.jobs.submit(self.draft())
        self.assertEqual(private_marker.read_bytes(), b"keep")
        self.assertFalse(self.jobs.jobs)
        self.assertEqual(list(self.jobs.output_dir.glob("*/.job_state.json")), [])

    def test_queued_render_command_rejects_replaced_sources(self):
        params = validate_draft(self.draft())
        (self.batch / ".sealed").touch()
        job = {"id": "f" * 32, "parameters": params}
        self.assertIn("--media-dir", self.jobs._arguments(job, self.root / "dest"))
        self.replace_batch_with_symlink()
        with self.assertRaisesRegex(ValueError, "Sources du rendu modifiées"):
            self.jobs._arguments(job, self.root / "dest")

    def test_upload_rejects_symlink_swap_during_media_probe(self):
        def swap_after_upload(_path):
            self.replace_batch_with_symlink()
            return 320, 240

        # A local process changing a batch while ffprobe runs must never
        # receive a newly published file through the substituted symlink.
        with patch("studio_server.media_dimensions", side_effect=swap_after_upload):
            with self.assertRaisesRegex(ValueError, "Lot supprimé ou déjà utilisé"):
                self.jobs.save_media(self.ident, "new.mp4", 8, io.BytesIO(b"video123"))
        self.assertFalse((self.outside / "002_new.mp4").exists())
        self.assertEqual((self.outside / "personal.mp4").read_bytes(), b"do not touch")


if __name__ == "__main__":
    unittest.main()
