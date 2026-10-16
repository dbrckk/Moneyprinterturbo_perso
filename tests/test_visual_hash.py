"""Image difference-hash tests, including genuine FFmpeg JPEG re-encoding."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from visual_hash import difference_hash, near_duplicate, visual_signature


class DifferenceHashTests(unittest.TestCase):
    def test_hash_identical_grayscale(self):
        gray = bytes((x*25 + (y%2)*3) % 256 for y in range(8) for x in range(9))
        value = difference_hash(gray)
        self.assertIsNotNone(value)
        self.assertEqual(value, difference_hash(gray))
        self.assertTrue(near_duplicate(value, value))
        self.assertFalse(near_duplicate(value, None))

    def test_flat_and_invalid_data_never_generate_false_duplicates(self):
        self.assertIsNone(difference_hash(bytes(72)))
        self.assertIsNone(difference_hash(bytes([128]*72)))
        self.assertIsNone(difference_hash(bytes(71)))
        self.assertFalse(near_duplicate(None, None))
        self.assertFalse(near_duplicate(-1, 1))
        with self.assertRaises(ValueError):
            near_duplicate(1, 2, distance=65)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_same_image_different_jpeg_quality_is_recognized(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/"source.png"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=s=288x180:r=1",
                "-frames:v", "1", "-update", "1", str(source)],
                capture_output=True, check=True, timeout=30)
            pictures=[]
            for quality in (2, 8):
                target=root/f"quality{quality}.jpg"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-q:v", str(quality),
                    "-frames:v", "1", "-update", "1", str(target)],
                    capture_output=True, check=True, timeout=30)
                pictures.append(visual_signature(target))
            self.assertTrue(all(value is not None for value in pictures))
            self.assertTrue(near_duplicate(*pictures))
            corrupted=root/"invalid.jpg"
            corrupted.write_bytes(b"not an image")
            self.assertIsNone(visual_signature(corrupted))


if __name__ == "__main__":
    unittest.main()
