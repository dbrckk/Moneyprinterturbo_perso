"""Real selector + storyboard orchestration must honor one global API budget."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib import parse

import media_assets as media


def page(title: str, index: int = 1) -> dict:
    return {
        "title": "File:" + title, "index": index,
        "imageinfo": [{
            "thumburl": "https://upload.wikimedia.org/wikipedia/commons/a/ab/sample.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:" + title.replace(" ", "_"),
            "extmetadata": {
                "LicenseShortName": {"value": "CC0"},
                "Artist": {"value": "Test contributor"},
                "ImageDescription": {"value": "Landscape nature photograph"},
            }
        }]
    }


def response(*images) -> bytes:
    return json.dumps({"query": {"pages": {
        str(index): image for index, image in enumerate(images, 1)
    }}}).encode()


class CommonsGlobalBudgetTests(unittest.TestCase):
    def network(self, result_map: dict[str, bytes], calls: list[str]):
        def read(url, max_bytes, *, timeout):
            if url.startswith(media.API + "?"):
                query = parse.parse_qs(parse.urlsplit(url).query)["gsrsearch"][0]
                calls.append(query)
                return result_map.get(query, response())
            return b"mock-image"
        return read

    def test_exhausted_global_search_budget_blocks_hidden_internal_fallback(self):
        calls = []
        samples = {
            "forest landscape": response(page("Forest trail panorama.jpg")),
            "river landscape": response(page("River gorge panorama.jpg")),
            "forest": response(page("Forest river landscape photograph.jpg")),
            "river": response(page("River forest landscape photograph.jpg")),
        }
        with TemporaryDirectory() as folder:
            stats = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.network(samples, calls)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 800)), \
                 patch.object(media, "visual_signature", return_value=None):
                selected, warnings = media.commons_scene_assets(
                    ["forest landscape", "forest landscape",
                     "river landscape", "river landscape"],
                    Path(folder), max_searches=2, stats=stats)
        self.assertEqual(len(selected), 4)
        self.assertEqual(sorted(calls), ["forest landscape", "river landscape"])
        self.assertEqual(stats["total_commons_api_queries"], 2)
        self.assertEqual(stats["internal_fallback_slots_reserved"], 0)
        self.assertEqual(stats["internal_fallback_queries_attempted"], 0)
        self.assertLessEqual(stats["total_commons_api_queries"], 2)
        self.assertEqual(warnings, [])

    def test_one_spare_call_recovers_distinct_photo_for_first_query_only(self):
        calls = []
        samples = {
            "forest landscape": response(page("Forest trail panorama.jpg")),
            "forest": response(page("Forest river landscape photograph.jpg")),
            "river landscape": response(page("River gorge panorama.jpg")),
        }
        with TemporaryDirectory() as folder:
            stats = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.network(samples, calls)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 800)), \
                 patch.object(media, "visual_signature", return_value=None):
                selected, warnings = media.commons_scene_assets(
                    ["forest landscape", "forest landscape",
                     "river landscape", "river landscape"],
                    Path(folder), max_searches=3, stats=stats)
        self.assertEqual(len(selected), 4)
        self.assertEqual(sorted(calls),
                         ["forest", "forest landscape", "river landscape"])
        self.assertEqual(stats["total_commons_api_queries"], 3)
        self.assertEqual(stats["internal_fallback_slots_reserved"], 1)
        self.assertEqual(stats["internal_fallback_queries_attempted"], 1)
        self.assertEqual(stats["secondary_queries_attempted"], 0)
        self.assertEqual(warnings, [])

    def test_explicit_secondary_cannot_spawn_nested_fallback(self):
        calls = []
        samples = {
            "montagne panorama": response(page("Montagne summit panorama.jpg")),
            "montagne paysage": response(
                page("Montagne glacier landscape.jpg"),
                page("Montagne forest landscape.jpg", index=2)),
        }
        with TemporaryDirectory() as folder:
            stats = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.network(samples, calls)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 800)), \
                 patch.object(media, "visual_signature", return_value=None):
                selected, warnings = media.commons_scene_assets(
                    ["montagne"] * 3, Path(folder), max_searches=2,
                    stats=stats)
        self.assertEqual(len(selected), 3)
        self.assertCountEqual(calls, ["montagne paysage", "montagne panorama"])
        self.assertEqual(stats["secondary_queries_attempted"], 1)
        self.assertEqual(stats["internal_fallback_slots_reserved"], 0)
        self.assertEqual(stats["internal_fallback_queries_attempted"], 0)
        self.assertEqual(stats["total_commons_api_queries"], 2)
        self.assertEqual(warnings, [])

    def test_direct_selector_preserves_optional_expansion_but_can_disable_it(self):
        data = {
            "forest landscape": response(page("Forest trail panorama.jpg")),
            "forest": response(page("Forest river landscape photograph.jpg")),
        }
        with TemporaryDirectory() as folder:
            calls = []
            stats = {}
            with patch.object(media, "_read_limited",
                              side_effect=self.network(data, calls)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 800)), \
                 patch.object(media, "visual_signature", return_value=None):
                found, warnings = media.commons_assets(
                    "forest landscape", Path(folder), limit=2, timings=stats,
                    allow_alternative=False)
            self.assertEqual(len(found), 1)
            self.assertEqual(calls, ["forest landscape"])
            self.assertEqual(stats["fallback_search_attempted"], 0)
            self.assertEqual(stats["api_queries"], 1)
            self.assertEqual(warnings, [])

            calls.clear()
            with patch.object(media, "_read_limited",
                              side_effect=self.network(data, calls)), \
                 patch.object(media, "media_dimensions", return_value=(1200, 800)), \
                 patch.object(media, "visual_signature", return_value=None):
                found, warnings = media.commons_assets(
                    "forest landscape", Path(folder), limit=2,
                    allow_alternative=True)
            self.assertEqual(len(found), 2)
            self.assertEqual(calls, ["forest landscape", "forest"])
            self.assertEqual(warnings, [])

    def test_alternative_setting_must_be_boolean(self):
        with TemporaryDirectory() as folder:
            for bad in ("false", 1, 0, None, []):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    media.commons_assets("forest", Path(folder),
                                         allow_alternative=bad)


if __name__ == "__main__":
    unittest.main()
