"""Bounded licensed-media discovery: coverage, safety, and network budget."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib import parse

import media_assets as media


def commons_page(title, license_name="CC0", index=1, description=""):
    return {
        "title": "File:" + title,
        "index": index,
        "imageinfo": [{
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:" + title.replace(" ", "_"),
            "thumburl": "https://upload.wikimedia.org/wikipedia/commons/a/ab/thumb.jpg",
            "extmetadata": {
                "LicenseShortName": {"value": license_name},
                "Artist": {"value": "Commons contributor"},
                "ImageDescription": {"value": description},
            },
        }],
    }


def api_payload(*pages):
    return json.dumps({"query": {"pages": {
        str(i): page for i, page in enumerate(pages, start=1)
    }}}).encode()


class CommonsCoverageTests(unittest.TestCase):
    def fake_network(self, results, observed):
        def read(url, limit, *, timeout):
            if url.startswith(media.API + "?"):
                params = parse.parse_qs(parse.urlsplit(url).query)
                phrase = params["gsrsearch"][0]
                observed.append(params)
                return results.get(phrase, api_payload())
            return b"mock-image-data"
        return read

    def test_broad_fallback_adds_distinct_originally_relevant_cc0_candidates(self):
        first = commons_page("Forest mountain panorama.jpg", index=1,
                             description="Photograph of a forest landscape")
        duplicate = commons_page("Forest mountain panorama.jpg", index=2)
        second = commons_page("Forest trail panorama.jpg", index=3,
                              description="Woods and forest landscape")
        rejected = commons_page("Forest trail painting.jpg", license_name="CC BY 4.0")
        observed = []
        results = {
            "forest landscape": api_payload(first, rejected),
            "forest": api_payload(duplicate, second),
        }
        with TemporaryDirectory() as folder:
            timings = {}
            with patch.object(media, "_read_limited", side_effect=self.fake_network(results, observed)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 900)), \
                 patch.object(media, "visual_signature", side_effect=[0, 18446744073709551615]):
                assets, notices = media.commons_assets(
                    "forest landscape", Path(folder), limit=2,
                    target_aspect=9 / 16, timings=timings)
        self.assertEqual(len(assets), 2)
        self.assertEqual(len(set(item.title for item in assets)), 2)
        self.assertTrue(all(item.license == "CC0" for item in assets))
        self.assertTrue(all(item.query_term_coverage > 0 for item in assets))
        self.assertEqual([p["gsrsearch"][0] for p in observed],
                         ["forest landscape", "forest"])
        self.assertEqual(timings["api_queries"], 2)
        self.assertEqual(timings["fallback_search_attempted"], 1)
        self.assertEqual(timings["fallback_search_added"], 1)
        self.assertEqual(timings["licensed_relevant_candidates"], 2)
        self.assertEqual(timings["download_attempts"], 2)
        self.assertEqual(observed[0]["gsrlimit"], ["45"])
        self.assertEqual(observed[0]["gsrnamespace"], ["6"])
        self.assertIn("LicenseShortName", observed[0]["iiextmetadatafilter"][0])
        self.assertEqual(notices, [])

    def test_distinct_images_required_even_when_licensed_titles_differ(self):
        observed = []
        results = {"forest landscape": api_payload(
            commons_page("Forest trail A.jpg", index=1),
            commons_page("Forest trail B.jpg", index=2),
            commons_page("Forest trail C.jpg", index=3),
        )}
        fingerprints = iter((0, 0, 18446744073709551615))
        with TemporaryDirectory() as folder:
            timings = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.fake_network(results, observed)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 900)), \
                 patch.object(media, "visual_signature",
                              side_effect=lambda path: next(fingerprints)):
                assets, warnings = media.commons_assets(
                    "forest landscape", Path(folder), limit=2, timings=timings)
            names = sorted(path.name for path in Path(folder).iterdir())
        self.assertEqual([a.title for a in assets],
                         ["File:Forest trail A.jpg", "File:Forest trail C.jpg"])
        self.assertEqual(names, ["commons_00.jpg", "commons_01.jpg"])
        self.assertEqual(timings["near_duplicate_downloads_skipped"], 1)
        self.assertEqual(timings["download_attempts"], 3)
        self.assertEqual(len(observed), 1)
        self.assertEqual(warnings, [])

    def test_unknown_fingerprint_does_not_block_licensed_candidates(self):
        observed = []
        results = {"forest landscape": api_payload(
            commons_page("Forest trail A.jpg"), commons_page("Forest trail B.jpg", index=2))}
        with TemporaryDirectory() as folder:
            with patch.object(media, "_read_limited",
                              side_effect=self.fake_network(results, observed)), \
                 patch.object(media, "media_dimensions", return_value=(900, 1200)), \
                 patch.object(media, "visual_signature", return_value=None):
                assets, _ = media.commons_assets(
                    "forest landscape", Path(folder), limit=2)
        self.assertEqual(len(assets), 2)

    def test_no_second_search_when_two_verified_candidates_exist(self):
        observed = []
        results = {"forest landscape": api_payload(
            commons_page("Forest mountain panorama.jpg", index=1),
            commons_page("Forest trail panorama.jpg", index=2),
        )}
        with TemporaryDirectory() as folder:
            timings = {}
            with patch.object(media, "_read_limited", side_effect=self.fake_network(results, observed)), \
                 patch.object(media, "media_dimensions", return_value=(900, 1600)), \
                 patch.object(media, "visual_signature", side_effect=[0, 18446744073709551615]):
                assets, _ = media.commons_assets(
                    "forest landscape", Path(folder), limit=2, timings=timings)
        self.assertEqual(len(assets), 2)
        self.assertEqual(timings["api_queries"], 1)
        self.assertEqual(timings["fallback_search_attempted"], 0)
        self.assertEqual(len(observed), 1)

    def test_second_search_recovers_when_licensed_primary_photos_are_duplicates(self):
        observed = []
        data = {
            "forest landscape": api_payload(
                commons_page("Forest trail panorama first.jpg", index=1),
                commons_page("Forest trail panorama second.jpg", index=2)),
            "forest": api_payload(
                commons_page("Forest trail panorama first.jpg", index=3),
                commons_page("Forest river panorama third.jpg", index=4)),
        }
        # Primary files are different uploads of the same visual photo.
        # Secondary photo is genuinely different and already CC0.
        fingerprints = iter((0, 0, 18446744073709551615))
        with TemporaryDirectory() as folder:
            timings = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.fake_network(data, observed)), \
                 patch.object(media, "media_dimensions", return_value=(900, 1600)), \
                 patch.object(media, "visual_signature",
                              side_effect=lambda path: next(fingerprints)):
                assets, warnings = media.commons_assets(
                    "forest landscape", Path(folder), limit=2, timings=timings)
        self.assertEqual([a.title for a in assets], [
            "File:Forest trail panorama first.jpg",
            "File:Forest river panorama third.jpg"])
        self.assertEqual([p["gsrsearch"][0] for p in observed],
                         ["forest landscape", "forest"])
        self.assertEqual(timings["near_duplicate_downloads_skipped"], 1)
        self.assertEqual(timings["fallback_search_attempted"], 1)
        self.assertEqual(timings["fallback_after_verification"], 1)
        self.assertEqual(timings["api_queries"], 2)
        self.assertEqual(timings["download_attempts"], 3)
        self.assertEqual(warnings, [])

    def test_secondary_query_recovers_after_corrupt_primary_candidate(self):
        attempts = []
        data = {
            "forest landscape": api_payload(
                commons_page("Forest trail panorama first.jpg", index=1),
                commons_page("Forest trail panorama second.jpg", index=2)),
            "forest": api_payload(
                commons_page("Forest river panorama third.jpg", index=3)),
        }
        dimensions = iter((None, (900, 1600), (900, 1600)))
        signatures = iter((0, 18446744073709551615))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            stats = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.fake_network(data, attempts)), \
                 patch.object(media, "media_dimensions",
                              side_effect=lambda path: next(dimensions)), \
                 patch.object(media, "visual_signature",
                              side_effect=lambda path: next(signatures)):
                assets, warnings = media.commons_assets(
                    "forest landscape", root, limit=2, timings=stats)
            remaining = sorted(item.name for item in root.iterdir())
        self.assertEqual(len(assets), 2)
        self.assertEqual(stats["api_queries"], 2)
        self.assertEqual(stats["download_attempts"], 3)
        self.assertEqual(stats["fallback_after_verification"], 1)
        self.assertEqual(remaining, ["commons_00.jpg", "commons_01.jpg"])
        self.assertEqual(warnings, [])

    def test_post_download_alternative_never_accepts_unlicensed_or_off_topic(self):
        observed = []
        data = {
            "forest landscape": api_payload(
                commons_page("Forest trail panorama first.jpg", index=1),
                commons_page("Forest trail panorama second.jpg", index=2)),
            "forest": api_payload(
                commons_page("Forest mountain panorama third.jpg",
                             license_name="CC BY-SA 4.0", index=3),
                commons_page("Portrait of Forest explorer.jpg",
                             description="Portrait of a person", index=4)),
        }
        with TemporaryDirectory() as folder:
            stats = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.fake_network(data, observed)), \
                 patch.object(media, "media_dimensions", return_value=(900, 1600)), \
                 patch.object(media, "visual_signature", return_value=1):
                assets, _ = media.commons_assets(
                    "forest landscape", Path(folder), limit=2, timings=stats)
        self.assertEqual(len(assets), 1)
        self.assertEqual(stats["api_queries"], 2)
        self.assertEqual(stats["fallback_after_verification"], 1)
        self.assertEqual(stats["fallback_search_added"], 0)
        self.assertEqual(stats["near_duplicate_downloads_skipped"], 1)

    def test_default_one_image_search_never_uses_fallback(self):
        observed = []
        data = {"mountain panorama": api_payload(
            commons_page("Mountain summit photograph.jpg",
                         license_name="CC BY-NC 4.0"))}
        with TemporaryDirectory() as folder:
            with patch.object(media, "_read_limited",
                              side_effect=self.fake_network(data, observed)):
                found, warning = media.commons_assets("mountain", Path(folder), limit=1)
        self.assertEqual(found, [])
        self.assertEqual(len(observed), 1)
        self.assertTrue(any("No relevant" in message for message in warning))

    def test_fallback_never_expands_names_abstract_or_multi_concept_queries(self):
        self.assertIsNone(media.alternative_commons_search_query("Henri Rivière"))
        self.assertIsNone(media.alternative_commons_search_query("red forest"))
        self.assertIsNone(media.alternative_commons_search_query("forest snow"))
        self.assertIsNone(media.alternative_commons_search_query("business strategy"))
        self.assertEqual(media.alternative_commons_search_query("forest landscape"), "forest")
        self.assertEqual(media.alternative_commons_search_query("forêt paysage"), "forest")
        self.assertEqual(media.alternative_commons_search_query("mountain"), "mountain")
        self.assertEqual(media.alternative_commons_search_query("castle"), "chateau")
        self.assertEqual(media.alternative_commons_search_query("waterfall"), "cascade")

    def test_fallback_does_not_bypass_original_relevance_or_license(self):
        observed = []
        results = {
            "river landscape": api_payload(commons_page(
                "River gorge panorama.jpg", description="River water landscape")),
            "river": api_payload(
                commons_page("Portrait of Henri Riviere.jpg",
                             description="Portrait of a painter"),
                commons_page("River cliff panorama.jpg",
                             license_name="CC BY-SA 4.0"),
            ),
        }
        with TemporaryDirectory() as folder:
            with patch.object(media, "_read_limited",
                              side_effect=self.fake_network(results, observed)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 800)), \
                 patch.object(media, "visual_signature", return_value=3):
                found, warning = media.commons_assets(
                    "river landscape", Path(folder), limit=2)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].title, "File:River gorge panorama.jpg")
        self.assertEqual(len(observed), 2)
        self.assertEqual(warning, [])

    def test_failed_second_network_query_preserves_first_licensed_image(self):
        attempts = []
        def read(url, limit, *, timeout):
            if "api.php?" in url:
                phrase = parse.parse_qs(parse.urlsplit(url).query)["gsrsearch"][0]
                attempts.append(phrase)
                if phrase == "forest":
                    raise TimeoutError("testing secondary API timeout")
                return api_payload(commons_page("Forest trail panorama.jpg"))
            return b"mock-image"
        with TemporaryDirectory() as folder:
            with patch.object(media, "_read_limited", side_effect=read), \
                 patch.object(media, "media_dimensions", return_value=(900, 1600)), \
                 patch.object(media, "visual_signature", return_value=4):
                found, warnings = media.commons_assets(
                    "forest landscape", Path(folder), limit=2)
        self.assertEqual(len(found), 1)
        self.assertEqual(attempts, ["forest landscape", "forest"])
        self.assertTrue(any("Alternative Commons" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
