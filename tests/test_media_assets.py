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

    def test_cross_host_redirect_blocked_before_any_followup_request(self):
        from urllib import request
        image = request.Request("https://upload.wikimedia.org/wikipedia/commons/test.jpg")
        api = request.Request("https://commons.wikimedia.org/w/api.php")
        media_redirects = media._TrustedRedirectHandler(api=False)
        api_redirects = media._TrustedRedirectHandler(api=True)
        for target in (
            "https://example.org/collect", "http://upload.wikimedia.org/file.jpg",
            "https://upload.wikimedia.org.evil.test/file.jpg",
            "file:///etc/passwd", "https://commons.wikimedia.org/w/api.php",
            "https://upload.wikimedia.org:8443/file.jpg",
        ):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "Cross-host redirect"):
                    media_redirects.redirect_request(image, None, 302, "Found", {}, target)
        with self.assertRaises(ValueError):
            api_redirects.redirect_request(api, None, 302, "Found", {},
                "https://upload.wikimedia.org/test.jpg")
        approved = media_redirects.redirect_request(image, None, 302, "Found", {},
            "https://upload.wikimedia.org/wikipedia/commons/other.jpg")
        self.assertEqual(approved.full_url,
            "https://upload.wikimedia.org/wikipedia/commons/other.jpg")
        api_approved = api_redirects.redirect_request(api, None, 302, "Found", {},
            "https://commons.wikimedia.org/w/api.php?query=ok")
        self.assertIn("query=ok", api_approved.full_url)

    def test_limited_download_installs_a_redirect_guard(self):
        from unittest.mock import MagicMock
        opener = MagicMock()
        response = MagicMock()
        response.geturl.return_value = "https://upload.wikimedia.org/media.jpg"
        response.headers = {}
        response.read.return_value = b"image"
        opener.open.return_value.__enter__.return_value = response
        with patch.object(media.request, "build_opener", return_value=opener) as factory:
            result = media._read_limited(
                "https://upload.wikimedia.org/media.jpg", 20, timeout=3)
        self.assertEqual(result, b"image")
        self.assertIsInstance(factory.call_args.args[0], media._TrustedRedirectHandler)
        self.assertFalse(factory.call_args.args[0].api)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 3)

    def test_malformed_commons_page_records_are_skipped(self):
        payload = {"query": {"pages": {
            "0": None,
            "1": {"title": "File:Bad.jpg", "imageinfo": [None]},
            "2": {"title": "File:Wrong.jpg", "imageinfo": {"value": "broken"}},
            "3": {"title": "File:Unknown.jpg", "imageinfo": [{
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Unknown.jpg",
                "thumburl": "https://upload.wikimedia.org/unknown.jpg",
                "extmetadata": []
            }]}
        }}}
        with tempfile.TemporaryDirectory() as name, patch.object(
                media, "_read_limited", return_value=json.dumps(payload).encode()):
            assets, notices = media.commons_assets("landscape", Path(name))
        self.assertEqual(assets, [])
        self.assertTrue(any("No relevant" in notice for notice in notices))

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

    def test_bilingual_visual_concepts_are_bounded_and_explainable(self):
        evidence = media.lexical_evidence(
            "forêt neige", "File:Forest snow landscape.jpg")
        self.assertEqual(evidence["query_terms"], 2)
        self.assertEqual(evidence["matched_terms"], 2)
        self.assertEqual(evidence["coverage"], 1.0)
        self.assertEqual(evidence["score"], 6)
        self.assertGreater(media.relevance_score("rivière", "File:River.jpg"), 0)
        self.assertEqual(media.relevance_score(
            "batterie électrique", "File:Electric battery.jpg"), 0)
        self.assertEqual(media.lexical_evidence(
            "forêt montagne château", "File:Forest.jpg")["coverage"], 0.333)

    def test_commons_keeps_bilingual_evidence_with_author_and_license(self):
        page = {"query": {"pages": {"1": {
            "title": "File:Forest snow panorama.jpg", "index": 1,
            "imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/test.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Forest_snow_panorama.jpg",
                "extmetadata": {"LicenseShortName": {"value": "CC0"},
                                "Artist": {"value": "Author"}}
            }]
        }}}}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(media, "_read_limited", side_effect=[
                json.dumps(page).encode(), b"fake-image"
            ]), patch.object(media, "media_dimensions", return_value=(900, 1600)):
                selected, warnings = media.commons_assets(
                    "forêt neige", Path(folder), limit=1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(warnings, [])
        self.assertEqual(selected[0].query_term_count, 2)
        self.assertEqual(selected[0].query_term_coverage, 1.0)
        self.assertEqual(selected[0].metadata()["query_term_coverage"], 1.0)
        self.assertEqual(selected[0].license, "CC0")

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


class ScenicCandidateSafetyTests(unittest.TestCase):
    def test_ambiguous_landscape_nouns_cannot_match_unrelated_people(self):
        self.assertEqual(media.safe_scenic_score("montagne",
            "File:Camille Montagne botaniste (cropped).png", "Portrait d'un botaniste"), 0)
        self.assertEqual(media.safe_scenic_score("rivière",
            "File:Henri.riviere.jpg", "Henri Rivière French painter"), 0)
        self.assertEqual(media.safe_scenic_score("océan",
            "File:Ocean-Morel-Fatio.jpg", "A painting of an ocean"), 0)

    def test_real_landscape_context_remains_eligible(self):
        self.assertGreater(media.safe_scenic_score("montagne",
            "File:Panorama montagne alps.jpg", "A mountain summit view"), 0)
        self.assertGreater(media.safe_scenic_score("rivière",
            "File:River landscape photographed.jpg", "Rivière au bord des arbres"), 0)
        self.assertGreater(media.safe_scenic_score("océan",
            "File:Ocean coastal seascape.jpg", "Côte et plage"), 0)

    def test_two_word_landscape_query_rejects_surname_false_positive(self):
        self.assertEqual(media.safe_scenic_score("rivière paysage",
            "File:Henri Rivière.jpg", "Portrait du peintre"), 0)
        self.assertEqual(media.safe_scenic_score("montagne panorama",
            "File:Camille Montagne.jpg", "Biography of a botanist"), 0)
        self.assertGreater(media.safe_scenic_score("rivière paysage",
            "File:Rivière panorama.jpg", "Landscape with trees and water"), 0)
        # A named person is still a valid explicit subject.
        self.assertGreater(media.safe_scenic_score("Henri Rivière",
            "File:Henri Rivière.jpg", "Portrait of a painter"), 0)
        # A non-scenic request has the original scoring semantics.
        self.assertGreater(media.safe_scenic_score("architecture paris",
            "File:Architecture Paris.jpg", "Old building"), 0)

    def test_explicit_person_query_is_not_blocked_by_landscape_filter(self):
        self.assertGreater(media.safe_scenic_score("Henri Rivière",
            "File:Henri.riviere.jpg", "Portrait"), 0)


class ContextualScenicSearchTests(unittest.TestCase):
    def test_ambiguous_single_word_gets_one_qualifier(self):
        self.assertEqual(media.expand_scenic_search_query("montagne"), "montagne panorama")
        self.assertEqual(media.expand_scenic_search_query("forêt"), "forêt paysage")
        self.assertEqual(media.expand_scenic_search_query("rivière"), "rivière paysage")
        self.assertEqual(media.expand_scenic_search_query("ocean"), "ocean paysage")
        self.assertEqual(media.expand_scenic_search_query("forest"), "forest landscape")
        self.assertEqual(media.expand_scenic_search_query("Henri Rivière"), "Henri Rivière")
        self.assertEqual(media.expand_scenic_search_query("rivière paysage"), "rivière paysage")

    def test_api_expands_but_original_query_is_used_for_relevance(self):
        from urllib import parse
        payload={"query":{"pages":{"1":{
            "title":"File:Camille Montagne botaniste.png", "index":1,
            "imageinfo":[{"descriptionurl":"https://commons.wikimedia.org/wiki/File:Camille_Montagne.png",
               "thumburl":"https://upload.wikimedia.org/test.png",
               "extmetadata":{"LicenseShortName":{"value":"CC0"},
                              "ImageDescription":{"value":"Portrait botaniste"}}}]}}}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(media,"_read_limited",return_value=json.dumps(payload).encode()) as read:
            assets,warnings=media.commons_assets("montagne",Path(tmp),limit=1)
        actual=parse.parse_qs(parse.urlsplit(read.call_args.args[0]).query)["gsrsearch"][0]
        self.assertEqual(actual,"montagne panorama")
        self.assertEqual(assets,[])
        self.assertTrue(warnings)
        self.assertEqual(read.call_count,1)



class WikimediaVisualMediumTests(unittest.TestCase):
    def test_medium_signals_are_conservative_and_artwork_takes_precedence(self):
        self.assertEqual(media.visual_medium_hint(
            "File:Landscape.jpg", "Photograph of a French river"), "photograph-likely")
        self.assertEqual(media.visual_medium_hint(
            "File:River landscape.png", "Oil painting on canvas"), "artwork-likely")
        self.assertEqual(media.visual_medium_hint(
            "File:River.jpg", "view of a river"), "unspecified")
        self.assertEqual(media.visual_medium_hint(
            "File:Photo of watercolor.jpg", "Photograph of a painted canvas"), "artwork-likely")
        self.assertEqual(media.visual_medium_hint(
            "File:River from 1918.jpg", "", "Landscape paintings"), "artwork-likely")

    def test_photograph_preferred_over_matching_painting_with_same_license(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def page(title, index, description):
                return {"title": title, "index": index, "imageinfo": [{
                    "thumburl": "https://upload.wikimedia.org/test.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:River_landscape.jpg",
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC0"},
                        "ImageDescription": {"value": description},
                        "Artist": {"value": "Example creator"}
                    }
                }]}
            payload = {"query": {"pages": {
                "1": page("File:River landscape painting.jpg", 1, "Oil painting of a river"),
                "2": page("File:River landscape photograph.jpg", 2, "Photograph of a river")
            }}}
            with patch.object(media, "_read_limited", side_effect=[
                     json.dumps(payload).encode(), b"fake-jpeg"]), \
                 patch.object(media, "media_dimensions", return_value=(1200, 800)):
                found, warnings = media.commons_assets("river landscape", root, limit=1)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].visual_medium, "photograph-likely")
            self.assertEqual(found[0].title, "File:River landscape photograph.jpg")
            self.assertEqual(warnings, [])

    def test_strong_scene_match_beats_weak_photo_and_better_crop(self):
        # The photo only matches "river"; the artwork depicts all three
        # requested concepts. The old media-first sort incorrectly chose it.
        def page(title, index, width, height):
            return {"title": title, "index": index, "imageinfo": [{
                "width": width, "height": height,
                "thumburl": "https://upload.wikimedia.org/sample.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:sample.jpg",
                "extmetadata": {"LicenseShortName": {"value": "CC0"}}
            }]}
        payload = {"query": {"pages": {
            "1": page("File:River photograph.jpg", 1, 900, 1600),
            "2": page("File:River gorge bridge painting.jpg", 2, 1600, 900)
        }}}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(media, "_read_limited", side_effect=[
                json.dumps(payload).encode(), b"fixture-image"
            ]) as read, patch.object(media, "media_dimensions", return_value=(1600, 900)):
                images, _ = media.commons_assets(
                    "river gorge bridge", Path(folder), limit=1,
                    target_aspect=9/16)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].title, "File:River gorge bridge painting.jpg")
        self.assertEqual(read.call_count, 2)

    def test_artwork_available_when_no_photo_and_provenance_retained(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = {"query": {"pages": {"1": {
                "title": "File:Landscape oil painting.jpg", "index": 1,
                "imageinfo": [{
                    "thumburl": "https://upload.wikimedia.org/test.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Landscape_oil_painting.jpg",
                    "extmetadata": {"LicenseShortName": {"value": "Public domain"}}
                }]
            }}}}
            with patch.object(media, "_read_limited", side_effect=[
                    json.dumps(info).encode(), b"fake-jpeg"]), \
                 patch.object(media, "media_dimensions", return_value=(800, 500)):
                found, warnings = media.commons_assets("landscape", root, limit=1)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].visual_medium, "artwork-likely")
            self.assertTrue(any("artwork" in w for w in warnings))
            self.assertEqual(found[0].license, "Public domain")
            self.assertEqual(found[0].metadata()["visual_medium"], "artwork-likely")



class WikimediaCategoryProvenanceTests(unittest.TestCase):
    def test_commons_categories_disambiguate_paintings(self):
        import json
        from urllib import parse
        def entry(title, index, categories):
            return {
                "title": title, "index": index,
                "categories": [{"ns": 14, "title": x} for x in categories],
                "imageinfo": [{
                    "thumburl": "https://upload.wikimedia.org/test.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Landscape.jpg",
                    "extmetadata": {"LicenseShortName": {"value": "Public domain"}}
                }]
            }
        payload={"query":{"pages":{
            "1": entry("File:River landscape old.jpg", 1,
                       ["Category:Paintings of rivers by JMW Turner"]),
            "2": entry("File:River landscape new.jpg", 2,
                       ["Category:Photographs of rivers"])
        }}}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(media, "_read_limited",
                              side_effect=[json.dumps(payload).encode(), b"fake-jpeg"]) as read, \
                 patch.object(media, "media_dimensions", return_value=(1024, 768)):
                images,warnings=media.commons_assets("river landscape",root,limit=1)
            params=parse.parse_qs(parse.urlsplit(read.call_args_list[0].args[0]).query)
            self.assertEqual(params["prop"][0],"imageinfo|categories")
            self.assertEqual(params["cllimit"][0],"max")
            self.assertEqual(len(images),1)
            self.assertEqual(images[0].title,"File:River landscape new.jpg")
            self.assertEqual(images[0].visual_medium,"photograph-likely")
            self.assertEqual(warnings,[])

    def test_silent_mixed_category_metadata_does_not_claim_photo(self):
        self.assertEqual(media.visual_medium_hint(
            "File:River.jpg","","Category:Bridges in Europe"),"unspecified")
        self.assertEqual(media.visual_medium_hint(
            "File:Turner landscape.jpg","","Category:Paintings of rivers"),
            "artwork-likely")
        self.assertEqual(media.visual_medium_hint(
            "File:Landscape.jpg","","Category:Photographs from 1996"),
            "photograph-likely")



class TargetAspectAssetRankingTests(unittest.TestCase):
    def test_crop_retention_for_vertical_and_wide_images(self):
        self.assertEqual(media.crop_retention(900, 1600, 9/16), 1.0)
        self.assertAlmostEqual(media.crop_retention(1600, 900, 9/16), .3164, delta=.0001)
        self.assertEqual(media.crop_retention(1600, 900, None), None)
        self.assertIsNone(media.crop_retention(None, None, 9/16))

    def test_equally_relevant_candidates_prefer_mobile_friendly_framing(self):
        payload={"query":{"pages":{
            "1":{"title":"File:Rose-wide.jpg","index":1,"imageinfo":[{
                "width":1600,"height":900,
                "thumburl":"https://upload.wikimedia.org/test.jpg",
                "descriptionurl":"https://commons.wikimedia.org/wiki/File:Rose-wide.jpg",
                "extmetadata":{"LicenseShortName":{"value":"CC0"}}}]},
            "2":{"title":"File:Rose-tall.jpg","index":2,"imageinfo":[{
                "width":900,"height":1600,
                "thumburl":"https://upload.wikimedia.org/test-tall.jpg",
                "descriptionurl":"https://commons.wikimedia.org/wiki/File:Rose-tall.jpg",
                "extmetadata":{"LicenseShortName":{"value":"CC0"}}}]}}}}
        with tempfile.TemporaryDirectory() as name:
            with patch.object(media,"_read_limited",
                    side_effect=[json.dumps(payload).encode(), b"fixture-image"]), \
                 patch.object(media,"media_dimensions",return_value=(900,1600)):
                sources, notices=media.commons_assets(
                    "rose",Path(name),limit=1,target_aspect=9/16)
        self.assertEqual(len(sources),1)
        self.assertEqual(sources[0].title,"File:Rose-tall.jpg")
        self.assertEqual(sources[0].crop_retention_estimate,1.0)
        self.assertEqual(notices,[])

    def test_relevance_remains_more_important_than_framing(self):
        payload={"query":{"pages":{
            "1":{"title":"File:Rose garden flower.jpg","index":1,"imageinfo":[{
                "width":1600,"height":900,
                "thumburl":"https://upload.wikimedia.org/wide.jpg",
                "descriptionurl":"https://commons.wikimedia.org/wiki/File:Rose-garden.jpg",
                "extmetadata":{"LicenseShortName":{"value":"CC0"},
                               "ImageDescription":{"value":"Flower and rose"}}}]},
            "2":{"title":"File:Flower.jpg","index":2,"imageinfo":[{
                "width":900,"height":1600,
                "thumburl":"https://upload.wikimedia.org/tall.jpg",
                "descriptionurl":"https://commons.wikimedia.org/wiki/File:Flower.jpg",
                "extmetadata":{"LicenseShortName":{"value":"CC0"},
                               "ImageDescription":{"value":"Rose flower"}}}]}}}}
        with tempfile.TemporaryDirectory() as name:
            with patch.object(media,"_read_limited",
                    side_effect=[json.dumps(payload).encode(), b"fixture-image"]), \
                 patch.object(media,"media_dimensions",return_value=(1600,900)):
                selected,_=media.commons_assets("rose garden",Path(name),
                                                 limit=1,target_aspect=9/16)
        self.assertEqual(selected[0].title,"File:Rose garden flower.jpg")

    def test_invalid_aspect_is_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as name:
            for bad in (0, -1, float("inf"), float("nan"), "portrait"):
                with self.assertRaises(ValueError):
                    media.commons_assets("nature",Path(name),target_aspect=bad)
