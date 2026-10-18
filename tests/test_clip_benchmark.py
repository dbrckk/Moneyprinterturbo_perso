"""Human-relevance benchmarks must never invent a CLIP quality outcome."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from semantic.benchmark import evaluate, main


def case(root, query="forest", scores=(0, 2), names=None):
    names = names or [f"{query}-{i}.jpg" for i in range(len(scores))]
    candidates = []
    for name, score in zip(names, scores):
        path = root / name
        path.write_bytes(b"local test image")
        candidates.append({"path": str(path), "relevance": score})
    return {"query": query, "candidates": candidates}


class BenchmarkTests(TestCase):
    def test_documented_cli_is_directly_executable_from_repository_root(self):
        root = Path(__file__).resolve().parents[1]
        run = subprocess.run(
            [sys.executable, str(root / "semantic" / "benchmark.py"), "--help"],
            cwd=root, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--output", run.stdout)

    def test_independent_labels_measure_only_real_selected_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"cases": [case(root)]}
            def fake(groups, work):
                assets = groups["forest"][0]
                self.assertTrue(all(a.license == "unverified" for a in assets))
                self.assertTrue(all(a.source_url == "local-benchmark" for a in assets))
                return {"forest": ([assets[1], assets[0]], [], {})}, {
                    "semantic_status": "ok", "semantic_candidate_count": 2}, []
            with patch("semantic.benchmark.rerank", side_effect=fake):
                result = evaluate(data, root / "new" / "working-dir")
            self.assertTrue(result["comparison_valid"])
            self.assertEqual(result["comparison_status"], "ok")
            self.assertEqual(result["improved"], 1)
            self.assertEqual(result["worsened"], 0)
            self.assertEqual(result["baseline_mean_relevance"], 0)
            self.assertEqual(result["clip_mean_relevance"], 2)
            self.assertEqual(result["cases"][0]["clip_relevance"], 2)
            self.assertEqual(result["candidate_count"], 2)

    def test_fallback_is_inconclusive_not_automatic_equality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"cases": [case(root)]}
            def unavailable(groups, work):
                return groups, {"semantic_status": "fallback",
                                "semantic_candidate_count": 2}, ["worker offline"]
            with patch("semantic.benchmark.rerank", side_effect=unavailable):
                result = evaluate(data, root)
            self.assertFalse(result["comparison_valid"])
            self.assertEqual(result["comparison_status"], "fallback")
            self.assertEqual(result["baseline_mean_relevance"], 0)
            for metric in ("clip_mean_relevance", "improved", "worsened", "unchanged"):
                self.assertIsNone(result[metric], metric)
            self.assertIsNone(result["cases"][0]["clip_relevance"])
            self.assertIsNone(result["cases"][0]["changed"])
            self.assertIn("worker offline", result["warnings"])
            self.assertTrue(any("withheld" in note for note in result["warnings"]))

    def test_partial_worker_results_do_not_count_as_model_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"cases": [case(root), case(root, "mountain")]}
            def partial(groups, work):
                return {"forest": groups["forest"]}, {
                    "semantic_status": "ok", "semantic_candidate_count": 2}, []
            with patch("semantic.benchmark.rerank", side_effect=partial):
                result = evaluate(data, root)
            self.assertFalse(result["comparison_valid"])
            self.assertIsNone(result["clip_mean_relevance"])
            self.assertEqual(result["comparison_status"], "invalid_result")

    def test_rejects_invalid_or_boolean_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = case(root)
            for label in (True, None, "2", -1, 3):
                bad["candidates"][0]["relevance"] = label
                with self.subTest(label=label), self.assertRaises(ValueError):
                    evaluate({"cases": [bad]}, root)

    def test_rejects_duplicate_candidates_and_casefolded_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = case(root)
            first["candidates"][1]["path"] = first["candidates"][0]["path"]
            with self.assertRaisesRegex(ValueError, "duplicate"):
                evaluate({"cases": [first]}, root)
            first = case(root)
            other = case(root, "Forest")
            with self.assertRaisesRegex(ValueError, "unique"):
                evaluate({"cases": [first, other]}, root)

    def test_rejects_unsupported_visual_queries_and_bad_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for query in ("Henri Rivière", "global business strategy", "", "forest "):
                with self.subTest(query=query), self.assertRaises(ValueError):
                    evaluate({"cases": [case(root, query)]}, root)
            for data in ([], {"cases": []}, {"cases": [None]},
                         {"cases": [{"query": "forest", "candidates": []}]}):
                with self.subTest(data=data), self.assertRaises(ValueError):
                    evaluate(data, root)

    def test_exactly_32_local_images_can_be_evaluated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"cases": [
                case(root, "forest", (0,) * 16),
                case(root, "mountain", (1,) * 16),
            ]}
            def passthrough(groups, work):
                return groups, {
                    "semantic_status": "ok", "semantic_candidate_count": 32}, []
            with patch("semantic.benchmark.rerank", side_effect=passthrough):
                report = evaluate(data, root)
            self.assertTrue(report["comparison_valid"])
            self.assertEqual(report["candidate_count"], 32)
            self.assertEqual(report["unchanged"], 2)
            self.assertEqual(report["baseline_mean_relevance"], 0.5)

    def test_33_images_rejected_before_worker_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"cases": [
                case(root, "forest", (0,) * 17),
                case(root, "mountain", (2,) * 16),
            ]}
            with patch("semantic.benchmark.rerank") as worker:
                with self.assertRaisesRegex(ValueError, "At most 32"):
                    evaluate(data, root)
                worker.assert_not_called()

    def test_cli_writes_inconclusive_json_and_exits_nonzero_without_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [case(root)]}))
            target = root / "nested" / "results" / "benchmark.json"
            def fallback(groups, work):
                return groups, {"semantic_status": "fallback"}, []
            with patch("semantic.benchmark.rerank", side_effect=fallback):
                result_code = main([str(dataset), "--output", str(target)])
            self.assertEqual(result_code, 2)
            report = json.loads(target.read_text())
            self.assertFalse(report["comparison_valid"])
            self.assertIsNone(report["clip_mean_relevance"])


if __name__ == "__main__":
    import unittest
    unittest.main()
