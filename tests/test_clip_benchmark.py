import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from semantic.benchmark import evaluate

class BenchmarkTests(TestCase):
    def test_documented_cli_is_directly_executable_from_repository_root(self):
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, str(root / "semantic" / "benchmark.py"), "--help"]
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--output", result.stdout)

    def test_paired_scores_count_improvement_without_using_clip_as_ground_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ("a.jpg","b.jpg"):
                (root/name).write_bytes(b"fixture")
            data={"cases":[{"query":"forest","candidates":[
                {"path":str(root/"a.jpg"),"relevance":0},
                {"path":str(root/"b.jpg"),"relevance":2}]}]}
            def fake_rerank(groups, work):
                assets=groups["forest"][0]
                return {"forest":([assets[1],assets[0]],[],{})},{"semantic_status":"ok"},[]
            with patch("semantic.benchmark.rerank",side_effect=fake_rerank):
                result=evaluate(data,root)
            self.assertEqual(result["improved"],1)
            self.assertEqual(result["worsened"],0)
            self.assertEqual(result["baseline_mean_relevance"],0)
            self.assertEqual(result["clip_mean_relevance"],2)

    def test_rejects_invalid_human_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ("a.jpg","b.jpg"):
                (root/name).write_bytes(b"fixture")
            data={"cases":[{"query":"forest","candidates":[
                {"path":str(root/"a.jpg"),"relevance":True},
                {"path":str(root/"b.jpg"),"relevance":2}]}]}
            with self.assertRaises(ValueError):
                evaluate(data,root)
