"""Concurrency, attribution, bounded search and network timing regressions."""
from pathlib import Path
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import media_assets as media


def fixture(work: Path, query: str) -> media.MediaAsset:
    return media.MediaAsset(work / (query + ".jpg"), "image",
        "https://commons.wikimedia.org/wiki/File:" + query + ".jpg",
        "CC0", "Tester", "File:" + query + ".jpg")


class CommonsParallelTests(unittest.TestCase):
    def test_three_unique_searches_are_actually_concurrent_and_keep_scene_order(self):
        barrier = threading.Barrier(3, timeout=6)
        calls = []
        lock = threading.Lock()
        def fake_search(query, directory, *, limit, timings, **kwargs):
            with lock:
                calls.append(query)
            barrier.wait()
            timings.update({"api_seconds": 0.15, "download_seconds": 0.07,
                            "verification_seconds": 0.02, "download_bytes": 42,
                            "download_attempts": 1})
            return [fixture(directory, query)], [f"found {query}"]
        with tempfile.TemporaryDirectory() as tmp, patch.object(media, "commons_assets", side_effect=fake_search):
            stats = {}
            selected, warnings = media.commons_scene_assets(
                ["forest", "river", "forest", "ocean"], Path(tmp), stats=stats)
        self.assertEqual(sorted(selected), [0, 1, 2, 3])
        self.assertEqual([selected[k].title for k in selected],
                         ["File:forest.jpg", "File:river.jpg", "File:forest.jpg", "File:ocean.jpg"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(warnings, ["Scene 1: found forest", "Scene 2: found river",
                                    "Scene 4: found ocean"])
        self.assertEqual(stats["unique_queries"], 3)
        self.assertEqual(stats["workers_used"], 3)
        self.assertEqual(stats["download_bytes"], 126)
        self.assertEqual(stats["download_attempts"], 3)
        self.assertAlmostEqual(stats["api_seconds_sum"], 0.45)

    def test_budget_and_broken_query_do_not_affect_other_scenes(self):
        def sometimes_fails(query, directory, *, limit, timings, **kwargs):
            if query == "river":
                raise OSError("network unreachable")
            return [fixture(directory, query)], []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                media, "commons_assets", side_effect=sometimes_fails) as search:
            stats = {}
            selected, warnings = media.commons_scene_assets(
                ["forest", "river", "forest", "ocean", "forest"], Path(tmp),
                max_searches=2, stats=stats)
        self.assertEqual(sorted(selected), [0, 2, 4])
        self.assertEqual(search.call_count, 2)
        self.assertEqual(stats["unique_queries"], 2)
        self.assertEqual(stats["scenes_matched"], 3)
        self.assertTrue(any("Scene 2" in n and "OSError" in n for n in warnings))
        self.assertTrue(any("Scene 4" in n and "budget" in n for n in warnings))

    def test_workers_limit_enforced_and_empty_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            for invalid in (0, 4, -1, "three"):
                with self.subTest(workers=invalid):
                    with self.assertRaises(ValueError):
                        media.commons_scene_assets(["trees"], Path(tmp), max_workers=invalid)
            stats = {}
            self.assertEqual(media.commons_scene_assets(["trees"], Path(tmp), indices=[],
                                                         stats=stats), ({}, []))
            self.assertEqual(stats["unique_queries"], 0)
            self.assertEqual(stats["workers_used"], 0)

    def test_real_render_fallback_stores_metrics(self):
        from free_studio import render
        import shutil
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg required")
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "video.mp4"
            def empty(queries, work, *, indices=None, stats=None, **kw):
                if stats is not None:
                    stats.update({"unique_queries": 2, "wall_seconds": 0.125,
                                  "api_seconds_sum": 0.11, "download_seconds_sum": 0.0,
                                  "scenes_matched": 0})
                return {}, ["Commons unavailable; graphic fallback"]
            with patch("free_studio.commons_scene_assets", side_effect=empty):
                info = render("Forest", "La forêt est magnifique. Le fleuve est bleu.",
                              video, mute=True, resolution=360, fps=12, commons=True)
            self.assertEqual(info["visual_mode"], "abstract-fallback")
            self.assertEqual(info["commons_fetch"]["unique_queries"], 2)
            self.assertAlmostEqual(info["render_performance"]["commons_wall_seconds"], .125)
            self.assertIn("Commons unavailable; graphic fallback", info["warnings"])


if __name__ == "__main__":
    unittest.main()


class CommonsSceneDiversityTests(unittest.TestCase):
    def test_repeated_query_uses_distinct_images_with_one_search(self):
        from collections import Counter
        requested = []
        def fake_search(query, directory, *, limit, timings, **kwargs):
            requested.append((query, limit))
            assets = [media.MediaAsset(
                directory / f"sample-{n}.jpg", "image",
                f"https://commons.wikimedia.org/wiki/File:{query}-{n}.jpg",
                "CC0", "Contributor", f"{query}-{n}")
                for n in range(limit)]
            return assets, []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                media, "commons_assets", side_effect=fake_search) as search:
            stats = {}
            selected, warnings = media.commons_scene_assets(
                ["mountain", "river", "mountain", "mountain", "river", "mountain"],
                Path(tmp), stats=stats)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(dict(requested), {"mountain": 3, "river": 2})
        self.assertEqual(stats["image_variants_budget"], 5)
        self.assertEqual(stats["image_variants_fetched"], 5)
        self.assertEqual(stats["unique_images_selected"], 5)
        self.assertEqual(stats["scenes_reusing_image"], 1)
        self.assertEqual(selected[0].source_url, selected[5].source_url)
        self.assertEqual(len({selected[i].source_url for i in (0, 2, 3)}), 3)
        self.assertEqual(len({selected[i].source_url for i in (1, 4)}), 2)
        self.assertEqual(warnings, [])

    def test_extra_image_budget_fairly_allocated_and_never_exceeds_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_search(query, folder, *, limit, timings, **kwargs):
                return [fixture(folder, query) for _ in range(limit)], []
            with patch.object(media, "commons_assets", side_effect=fake_search) as request:
                stats = {}
                media.commons_scene_assets(
                    ["a", "b", "c", "d"] * 3, Path(tmp), stats=stats)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(stats["unique_queries"], 4)
            self.assertEqual(stats["image_variants_budget"], 8)
            self.assertEqual(sorted(x.kwargs["limit"] for x in request.call_args_list),
                             [2, 2, 2, 2])

    def test_source_dedup_across_different_queries_if_alternative_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_search(query, folder, *, limit, timings, **kwargs):
                common = media.MediaAsset(
                    folder / "common.jpg", "image",
                    "https://commons.wikimedia.org/wiki/File:common.jpg",
                    "CC0", "Contributor", "common")
                alternative = media.MediaAsset(
                    folder / "other.jpg", "image",
                    "https://commons.wikimedia.org/wiki/File:other.jpg",
                    "CC0", "Contributor", "other")
                return ([common, alternative] if query == "forest" else
                        [common])[:limit], []
            with patch.object(media, "commons_assets", side_effect=fake_search):
                selected, warnings = media.commons_scene_assets(
                    ["river", "forest", "forest"], Path(tmp))
            self.assertEqual(selected[0].source_url,
                             "https://commons.wikimedia.org/wiki/File:common.jpg")
            self.assertEqual(selected[1].source_url,
                             "https://commons.wikimedia.org/wiki/File:other.jpg")
            self.assertEqual(len(selected), 3)

    def test_no_extra_downloads_when_every_query_is_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake_search(query, folder, *, limit, timings, **kwargs):
                return [fixture(folder, query)], []
            with patch.object(media, "commons_assets", side_effect=fake_search) as request:
                stats = {}
                found, warnings = media.commons_scene_assets(
                    ["forest", "river", "lake"], Path(tmp), stats=stats)
            self.assertEqual(len(found), 3)
            self.assertEqual([x.kwargs["limit"] for x in request.call_args_list],
                             [1, 1, 1])
            self.assertEqual(stats["scenes_reusing_image"], 0)



class CommonsSecondarySearchTests(unittest.TestCase):
    def test_limited_secondary_lookup_increases_distinct_scene_images(self):
        calls=[]
        def mocked(query, directory, *, limit, timings, **kwargs):
            calls.append((query, limit))
            timings.update({"api_seconds": 0.2, "download_seconds": 0.1,
                            "verification_seconds": 0.02, "download_bytes": 250,
                            "download_attempts": limit})
            assets = [media.MediaAsset(directory / f"{i}.jpg", "image",
                 f"https://commons.wikimedia.org/wiki/File:{query}-{i}.jpg",
                 "Public domain", "Test Author", f"{query}-{i}")
                 for i in range(1 if query == "montagne" else limit)]
            return assets, []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                media, "commons_assets", side_effect=mocked):
            stats={}
            selected,warnings=media.commons_scene_assets(
                ["montagne"]*3, Path(tmp), stats=stats)
        self.assertEqual(calls,[("montagne", 3), ("montagne paysage", 2)])
        self.assertEqual(len(selected),3)
        self.assertEqual(len({a.source_url for a in selected.values()}),3)
        self.assertEqual(stats["unique_queries"],1)
        self.assertEqual(stats["image_variants_budget"],3)
        self.assertEqual(stats["image_variants_fetched"],3)
        self.assertEqual(stats["secondary_queries_attempted"],1)
        self.assertEqual(stats["total_commons_api_queries"],2)
        self.assertAlmostEqual(stats["api_seconds_sum"],0.4)
        self.assertEqual(stats["download_bytes"],500)
        self.assertEqual(warnings,[])

    def test_secondary_search_does_not_claim_original_query_coverage(self):
        def search(query, directory, *, limit, timings, **kwargs):
            results = [media.MediaAsset(
                directory / f"{query.replace(' ', '-')}-{n}.jpg", "image",
                f"https://commons.wikimedia.org/wiki/File:{query}-{n}.jpg",
                "CC0", "Author", query,
                query_term_count=len(query.split()),
                query_term_coverage=1.0)
                for n in range(1 if query == "montagne" else limit)]
            return results, []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                media, "commons_assets", side_effect=search):
            found, _ = media.commons_scene_assets(
                ["montagne"] * 3, Path(tmp), max_searches=3)
        self.assertEqual(found[0].query_term_count, 1)
        self.assertEqual(found[0].query_term_coverage, 1.0)
        self.assertIsNone(found[1].query_term_count)
        self.assertIsNone(found[1].query_term_coverage)
        self.assertIsNone(found[2].query_term_coverage)

    def test_secondary_never_bypasses_total_search_budget(self):
        calls=[]
        def mocked(query, directory, *, limit, timings, **kwargs):
            calls.append(query)
            return [fixture(directory,query)], []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                media, "commons_assets", side_effect=mocked):
            stats={}
            # Only one distinct primary search is allowed.
            chosen,_=media.commons_scene_assets(
                ["montagne"]*3,Path(tmp),max_searches=1,stats=stats)
        self.assertEqual(calls,["montagne"])
        self.assertEqual(stats["total_commons_api_queries"],1)
        self.assertEqual(stats["scenes_reusing_image"],2)
        self.assertEqual(len(chosen),3)

    def test_secondary_failure_keeps_safe_primary_image(self):
        def mocked(query, directory, *, limit, timings, **kwargs):
            if query == "montagne paysage":
                raise ConnectionError("network closed")
            return [fixture(directory,query)], []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                media, "commons_assets", side_effect=mocked):
            stats={}
            chosen,warnings=media.commons_scene_assets(
                ["montagne"]*3,Path(tmp),stats=stats)
        self.assertEqual(len(chosen),3)
        self.assertEqual(len({a.source_url for a in chosen.values()}),1)
        self.assertEqual(stats["secondary_queries_attempted"],1)
        self.assertTrue(any("ConnectionError" in w for w in warnings))

    def test_non_scenic_or_two_occurrences_never_adds_network_calls(self):
        self.assertIsNone(media.alternate_scenic_search_query("Henri Rivière"))
        self.assertEqual(media.alternate_scenic_search_query("montagne"),
                         "montagne paysage")
        self.assertEqual(media.alternate_scenic_search_query("mountain"),
                         "mountain landscape")
        calls=[]
        def mocked(query, directory, *, limit, timings, **kwargs):
            calls.append(query)
            return [fixture(directory,query)], []
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                media, "commons_assets", side_effect=mocked):
            result,_=media.commons_scene_assets(
                ["montagne","montagne"],Path(tmp))
        self.assertEqual(calls,["montagne"])
        self.assertEqual(len(result),2)
