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
            with patch.object(studio, "commons_scene_assets", return_value=({}, ["Network unavailable"])):
                result = studio.render("Forest", "Bonjour depuis la forêt.", target,
                                       resolution=360, fps=12, mute=True, commons=True)
            self.assertEqual(result["visual_mode"], "abstract-fallback")
            self.assertIn("Network unavailable", result["warnings"])
            self.assertGreater(target.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()

class SceneSearchTests(unittest.TestCase):
    def test_one_query_per_scene_and_overrides(self):
        segments = ["Watch the forest.", "Travel beside the river."]
        self.assertEqual(media.scene_search_queries("nature", segments), ["forest", "travel beside river"])
        self.assertEqual(media.scene_search_queries("nature", segments, ["red fox", "blue river"]), ["red fox", "blue river"])
        with self.assertRaises(ValueError):
            media.scene_search_queries("nature", segments, ["only one"])
        with self.assertRaises(ValueError):
            media.scene_search_queries("nature", segments, ["red fox", "  "])

    def test_commons_per_scene_cache_and_search_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            result = media.MediaAsset(work / "picture.jpg", "image", "https://commons.wikimedia.org/wiki/File:picture.jpg", "CC0", "Artist", "picture.jpg")
            with patch.object(media, "commons_assets", return_value=([result], [])) as search:
                found, warnings = media.commons_scene_assets(["forest", "forest", "river", "sea"], work, max_searches=2)
            self.assertEqual(sorted(found), [0, 1, 2])
            self.assertEqual(search.call_count, 2)
            self.assertTrue(any("budget" in warning for warning in warnings))
            self.assertEqual({call.args[0] for call in search.call_args_list}, {"forest", "river"})

    @unittest.skipUnless(FFMPEG, "FFmpeg required")
    def test_mixed_scenes_keep_correct_attribution_and_srt(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            photo = work / "forest.png"
            make_image(photo)
            asset = media.MediaAsset(photo, "image", "https://commons.wikimedia.org/wiki/File:forest.png", "CC0", "Artist", "Forest")
            target = work / "mix.mp4"
            with patch.object(studio, "commons_scene_assets", return_value=({1: asset}, ["Scene 1: unavailable"])) as search:
                info = studio.render("landscape", "Trees in the forest. Rivers in the valley.", target,
                                     lang="en", resolution=360, fps=12, mute=True, commons=True,
                                     scene_queries=["forest trees", "river valley"])
            self.assertEqual(info["visual_mode"], "mixed-media")
            self.assertEqual([item["segment"] for item in info["media"]], [2])
            self.assertEqual(info["media"][0]["search_query"], "river valley")
            self.assertTrue(target.with_suffix(".srt").exists())
            self.assertEqual(target.with_suffix(".srt").read_text(encoding="utf-8").count(" --> "), 2)
            self.assertIn("Scene 2:", target.with_suffix(".credits.txt").read_text())
            self.assertEqual(search.call_args.kwargs["indices"], [0, 1])
            self.assertAlmostEqual(studio.duration_seconds(target), info["duration_seconds"], delta=0.3)

class CommonsRelevanceTests(unittest.TestCase):
    def test_text_scoring_prefers_accurate_title_and_ignores_accents(self):
        self.assertGreater(media.relevance_score('forêt montagne','File:Forêt montagne.jpg'),
                           media.relevance_score('forêt montagne','File:Forest.jpg','Une montagne'))
        self.assertEqual(media.relevance_score('forêt montagne','File:Car showroom.jpg','Voiture électrique'),0)
        self.assertGreater(media.relevance_score('énergie solaire','File:Energie solaire.jpg'),0)

    @unittest.skipUnless(FFMPEG, 'FFmpeg required')
    def test_commons_prefers_relevant_photo_over_top_unrelated_result(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            img=root/'demo.png';make_image(img)
            def item(title,index):
                return {'title':title,'index':index,'imageinfo':[{'descriptionurl':
                    'https://commons.wikimedia.org/wiki/'+title,
                    'thumburl':'https://upload.wikimedia.org/test.png',
                    'extmetadata':{'LicenseShortName':{'value':'CC0'}}}]}
            data={'query':{'pages':{'1':item('File:Car.png',1),'2':item('File:Montagne_neige.png',2)}}}
            with patch.object(media,'_read_limited',side_effect=[json.dumps(data).encode(),img.read_bytes()]) as request:
                found,warnings=media.commons_assets('montagne neige',root,limit=1)
            self.assertEqual(len(found),1)
            self.assertEqual(found[0].title,'File:Montagne_neige.png')
            self.assertEqual(request.call_count,2)
            self.assertEqual(warnings,[])

    def test_commons_explicit_fallback_when_licence_ok_but_no_match(self):
        candidate={'query':{'pages':{'1':{'title':'File:Car.png','index':1,
          'imageinfo':[{'descriptionurl':'https://commons.wikimedia.org/wiki/File:Car.png',
          'thumburl':'https://upload.wikimedia.org/test.png',
          'extmetadata':{'LicenseShortName':{'value':'CC0'}}}]}}}}
        with tempfile.TemporaryDirectory() as name:
            with patch.object(media,'_read_limited',return_value=json.dumps(candidate).encode()) as request:
                selected,warnings=media.commons_assets('mountain glacier',Path(name))
            self.assertEqual(selected,[])
            self.assertTrue(any('relevant' in w for w in warnings))
            self.assertEqual(request.call_count,1)
