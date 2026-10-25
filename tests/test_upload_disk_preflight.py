"""Reject oversized phone imports before consuming storage or source bytes."""
from collections import namedtuple
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from studio_server import Jobs, UPLOAD_MIN_REMAINING_BYTES

Usage = namedtuple('Usage', 'total used free')


class UploadDiskPreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.jobs = Jobs(Path(temp.name) / 'out')
        self.batch = self.jobs.create_media_batch()

    def test_low_disk_rejects_before_reading_stream_or_creating_partial(self):
        directory = self.jobs.upload_root / self.batch
        source = directory / '001_existing.mp4'
        source.write_bytes(b'keep original')
        with patch('studio_server.shutil.disk_usage', return_value=Usage(1000, 900, 100)):
            with self.assertRaisesRegex(ValueError, 'Espace insuffisant'):
                self.jobs.save_media(self.batch, 'next.mp4', 1024, io.BytesIO(b'ignored'))
        self.assertEqual(source.read_bytes(), b'keep original')
        self.assertFalse(list(directory.glob('.receiving-*')))
        self.assertFalse(self.jobs._uploads_in_progress)

    def test_exact_threshold_accepts_without_false_reservation_double_count(self):
        size = 1000
        with patch('studio_server.shutil.disk_usage', return_value=Usage(2**32, 0, size + UPLOAD_MIN_REMAINING_BYTES)), \
             patch('studio_server.media_dimensions', return_value=(320, 240)):
            result = self.jobs.save_media(self.batch, 'phone.mp4', size, io.BytesIO(b'a' * size))
        self.assertEqual(result['bytes'], size)

    def test_other_uploads_unwritten_bytes_are_reserved(self):
        existing = self.jobs.upload_root / self.batch / '.receiving-existing'
        existing.write_bytes(b'12345')
        self.jobs._uploads_in_progress['f' * 32] = (200, existing)
        with patch('studio_server.shutil.disk_usage', return_value=Usage(2**32, 0, 100 + UPLOAD_MIN_REMAINING_BYTES)):
            with self.assertRaisesRegex(ValueError, 'Espace insuffisant'):
                self.jobs.save_media(self.batch, 'next.mp4', 100, io.BytesIO(b'x' * 100))
        self.assertEqual(existing.read_bytes(), b'12345')
        self.assertNotIn(self.batch, self.jobs._uploads_in_progress)

    def test_disk_pressure_during_upload_removes_only_partial_file(self):
        size = 5 * 1024 * 1024
        folder = self.jobs.upload_root / self.batch
        original = folder / '001_original.mp4'
        original.write_bytes(b'preserve this')
        free = size + UPLOAD_MIN_REMAINING_BYTES + 1024
        calls = [Usage(2**32, 0, free),
                 Usage(2**32, 0, UPLOAD_MIN_REMAINING_BYTES - 1)]
        with patch('studio_server.shutil.disk_usage', side_effect=calls):
            with self.assertRaisesRegex(ValueError, 'Stockage presque plein'):
                self.jobs.save_media(self.batch, 'large.mp4', size, io.BytesIO(b'x' * size))
        self.assertEqual(original.read_bytes(), b'preserve this')
        self.assertFalse(list(folder.glob('.receiving-*')))
        self.assertFalse(self.jobs._uploads_in_progress)

    def test_disk_probe_failure_during_upload_reclaims_partial(self):
        size = 5 * 1024 * 1024
        with patch('studio_server.shutil.disk_usage', side_effect=[
                Usage(2**32, 0, size + UPLOAD_MIN_REMAINING_BYTES), OSError('storage gone')]):
            with self.assertRaisesRegex(ValueError, 'pendant l.import'):
                self.jobs.save_media(self.batch, 'large.mp4', size, io.BytesIO(b'x' * size))
        self.assertFalse(list((self.jobs.upload_root / self.batch).glob('.receiving-*')))

    def test_unavailable_disk_space_is_safe_rejection(self):
        with patch('studio_server.shutil.disk_usage', side_effect=OSError('statvfs')):
            with self.assertRaisesRegex(ValueError, 'impossible à vérifier'):
                self.jobs.save_media(self.batch, 'phone.mp4', 100, io.BytesIO(b'x' * 100))
        self.assertFalse(self.jobs._uploads_in_progress)


if __name__ == '__main__':
    unittest.main()
