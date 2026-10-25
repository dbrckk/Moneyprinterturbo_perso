"""Evidence-based post-export review for private Android video projects."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from render_review import summarize_completed_render as review
from studio_server import Jobs


def manifest():
    return {
        "duration_seconds": 6.0,
        "segments": [
            {"text": "Bonjour.", "start": 0, "end": 3},
            {"text": "Bienvenue.", "start": 3, "end": 6},
        ],
        "speech": True, "speech_engine": "espeak",
        "caption_timing": "estimated-per-word",
        "media": [{"segment": 1, "kind": "image", "source": "local"}],
        "final_visual_review": {
            "sampled_count": 1, "samples_unavailable": 0,
            "boundaries": [{
                "boundary": 1, "review_confidence": "high",
                "requires_manual_review": True,
            }],
        },
        "warnings": ["A transition needs inspection."],
    }


class RenderReviewTests(unittest.TestCase):
    def test_realistic_partial_media_render_highlights_actual_gaps(self):
        summary = review(manifest())
        self.assertEqual(summary["status"], "exported-not-artistically-approved")
        self.assertTrue(summary["manual_review_required"])
        self.assertEqual(summary["scenes"], 2)
        self.assertEqual(summary["with_media"], 1)
        self.assertEqual(summary["with_graphic_fallback"], 1)
        self.assertEqual(len(summary["checks"]), 6)
        by_key = {x["key"]: x for x in summary["checks"]}
        self.assertEqual(
            set(by_key), {"media", "voice", "captions", "transitions", "music", "rights"})
        self.assertTrue(by_key["media"]["attention"])
        self.assertTrue(by_key["voice"]["attention"])
        self.assertTrue(by_key["captions"]["attention"])
        self.assertTrue(by_key["transitions"]["attention"])
        self.assertTrue(by_key["rights"]["attention"])
        self.assertIn("Aucune musique", by_key["music"]["detail"])
        self.assertIn("1/2", by_key["media"]["detail"])
        self.assertEqual(summary["warnings_total"], 1)

    def test_measured_captions_speech_and_clean_review_never_certify_art(self):
        data = manifest()
        data["speech_engine"] = "piper"
        data["caption_timing"] = "forced-audio-word-onsets"
        data["media"].append({"segment": 2, "kind": "video", "source": "local"})
        data["final_visual_review"]["boundaries"][0]["requires_manual_review"] = False
        data["warnings"] = []
        result = review(data)
        by_key = {x["key"]: x for x in result["checks"]}
        self.assertFalse(by_key["media"]["attention"])
        self.assertFalse(by_key["voice"]["attention"])
        self.assertFalse(by_key["captions"]["attention"])
        self.assertFalse(by_key["transitions"]["attention"])
        self.assertTrue(result["manual_review_required"])
        self.assertIn("pas sur un jugement esthétique", result["summary"])
        self.assertEqual(result["with_graphic_fallback"], 0)

    def test_muted_one_scene_and_missing_review_are_not_claimed_as_errors(self):
        data = manifest()
        data["segments"] = data["segments"][:1]
        data["media"] = []
        data["speech"] = False
        data["speech_engine"] = "muted"
        data["caption_timing"] = "scene-level"
        data["final_visual_review"] = None
        result = review(data)
        by_key = {x["key"]: x for x in result["checks"]}
        self.assertFalse(by_key["voice"]["attention"])
        self.assertIn("mode muet", by_key["voice"]["detail"])
        self.assertIn("Une seule scène", by_key["transitions"]["detail"])

    def test_old_export_without_new_fields_remains_reviewable(self):
        data = manifest()
        del data["caption_timing"]
        del data["final_visual_review"]
        del data["speech_engine"]
        result = review(data)
        by_key = {x["key"]: x for x in result["checks"]}
        self.assertTrue(by_key["voice"]["attention"])
        self.assertTrue(by_key["captions"]["attention"])
        self.assertTrue(by_key["transitions"]["attention"])

    def test_bounded_warnings_cannot_become_html_or_unbounded_response(self):
        data = manifest()
        data["warnings"] = [
            '<img src=x onerror="alert(1)">\n' + "b" * 500
        ] * 100
        result = review(data)
        self.assertEqual(len(result["warnings"]), 6)
        self.assertEqual(result["warnings_total"], 100)
        self.assertTrue(all(len(x) <= 220 for x in result["warnings"]))
        self.assertNotIn("\n", result["warnings"][0])
        # This is plain text, not trusted HTML; browser uses textContent.
        self.assertTrue(result["warnings"][0].startswith("<img"))

    def test_untrusted_media_indices_cannot_fake_full_coverage(self):
        data = manifest()
        data["media"] = [{"segment": 1, "kind": "image"}] * 30 + [
            {"segment": True, "kind": "image"},
            {"segment": 999, "kind": "image"},
            {"segment": 2, "kind": "unknown"}]
        result = review(data)
        self.assertEqual(result["with_media"], 1)
        self.assertEqual(result["with_graphic_fallback"], 1)

    def test_missing_or_corrupt_manifest_is_not_an_approved_review(self):
        for bad in (None, "", 23, {}, {"segments": []},
                    {"segments": [{"start": 3, "end": 2}]},
                    {"segments": [{"start": True, "end": 1}]},
                    {"segments": [{"start": float("nan"), "end": 2}]}):
            with self.subTest(bad=bad):
                self.assertIsNone(review(bad))

    def test_public_job_review_only_after_completion_and_recovery(self):
        with tempfile.TemporaryDirectory() as work:
            jobs = Jobs(Path(work) / "output")
            job = {"id": "a" * 32, "status": "running",
                   "created": 123, "finished": None, "error": None,
                   "parameters": {"topic": "Sujet", "resolution": 360},
                   "metadata": manifest()}
            self.assertIsNone(jobs.public(job)["render_review"])
            job["status"] = "error"
            self.assertIsNone(jobs.public(job)["render_review"])
            job["status"] = "cancelled"
            self.assertIsNone(jobs.public(job)["render_review"])
            job["status"] = "done"
            expected = review(manifest())
            self.assertEqual(jobs.public(job)["render_review"], expected)
            self.assertNotIn("output", jobs.public(job)["render_review"])
            self.assertLess(len(json.dumps(jobs.public(job)["render_review"])), 4500)


if __name__ == "__main__":
    unittest.main()
