"""Free workflow quotas: invalid inputs should never reach heavy installs."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from unittest import TestCase
from unittest.mock import patch

from workflow_preflight import CHOICES, main, validate_request


BASE = {
    "TOPIC": "Les montagnes",
    "SCRIPT_TEXT": "La forêt est verte. La montagne est haute.",
    "SCRIPT_MODE": "template",
    "SCENE_QUERIES": "forêt|montagne",
    "LANG": "fr",
    "ASPECT": "9:16",
    "RESOLUTION": "720",
    "VISUAL_MODE": "commons",
    "VISUAL_AI": "off",
    "VOICE": "espeak",
    "SPEECH_RATE": "145",
    "VISUAL_STYLE": "cinematic",
    "CAPTION_STYLE": "kinetic",
    "WORD_ALIGNMENT": "estimated",
    "TRANSITION": "auto",
}


class WorkflowPreflightTests(TestCase):
    def test_normal_user_script_preflight(self):
        report = validate_request(BASE)
        self.assertEqual(report["scene_count"], 2)
        self.assertEqual(report["visuals"], "commons")
        self.assertFalse(report["needs_local_model"])

    def test_template_and_real_local_ai_are_valid_without_pretending_ai_was_used(self):
        without_narration = {**BASE, "SCRIPT_TEXT": "", "SCENE_QUERIES": ""}
        report = validate_request(without_narration)
        self.assertEqual(report["script_mode"], "template")
        self.assertGreaterEqual(report["scene_count"], 1)
        local_ai = validate_request({**without_narration, "SCRIPT_MODE": "local-ai"})
        self.assertTrue(local_ai["needs_local_model"])
        self.assertIsNone(local_ai["scene_count"])

    def test_selected_optional_workflows_pass_without_model_install(self):
        settings = {**BASE, "VOICE": "piper-fr-siwis",
                    "WORD_ALIGNMENT": "forced", "VISUAL_AI": "clip"}
        report = validate_request(settings)
        self.assertTrue(report["needs_piper"])
        self.assertTrue(report["needs_clip"])
        self.assertTrue(report["needs_whisper"])

    def test_workflow_choices_are_rejected_individually(self):
        for field in CHOICES:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                validate_request({**BASE, field: "unsupported"})
        with self.assertRaisesRegex(ValueError, "LANG"):
            validate_request({key: value for key, value in BASE.items() if key != "LANG"})

    def test_broken_narration_and_queries_are_rejected_early(self):
        cases = [
            ({"SCRIPT_TEXT": "x" * 5001}, "5000"),
            ({"SCRIPT_TEXT": "Salut. " * 19, "SCENE_QUERIES": ""}, "18"),
            ({"SCENE_QUERIES": "forêt"}, "Expected 2"),
            ({"SCENE_QUERIES": "forêt|"}, "Scene query 2"),
            ({"SCENE_QUERIES": "forêt|!!!"}, "Scene query 2"),
            ({"SCRIPT_TEXT": "", "SCENE_QUERIES": "forêt"}, "written narration"),
            ({"VISUAL_MODE": "abstract"}, "Per-scene searches"),
            ({"VISUAL_AI": "clip", "VISUAL_MODE": "abstract",
              "SCENE_QUERIES": ""}, "Visual CLIP"),
            ({"SCRIPT_MODE": "local-ai"}, "combined"),
            ({"VOICE": "piper-fr-siwis", "LANG": "en"}, "French only"),
            ({"WORD_ALIGNMENT": "forced", "CAPTION_STYLE": "classic"}, "kinetic"),
            ({"TOPIC": "A"}, "2"),
            ({"TOPIC": "Forest\nGone"}, "one line"),
            ({"TOPIC": "!!!"}, "searchable words"),
        ]
        for changes, fragment in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, fragment):
                validate_request({**BASE, **changes})

    def test_abstract_script_does_not_need_searchable_topic(self):
        settings = {**BASE, "TOPIC": "!!!", "VISUAL_MODE": "abstract",
                    "SCENE_QUERIES": ""}
        self.assertEqual(validate_request(settings)["scene_count"], 2)

    def test_valid_large_override_budget_and_explicit_18_scenes(self):
        settings = {**BASE, "SCRIPT_TEXT": "Hello. " * 18,
                    "SCENE_QUERIES": "|".join(["mountain"] * 18),
                    "LANG": "en"}
        report = validate_request(settings)
        self.assertEqual(report["scene_count"], 18)

    def test_cli_errors_never_echo_supplied_narration_or_secrets(self):
        bad = {**BASE, "SCRIPT_TEXT": "SENSITIVE NARRATION DO NOT LOG",
               "VISUAL_AI": "invalid", "PRIVATE_TOKEN": "TOPSECRET"}
        stderr = StringIO()
        with patch.dict(os.environ, bad, clear=True), redirect_stderr(stderr):
            self.assertEqual(main(), 2)
        self.assertIn("::error", stderr.getvalue())
        self.assertNotIn("SENSITIVE NARRATION", stderr.getvalue())
        self.assertNotIn("TOPSECRET", stderr.getvalue())
        stdout = StringIO()
        with patch.dict(os.environ, BASE, clear=True), redirect_stdout(stdout):
            self.assertEqual(main(), 0)
        self.assertIn("2 scenes", stdout.getvalue())
        self.assertNotIn(BASE["SCRIPT_TEXT"], stdout.getvalue())


if __name__ == "__main__":
    import unittest
    unittest.main()
