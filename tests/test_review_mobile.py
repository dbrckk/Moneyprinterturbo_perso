"""Offline Android review must be blind, self-contained and CSV-compatible."""
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from review_mobile import build_mobile_review, _preview


def create_jpeg(path: Path, color: str):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=" + color + ":s=128x96",
         "-frames:v", "1", "-update", "1", str(path)],
        capture_output=True, check=False, timeout=25,
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode()[:800])


def fixture_pair(base: Path, *, changed=True, query="forest"):
    images = base / "images"
    images.mkdir(exist_ok=True)
    create_jpeg(images / "first.jpg", "red")
    create_jpeg(images / "second.jpg", "blue")
    return {"cases": [{
        "status": "evaluated",
        "query": query,
        "changed": changed,
        "baseline": {
            "image": "images/first.jpg",
            "source": "https://commons.wikimedia.org/wiki/File:first.jpg",
        },
        "clip": {
            "image": "images/second.jpg" if changed else "images/first.jpg",
            "source": "https://commons.wikimedia.org/wiki/File:second.jpg" if changed
                      else "https://commons.wikimedia.org/wiki/File:first.jpg",
        },
    }]}


class OfflineMobileReviewTests(unittest.TestCase):
    def test_bundles_real_decodable_previews_and_blinds_model_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = fixture_pair(root)
            output = root / "review-mobile.html"
            count = build_mobile_review(summary, root / "images", output)
            page = output.read_text(encoding="utf-8")
            self.assertEqual(count, 1)
            self.assertIn("data:image/jpeg;base64,", page)
            self.assertIn("Exporter mes évaluations", page)
            self.assertIn("connect-src 'none'", page)
            self.assertIn("human_choice_baseline_clip_tie_both_bad", page)
            self.assertNotIn("__REVIEW_DATA__", page)
            self.assertNotIn('src="https://', page)
            self.assertLess(output.stat().st_size, 1_000_000)
            # Opaque left/right ordering is stable for a reproducible review.
            first = page
            build_mobile_review(summary, root / "images", output)
            self.assertEqual(first, output.read_text(encoding="utf-8"))

    def test_untrusted_query_is_never_interpreted_as_html(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = fixture_pair(root, query='forest</script><img src=x onerror=alert(1)>')
            output = root / "review-mobile.html"
            build_mobile_review(summary, root / "images", output)
            page = output.read_text(encoding="utf-8")
            self.assertNotIn("</script><img", page)
            self.assertIn(r"\u003c/script\u003e", page)
            self.assertEqual(page.count("</script>"), 1)

    def test_identical_candidates_are_flagged_for_tie_or_bad(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = fixture_pair(root, changed=False)
            output = root / "review-mobile.html"
            self.assertEqual(build_mobile_review(summary, root / "images", output), 1)
            page = output.read_text()
            self.assertIn('"changed":false', page)
            self.assertIn("Même image, pertinente", page)

    def test_rejects_traversal_and_oversized_images(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = fixture_pair(root)
            summary["cases"][0]["baseline"]["image"] = "images/../secret.jpg"
            with self.assertRaisesRegex(ValueError, "Unsafe benchmark image"):
                build_mobile_review(summary, root / "images", root / "mobile.html")
            summary["cases"][0]["baseline"]["image"] = "images/first.jpg"
            (root / "images/first.jpg").write_bytes(b"not jpeg")
            with self.assertRaises((RuntimeError, ValueError)):
                build_mobile_review(summary, root / "images", root / "mobile.html")

    def test_skipped_cases_do_not_generate_false_mobile_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(build_mobile_review(
                {"cases": [{"status": "skipped", "query": "forest"}]},
                root, root / "mobile.html"), 0)
            self.assertFalse((root / "mobile.html").exists())


if __name__ == "__main__":
    unittest.main()
