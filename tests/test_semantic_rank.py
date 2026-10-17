"""CLIP selection safety, stable no-ML default and graceful fallback."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import media_assets as media
from semantic_rank import choose_with_clip, english_visual_prompt, rerank


def candidate(root, name, *, coverage=1.0):
    target = root / (name + ".jpg")
    target.write_bytes(b"fixture")
    return media.MediaAsset(
        target, "image", f"https://commons.wikimedia.org/wiki/File:{name}.jpg",
        "CC0", "Example", name, query_term_count=2,
        query_term_coverage=coverage)


class SemanticRankingTests(unittest.TestCase):
    def test_only_known_visual_terms_translate_into_clip_prompts(self):
        self.assertEqual(english_visual_prompt("forêt neige"), "forest snow")
        self.assertEqual(english_visual_prompt("mountain bridge"), "mountain bridge")
        self.assertIsNone(english_visual_prompt("Henri Rivière peintre"))
        self.assertIsNone(english_visual_prompt("batterie rendement"))
        self.assertIsNone(english_visual_prompt(""))

    def test_confident_difference_can_choose_better_matching_image(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            assets = [candidate(root, "a"), candidate(root, "b")]
            updated, switched = choose_with_clip(assets, [0.20, 0.87])
            self.assertTrue(switched)
            self.assertEqual(updated[0].title, "b")
            self.assertEqual(updated[0].semantic_score, 0.87)
            self.assertEqual(assets[0].semantic_score, None)

    def test_cannot_swap_for_small_uncertain_or_lexically_weak_signal(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            best = candidate(root, "best")
            weaker = candidate(root, "weak", coverage=0.2)
            for scores in ([0.6, 0.7], [0.4, 0.48], [0.7, None]):
                reordered, switched = choose_with_clip([best, weaker], scores)
                self.assertFalse(switched)
                self.assertEqual(reordered[0].title, "best")
            reordered, switched = choose_with_clip([best, weaker], [0.52, 0.99])
            self.assertFalse(switched, "Textual evidence must remain a safeguard")
            self.assertEqual(reordered[0].title, "best")

    def test_worker_invoked_once_and_updates_all_eligible_candidates(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            assets = [candidate(root, "a"), candidate(root, "b")]
            results = {"forêt": (assets, [], {})}
            def mock_worker(argv, **kwargs):
                request = json.loads(Path(argv[-2]).read_text())
                self.assertEqual([row["prompt"] for row in request["candidates"]],
                                 ["forest", "forest"])
                Path(argv[-1]).write_text(json.dumps({
                    "model": "Xenova/clip-vit-base-patch32",
                    "scores": [{"id": 0, "score": 0.22}, {"id": 1, "score": 0.85}]
                }))
                return type("Result", (), {"returncode": 0})()
            with patch("semantic_rank.subprocess.run", side_effect=mock_worker) as runner:
                updated, stats, warnings = rerank(results, root)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(updated["forêt"][0][0].title, "b")
            self.assertEqual(stats["semantic_reordered_queries"], 1)
            self.assertEqual(stats["semantic_status"], "ok")
            self.assertEqual(warnings, [])
            self.assertFalse((root / "clip_input.json").exists())

    def test_worker_error_invalid_score_and_unknown_query_keep_safe_order(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            assets = [candidate(root, "a"), candidate(root, "b")]
            results = {"forêt": (assets, [], {})}
            with patch("semantic_rank.subprocess.run", side_effect=OSError("offline")):
                updated, stats, warnings = rerank(results, root)
            self.assertIs(updated, results)
            self.assertEqual(stats["semantic_status"], "fallback")
            self.assertTrue(any("retained" in warning for warning in warnings))
            with patch("semantic_rank.subprocess.run") as run:
                updated, stats, warnings = rerank({"Henri Rivière": (assets, [], {})}, root)
            run.assert_not_called()
            self.assertEqual(stats["semantic_status"], "skipped")
            self.assertEqual(warnings, [])

    def test_ordinary_fetch_does_not_invoke_clip_or_expand_download_budget(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            def lookup(query, path, *, limit, timings):
                return [candidate(path, str(n)) for n in range(limit)], []
            with patch.object(media, "commons_assets", side_effect=lookup) as get, patch.object(
                    media, "semantic_rerank") as clip:
                found, notices = media.commons_scene_assets(
                    ["forest", "mountain", "river"], root)
                self.assertEqual([call.kwargs["limit"] for call in get.call_args_list], [1, 1, 1])
                self.assertEqual(len(found), 3)
                self.assertEqual(notices, [])
                clip.assert_not_called()

    def test_opt_in_budget_is_bounded_and_keeps_repeated_topics_priority(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            def lookup(query, path, *, limit, timings):
                return [candidate(path, f"{query}-{n}") for n in range(limit)], []
            with patch.object(media, "commons_assets", side_effect=lookup) as get, patch.object(
                    media, "semantic_rerank", side_effect=lambda results, work: (
                        results, {"semantic_status": "ok", "semantic_reordered_queries": 0}, [])):
                stats = {}
                found, _ = media.commons_scene_assets(
                    ["forest", "river", "mountain", "sea"], root,
                    semantic_ranking=True, stats=stats)
            self.assertEqual(len(found), 4)
            # "sea" is not in the intentionally conservative prompt glossary:
            # no extra image download should be spent on an unsupported query.
            self.assertEqual(sum(call.kwargs["limit"] for call in get.call_args_list), 7)
            self.assertEqual(stats["image_variants_budget"], 7)
            self.assertEqual(stats["semantic_status"], "ok")


if __name__ == "__main__":
    unittest.main()
