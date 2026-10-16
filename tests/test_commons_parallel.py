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
        def fake_search(query, directory, *, limit, timings):
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
        def sometimes_fails(query, directory, *, limit, timings):
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
