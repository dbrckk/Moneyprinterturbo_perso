"""Production-facing social publication text, tested with real narrated MP4s."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

import free_studio
from social_publish import prepare_publication, render_publication_text


class SocialPublishPolicyTests(unittest.TestCase):
    def sample(self, *, lang="fr", aspect="9:16", topic="Les paysages de France"):
        return prepare_publication(
            topic, ["La forêt se réveille au lever du jour.",
                    "Le vent traverse la vallée."], lang=lang, aspect=aspect)

    def test_reels_tiktok_shorts_derive_only_actual_narration(self):
        result = self.sample()
        self.assertEqual(result["filename"], "video.publish.txt")
        self.assertFalse(result["ready_for_publication"])
        self.assertEqual(result["generated_from"], "actual-narration-and-topic-only")
        self.assertEqual(set(result["platforms"]),
                         {"instagram_reels", "tiktok", "youtube_shorts"})
        for platform in result["platforms"].values():
            self.assertIn("La forêt se réveille", platform["description"])
            self.assertNotIn("garanti", platform["description"])
            self.assertTrue(all(tag.startswith("#") for tag in platform["hashtags"]))
            self.assertLessEqual(len(platform["hashtags"]), 6)
        self.assertLessEqual(len(result["platforms"]["youtube_shorts"]["title"]), 100)
        text = render_publication_text(result)
        self.assertIn("INSTAGRAM REELS", text)
        self.assertIn("YOUTUBE SHORTS", text)
        self.assertIn("TIKTOK", text)
        self.assertIn("video.credits.txt", text)
        self.assertIn("Aucune publication", text)
        self.assertNotIn("fichier personnel", text)

    def test_landscape_labels_youtube_video_not_short(self):
        result = self.sample(aspect="16:9")
        self.assertIn("youtube_video", result["platforms"])
        self.assertNotIn("youtube_shorts", result["platforms"])
        self.assertNotIn("#Shorts", result["platforms"]["youtube_video"]["title"])
        self.assertIn("YOUTUBE VIDÉO", render_publication_text(result))
        self.assertTrue(any("vertical" in note for note
                            in result["human_review_required"]))

    def test_six_supported_languages_generate_uninvented_editorial_copy(self):
        for lang in ("fr", "en", "es", "de", "it", "pt"):
            with self.subTest(lang=lang):
                result = self.sample(lang=lang)
                self.assertIn("Le vent traverse", result["platforms"]["youtube_shorts"]["description"])
                self.assertGreater(len(render_publication_text(result)), 200)

    def test_long_user_topic_and_script_are_bounded_and_cleaned(self):
        result = self.sample(
            topic="Découvrir une activité extrême : " + "très interessant " * 6)
        self.assertLessEqual(len(result["platforms"]["youtube_shorts"]["title"]), 100)
        result = prepare_publication(
            "Une magnifique forêt\net les lacs", ["Lumière douce.\n\nSuite."],
            lang="fr", aspect="9:16")
        for platform in result["platforms"].values():
            for tag in platform["hashtags"]:
                self.assertRegex(tag, r"^#[A-Za-z][A-Za-z0-9]*$")
            self.assertNotIn("\n\n\n", platform["description"])
        self.assertNotIn("None", render_publication_text(result))

    def test_broken_inputs_rejected_without_external_services(self):
        for topic, scenes, lang, aspect in (
            ("x", ["Bonjour"], "fr", "9:16"),
            ("Sujet", [], "fr", "9:16"),
            ("Sujet", ["ok"], "nl", "9:16"),
            ("Sujet", ["ok"], "fr", "4:3"),
            ("Sujet", [""], "fr", "9:16"),
            ("Sujet", [3], "fr", "9:16"),
            ("Sujet", ["a" * 501], "fr", "9:16"),
            ("Sujet", ["a"] * 19, "fr", "9:16"),
        ):
            with self.subTest(topic=topic, scenes=scenes, lang=lang, aspect=aspect):
                with self.assertRaises(ValueError):
                    prepare_publication(topic, scenes, lang=lang, aspect=aspect)

    def test_hashtags_never_include_control_characters_or_invalid_urls(self):
        kit = self.sample(topic="Visitez https://example.org/a?token=secret ou la forêt!")
        tags = kit["platforms"]["tiktok"]["hashtags"]
        self.assertTrue(all(len(tag) <= 22 for tag in tags))
        self.assertFalse(any("//" in tag or "?" in tag or "secret" in tag.lower()
                             for tag in tags))


@unittest.skipUnless(all(shutil.which(p) for p in ("ffmpeg", "ffprobe")),
                     "FFmpeg/FFprobe required")
class SocialPublishRealMP4Tests(unittest.TestCase):
    def test_every_actual_render_creates_same_narration_publication_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "video.mp4"
            info = free_studio.render(
                "Les paysages de France",
                "La forêt se réveille. Une rivière traverse la vallée.",
                output, lang="fr", fps=12, resolution=360,
                mute=True, caption_style="classic")
            sidecar = output.with_suffix(".publish.txt")
            self.assertTrue(sidecar.is_file())
            self.assertGreater(sidecar.stat().st_size, 180)
            script = [item["text"] for item in info["segments"]]
            self.assertEqual(script, ["La forêt se réveille.", "Une rivière traverse la vallée."])
            self.assertIn(script[0], sidecar.read_text(encoding="utf-8"))
            self.assertIn("TIKTOK", sidecar.read_text(encoding="utf-8"))
            self.assertFalse(info["publication"]["ready_for_publication"])
            self.assertEqual(info["publication"]["filename"], sidecar.name)
            stored = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(stored["publication"], info["publication"])
            self.assertTrue(output.with_suffix(".credits.txt").is_file())

    def test_second_export_replaces_publication_without_stale_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "video.mp4"
            free_studio.render("Montagnes", "Une montagne magnifique.", output,
                               fps=12, resolution=360, mute=True,
                               caption_style="classic")
            first = output.with_suffix(".publish.txt").read_text(encoding="utf-8")
            free_studio.render("Océan", "La mer devient calme.", output,
                               fps=12, resolution=360, mute=True,
                               caption_style="classic")
            last = output.with_suffix(".publish.txt").read_text(encoding="utf-8")
            self.assertNotEqual(first, last)
            self.assertIn("La mer devient calme.", last)
            self.assertNotIn("Une montagne magnifique.", last)
            self.assertTrue(output.with_suffix(".mp4").is_file())


if __name__ == "__main__":
    unittest.main()
