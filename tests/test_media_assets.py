"""Real FFmpeg media regression tests; remote Wikimedia calls are stubbed."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import free_studio as studio
import media_assets as media


FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_image(target: Path) -> None:
    studio.execute(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=s=640x960:r=1", "-frames:v", "1", "-update", "1", str(target)])


class MediaAssetsTests(unittest.TestCase):
    def test_untrusted_hosts_and_redirects_are_blocked(self):
        allowed = "https://upload.wikimedia.org/wikipedia/commons/test.jpg"
        self.assertTrue(media._trusted_url(allowed))
        for url in (
            "http://upload.wikimedia.org/photo.jpg", "https://upload.wikimedia.org.evil.test/img.jpg",
            "https://root:pw@upload.wikimedia.org/img.jpg", "https://upload.wikimedia.org:9999/img.jpg",
            "file:///etc/passwd", "https://upload.wikimedia.org:bad/a.jpg",
        ):
            with self.subTest(url=url):
                self.assertFalse(media._trusted_url(url))
        self.assertFalse(media._trusted_url("https://commons.wikimedia.org/w/api.php"))
        self.assertTrue(media._trusted_url(media.API, api=True))

    @unittest.skipUnless(FFMPEG, "FFmpeg required")
    def test_local_media_validation_and_symlinks(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            make_image(folder / "proper.png")
            (folder / "wrong.jpg").write_text("not an image")
            (folder / "link.jpg").symlink_to(folder / "proper.png")
            sources, warnings = media.local_assets(folder)
            self.assertEqual([item.title for item in sources], ["proper.png"])
            self.assertEqual(len(warnings), 1)
            self.assertEqual(sources[0].license, "user-provided")

    def test_commons_rejects_unverified_license(self):
        data = {"query": {"pages": {"100": {
            "title": "File:LicenseNotFree.jpg", "index": 1,
            "imageinfo": [{"descriptionurl": "https://commons.wikimedia.org/wiki/File:LicenseNotFree.jpg",
                           "thumburl": "https://upload.wikimedia.org/test.jpg",
                           "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 4.0"}}}],
        }}}}
        with tempfile.TemporaryDirectory() as name:
            with patch.object(media, "_read_limited", return_value=json.dumps(data).encode()) as mocked:
                sources, warnings = media.commons_assets("wildlife", Path(name))
            self.assertEqual(sources, [])
            self.assertTrue(warnings)
            self.assertEqual(mocked.call_count, 1)

    @unittest.skipUnless(FFMPEG, "FFmpeg required")
    def test_commons_accepts_cc0_with_credits(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            image = folder / "fixture.png"
            make_image(image)
            info = {"query": {"pages": {"100": {
                "title": "File:Beautiful landscape.png", "index": 1,
                "imageinfo": [{"descriptionurl": "https://commons.wikimedia.org/wiki/File:Beautiful_landscape.png",
                               "thumburl": "https://upload.wikimedia.org/wikipedia/commons/1/11/Beautiful.png",
                               "extmetadata": {"LicenseShortName": {"value": "CC0"},
                                               "Artist": {"value": "<b>Tester</b>"}}}],
            }}}}
            with patch.object(media, "_read_limited", side_effect=[json.dumps(info).encode(), image.read_bytes()]):
                sources, warnings = media.commons_assets("landscape", folder)
            self.assertEqual(warnings, [])
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0].license, "CC0")
            self.assertEqual(sources[0].author, "Tester")
            self.assertTrue(sources[0].file.is_file())

    @unittest.skipUnless(FFMPEG, "FFmpeg required")
    def test_end_to_end_user_images_and_video_with_attribution(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            media_dir = folder / "media"
            media_dir.mkdir()
            make_image(media_dir / "01.png")
            studio.execute(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=s=640x960:r=12", "-frames:v", "12", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(media_dir / "02.mp4")])
            target = folder / "final.mp4"
            result = studio.render("Trip", "Watch the forest. Enjoy the river.", target,
                                   lang="en", resolution=360, fps=12, mute=True, media_dir=media_dir)
            self.assertEqual(result["visual_mode"], "media")
            self.assertEqual([v["kind"] for v in result["media"]], ["image", "video"])
            self.assertTrue(target.with_suffix(".credits.txt").exists())
            self.assertIn("user-provided", target.with_suffix(".credits.txt").read_text())
            self.assertEqual(len(json.loads(target.with_suffix(".json").read_text())["media"]), 2)
            self.assertAlmostEqual(studio.duration_seconds(target), result["duration_seconds"], delta=0.25)

    @unittest.skipUnless(FFMPEG, "FFmpeg required")
    def test_commons_network_failure_gracefully_renders_graphic(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / "fallback.mp4"
            with patch.object(studio, "commons_assets", return_value=([], ["Network unavailable"])):
                result = studio.render("Forest", "Bonjour depuis la forêt.", target,
                                       resolution=360, fps=12, mute=True, commons=True)
            self.assertEqual(result["visual_mode"], "abstract-fallback")
            self.assertIn("Network unavailable", result["warnings"])
            self.assertGreater(target.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
