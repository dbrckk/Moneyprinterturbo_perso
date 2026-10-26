"""Media batch APIs never traverse symlinked user directories."""
import io
from pathlib import Path
import tempfile
import unittest
from studio_server import Jobs


class MediaBatchSymlinkGuardTests(unittest.TestCase):
    def test_symlink_batch_cannot_be_read_uploaded_or_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(Path(tmp) / 'out')
            outside = Path(tmp) / 'user-files'
            outside.mkdir()
            original = outside / 'personal.mp4'
            original.write_bytes(b'private video')
            ident = 'a' * 32
            jobs.upload_root.mkdir(parents=True, exist_ok=True)
            (jobs.upload_root / ident).symlink_to(outside, target_is_directory=True)
            self.assertIsNone(jobs.batch_info(ident))
            with self.assertRaisesRegex(ValueError, 'Lot absent'):
                jobs.save_media(ident, 'new.mp4', 8, io.BytesIO(b'anything'))
            self.assertFalse(jobs.delete_media_batch(ident))
            self.assertEqual(original.read_bytes(), b'private video')
            self.assertFalse((outside / 'new.mp4').exists())

    def test_regular_batch_still_visible_and_deletable(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Jobs(Path(tmp) / 'out')
            ident = jobs.create_media_batch()
            self.assertEqual(jobs.batch_info(ident)['id'], ident)
            self.assertTrue(jobs.delete_media_batch(ident))

if __name__ == '__main__':
    unittest.main()
