"""Benchmark reports measure choices, not made-up visual accuracy."""
import csv
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from media_assets import MediaAsset
import visual_benchmark as ab


def fixture(directory, title, license="CC0"):
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / (title + ".jpg")
    file.write_bytes(b"test image bytes")
    return MediaAsset(
        file=file, kind="image",
        source_url="https://commons.wikimedia.org/wiki/File:" + title + ".jpg",
        license=license, author="<Artist>", title=title,
        visual_medium="photograph-likely",
        query_term_count=2, query_term_coverage=0.5,
    )


class BenchmarkTests(unittest.TestCase):
    def test_ab_review_has_real_provenance_but_never_claims_accuracy(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            queries = ab.BENCHMARK_QUERIES[:2]
            def download(query, folder, *, limit, target_aspect):
                self.assertEqual(limit, 2)
                self.assertAlmostEqual(target_aspect, 9 / 16)
                return [
                    fixture(folder, "Landscape <script>alert(1)</script>"),
                    fixture(folder, "Mountain-B"),
                ], []
            def compare(results, folder):
                updated = {}
                for query, (items, notes, timing) in results.items():
                    enriched = [replace(item, semantic_score=0.1 + i * 0.8)
                                for i, item in enumerate(items)]
                    updated[query] = (enriched[::-1], notes, timing)
                return updated, {"semantic_status": "ok", "semantic_seconds": 2.4}, []
            result = ab.run_benchmark(
                root, queries=queries, fetch=download, compare=compare,
                make_sheet=lambda summary, images, path: True)
            self.assertEqual(result["requested_cases"], 2)
            self.assertEqual(result["evaluated_cases"], 2)
            self.assertEqual(result["changed_cases"], 2)
            self.assertEqual(result["retained_cases"], 0)
            self.assertEqual(result["human_reviews_completed"], 0)
            self.assertTrue(result["contact_sheet_ready"])
            summary = json.loads((root / "metrics.json").read_text())
            self.assertIn("not measured", summary["description"])
            report = (root / "report.html").read_text()
            self.assertNotIn("<script>alert(1)</script>", report)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", report)
            self.assertNotIn("<Artist>", report)
            self.assertIn("&lt;Artist&gt;", report)
            self.assertIn("Choix modifié", report)
            with (root / "human-review.csv").open(newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["human_choice_baseline_clip_tie_both_bad"], "")
            self.assertTrue((root / "images/00-baseline.jpg").is_file())
            self.assertTrue((root / "images/00-clip.jpg").is_file())

    def test_invalid_licenses_are_rejected_before_report_copy(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for license in ("CC BY-SA", "unknown", "All Rights Reserved"):
                item = fixture(root, "photo", license=license)
                with self.assertRaises(ValueError):
                    ab.source_record(item, root, "licensed")
                self.assertFalse((root / "licensed.jpg").exists())

    def test_benchmark_cannot_accept_arbitrary_user_queries(self):
        with TemporaryDirectory() as temp:
            for queries in [(), ("mountain landscape",) * 2,
                            ("bad; download unlicensed",), tuple(ab.BENCHMARK_QUERIES) * 2]:
                with self.subTest(queries=queries), self.assertRaises(ValueError):
                    ab.run_benchmark(Path(temp), queries=queries)

    def test_failed_model_keeps_no_false_success_or_visual_claim(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            def download(query, folder, *, limit, target_aspect):
                return [fixture(folder, "a"), fixture(folder, "b")], []
            def failed(results, work):
                return results, {"semantic_status": "fallback", "semantic_seconds": 1.0}, [
                    "CLIP offline"]
            result = ab.run_benchmark(
                root, queries=ab.BENCHMARK_QUERIES[:2],
                fetch=download, compare=failed,
                make_sheet=lambda *a: False)
            self.assertEqual(result["semantic_status"], "fallback")
            self.assertEqual(result["evaluated_cases"], 0)
            self.assertEqual(result["changed_cases"], 0)
            self.assertEqual(result["contact_sheet_ready"], False)
            self.assertFalse(list((root / "images").glob("*")))
            self.assertIn("CLIP offline", (root / "metrics.json").read_text())

    def test_insufficient_licensed_candidates_are_reported_as_skipped(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            def download(query, folder, *, limit, target_aspect):
                return [fixture(folder, "solo")], ["No second licensed image"]
            def compare(results, work):
                return results, {"semantic_status": "skipped", "semantic_seconds": 0.0}, []
            report = ab.run_benchmark(
                root, queries=ab.BENCHMARK_QUERIES[:2],
                fetch=download, compare=compare,
                make_sheet=lambda *args: False)
            self.assertEqual(report["evaluated_cases"], 0)
            self.assertTrue(all(row["status"] == "skipped" for row in report["cases"]))
            self.assertIn("No second licensed image", (root / "report.html").read_text())

    def test_missing_contact_sheet_does_not_invalidate_source_and_json_report(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            def download(query, folder, *, limit, target_aspect):
                return [fixture(folder, "a"), fixture(folder, "b")], []
            def compare(results, work):
                return {query: ([replace(row, semantic_score=0.5)
                                  for row in assets], warnings, timings)
                        for query, (assets, warnings, timings) in results.items()}, {
                            "semantic_status": "ok", "semantic_seconds": 1.0}, []
            report = ab.run_benchmark(
                root, queries=ab.BENCHMARK_QUERIES[:2],
                fetch=download, compare=compare,
                make_sheet=lambda *args: False)
            self.assertEqual(report["evaluated_cases"], 2)
            self.assertEqual(report["changed_cases"], 0)
            self.assertFalse(report["contact_sheet_ready"])
            self.assertTrue((root / "report.html").is_file())
            self.assertTrue((root / "metrics.json").is_file())


if __name__ == "__main__":
    unittest.main()
