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
                    fixture(folder, "Landscape <img src=x onerror=alert(1)>"),
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
            self.assertNotIn("<img src=x onerror=alert(1)>", report)
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", report)
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

    def test_real_human_ratings_are_aggregated_only_after_explicit_review(self):
        queries = ab.BENCHMARK_QUERIES[:2]
        metrics = {"evaluated_cases": 2, "cases": [
            {"query": queries[0], "status": "evaluated", "changed": True,
             "baseline": {"source": "https://commons.wikimedia.org/wiki/File:a.jpg"},
             "clip": {"source": "https://commons.wikimedia.org/wiki/File:b.jpg"}},
            {"query": queries[1], "status": "evaluated", "changed": False,
             "baseline": {"source": "https://commons.wikimedia.org/wiki/File:c.jpg"},
             "clip": {"source": "https://commons.wikimedia.org/wiki/File:c.jpg"}},
        ]}
        with TemporaryDirectory() as temp:
            file = Path(temp) / "review.csv"
            def save(rows):
                with file.open("w", encoding="utf-8", newline="") as fp:
                    writer = csv.writer(fp)
                    writer.writerow(["query", "baseline_url", "clip_url",
                                     "human_choice_baseline_clip_tie_both_bad", "reviewer_notes"])
                    writer.writerows(rows)
            good = [
                [queries[0], metrics["cases"][0]["baseline"]["source"],
                 metrics["cases"][0]["clip"]["source"], "clip", ""],
                [queries[1], metrics["cases"][1]["baseline"]["source"],
                 metrics["cases"][1]["clip"]["source"], "tie", ""],
            ]
            save(good)
            result = ab.score_human_reviews(metrics, file)
            self.assertEqual(result["human_reviews_completed"], 2)
            self.assertEqual(result["changed_cases_reviewed"], 1)
            self.assertEqual(result["human_choices"]["clip"], 1)
            self.assertEqual(result["clip_share_of_decisive_votes"], 1.0)
            self.assertIn("no automatic default switch", result["disclaimer"])
            good[1][3] = "baseline"
            save(good)
            with self.assertRaisesRegex(ValueError, "Identical photos"):
                ab.score_human_reviews(metrics, file)
            good[1][3] = "tie"
            good[0][1] = "https://wrong.example/image.jpg"
            save(good)
            with self.assertRaisesRegex(ValueError, "sources do not match"):
                ab.score_human_reviews(metrics, file)

    def test_review_scorer_refuses_missing_duplicates_and_invalid_choices(self):
        queries = ab.BENCHMARK_QUERIES[:1]
        case = {"query": queries[0], "status": "evaluated", "changed": True,
                "baseline": {"source": "https://commons.wikimedia.org/a"},
                "clip": {"source": "https://commons.wikimedia.org/b"}}
        metrics = {"evaluated_cases": 1, "cases": [case]}
        with TemporaryDirectory() as temp:
            file = Path(temp) / "votes.csv"
            with file.open("w", encoding="utf-8", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(["query", "baseline_url", "clip_url",
                                 "human_choice_baseline_clip_tie_both_bad", "reviewer_notes"])
                writer.writerow([queries[0], case["baseline"]["source"],
                                 case["clip"]["source"], "", ""])
            result = ab.score_human_reviews(metrics, file)
            self.assertEqual(result["human_reviews_completed"], 0)
            self.assertIsNone(result["clip_share_of_decisive_votes"])
            with file.open("a", encoding="utf-8", newline="") as fp:
                csv.writer(fp).writerow([queries[0], case["baseline"]["source"],
                                         case["clip"]["source"], "clip", ""])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                ab.score_human_reviews(metrics, file)

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
