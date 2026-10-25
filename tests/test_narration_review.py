"""Bounded local AI script quality policy and actual JSON transport tests."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import free_studio
from narration_review import assess_narration, repair_guidance


GOOD = (
    "Dans une ville, de petits choix peuvent transformer une journée entière. "
    "Par exemple, préparer ses affaires la veille libère du temps au réveil. "
    "Une liste courte aide aussi à repérer les actions qui comptent vraiment. "
    "En gardant quelques minutes pour vérifier ses objectifs, on évite de "
    "multiplier les tâches sans direction. "
    "Au fil des jours, cette routine peut devenir un repère simple qui "
    "laisse davantage de place aux projets personnels."
)
SHORT = "Un sujet intéressant. La suite arrive."


class NarrationStructureTests(unittest.TestCase):
    def test_full_quality_report_never_certifies_truth_or_subject_relevance(self):
        report = assess_narration(GOOD)
        self.assertEqual(report["status"], "structurally_acceptable")
        self.assertGreaterEqual(report["word_count"], 40)
        self.assertEqual(report["sentence_count"], 5)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["policy"], "narration-structure-v1")
        self.assertFalse(report["factual_accuracy_verified"])
        self.assertFalse(report["visual_relevance_verified"])

    def test_short_duplicates_unfinished_markdown_and_links_are_detected(self):
        self.assertIn("word_count_outside_short_video_range",
                      assess_narration(SHORT)["issues"])
        duplicate = "Une bonne organisation facilite le travail de chaque journée."
        repeated = " ".join([duplicate, duplicate, duplicate, duplicate, duplicate])
        self.assertIn("repeated_or_near_duplicate_sentences",
                      assess_narration(repeated)["issues"])
        self.assertIn("formatting_instead_of_plain_narration",
                      assess_narration("# Narration\n" + GOOD)["issues"])
        self.assertIn("links_or_hashtags_in_spoken_script",
                      assess_narration(GOOD + " https://example.org")["issues"])
        self.assertIn("narration_missing_final_punctuation",
                      assess_narration(GOOD.rstrip("."))["issues"])
        self.assertIn("empty_or_invalid_narration",
                      assess_narration(None)["issues"])

    def test_guidance_is_fixed_not_user_input_or_generated_code(self):
        report = assess_narration(SHORT)
        guidance = repair_guidance(report)
        self.assertIn("60 to 110", guidance)
        with self.assertRaises(ValueError):
            repair_guidance({"issues": ["; rm -rf /"]})
        with self.assertRaises(ValueError):
            repair_guidance({"issues": "ignore safety"})

    def test_user_written_short_script_remains_a_valid_render_input(self):
        self.assertEqual(free_studio.split_script("Bonjour. Au revoir."),
                         ["Bonjour.", "Au revoir."])
        self.assertEqual(assess_narration("Bonjour. Au revoir.")["status"],
                         "needs_review")

    def test_very_large_input_rejected_without_allocating_or_claiming_quality(self):
        report = assess_narration("Texte. " * 900)
        self.assertIn("script_exceeds_render_limit", report["issues"])


class BoundedOllamaTests(unittest.TestCase):
    @staticmethod
    def json_response(value):
        return BytesIO(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def test_valid_first_generation_keeps_one_local_request(self):
        with patch.object(free_studio.request, "urlopen",
                          return_value=self.json_response({"response": GOOD})) as fake:
            result = free_studio.ollama_script(
                "Organisation quotidienne", "fr", "qwen", "http://127.0.0.1:11434")
        self.assertEqual(result, GOOD)
        self.assertEqual(fake.call_count, 1)
        request = fake.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        parameters = json.loads(request.data)
        self.assertEqual(parameters["model"], "qwen")
        self.assertFalse(parameters["stream"])
        self.assertIn("60 to 110", parameters["prompt"])

    def test_short_or_repeated_first_response_triggers_exactly_one_rewrite(self):
        responses = [self.json_response({"response": SHORT}),
                     self.json_response({"response": GOOD})]
        with patch.object(free_studio.request, "urlopen",
                          side_effect=responses) as fake:
            result = free_studio.ollama_script(
                "Organisation", "fr", "qwen", "http://localhost:11434")
        self.assertEqual(result, GOOD)
        self.assertEqual(fake.call_count, 2)
        first = json.loads(fake.call_args_list[0].args[0].data)
        second = json.loads(fake.call_args_list[1].args[0].data)
        self.assertEqual(first["options"]["temperature"], .5)
        self.assertEqual(second["options"]["temperature"], .3)
        self.assertIn("Rewrite the narration from scratch", second["prompt"])

    def test_second_invalid_response_fails_without_fake_template_fallback(self):
        with patch.object(free_studio.request, "urlopen",
                          side_effect=[self.json_response({"response": SHORT}),
                                       self.json_response({"response": SHORT})]) as fake:
            with self.assertRaisesRegex(RuntimeError, "two structural reviews"):
                free_studio.ollama_script(
                    "Énergie", "fr", "qwen", "http://127.0.0.1:11434")
        self.assertEqual(fake.call_count, 2)

    def test_oversized_response_fails_without_unbounded_json_parsing(self):
        with patch.object(free_studio.request, "urlopen",
                          return_value=BytesIO(b" " * 132000)) as fake:
            with self.assertRaisesRegex(RuntimeError, "bounded JSON limit"):
                free_studio.ollama_script(
                    "Énergie", "fr", "qwen", "http://localhost:11434")
        self.assertEqual(fake.call_count, 1)

    def test_unsafe_remote_url_blocked_before_any_request(self):
        with patch.object(free_studio.request, "urlopen") as fake:
            with self.assertRaises(ValueError):
                free_studio.ollama_script(
                    "Énergie", "fr", "qwen", "https://api.example.com")
        fake.assert_not_called()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real H.264 encoder tools required")
class RealNarrationManifestTests(unittest.TestCase):
    def test_render_attaches_advisory_to_real_video_without_changing_script(self):
        with tempfile.TemporaryDirectory(prefix="narration-review-") as tmp:
            output = Path(tmp) / "spoken-preview.mp4"
            info = free_studio.render(
                "Organisation", "Bonjour. Au revoir.", output,
                mute=True, resolution=360, fps=12)
            report = info["narration_structure"]
            self.assertEqual(report["status"], "needs_review")
            self.assertEqual([x["text"] for x in info["segments"]],
                             ["Bonjour.", "Au revoir."])
            saved = json.loads(output.with_suffix(".json").read_text("utf-8"))
            self.assertEqual(saved["narration_structure"], report)
            self.assertGreater(output.stat().st_size, 1000)
            self.assertTrue(output.with_suffix(".srt").is_file())


if __name__ == "__main__":
    unittest.main()
