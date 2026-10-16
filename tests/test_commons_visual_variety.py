"""Perceptual ranking without rejecting licensed but visually repeated images."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import media_assets as media


def asset(root, name, signature):
    return media.MediaAsset(root / f"{name}.jpg", "image",
            f"https://commons.wikimedia.org/wiki/File:{name}.jpg",
            "CC0", "Example", name, visual_fingerprint=signature)


class VisualDiversityTests(unittest.TestCase):
    def test_choice_prioritizes_different_visuals_even_if_urls_differ(self):
        def search(query, folder, *, limit, timings, **kwargs):
            return [asset(folder,"a",0xAA00AA00AA00AA00),
                    asset(folder,"b",0xAA00AA00AA00AA01),
                    asset(folder,"c",0x00FF00FF00FF00FF)][:limit], []
        with TemporaryDirectory() as folder, patch.object(
                media, "commons_assets", side_effect=search) as searcher:
            stats={}
            result, errors=media.commons_scene_assets(
                ["foret","foret","foret"],Path(folder),stats=stats,max_searches=1)
        self.assertEqual(searcher.call_count,1)
        self.assertEqual(result[0].title,"a")
        self.assertEqual(result[1].title,"c")
        self.assertEqual(result[2].title,"b")
        self.assertEqual(stats["near_duplicate_candidates_avoided"],1)
        self.assertEqual(stats["distinct_visual_patterns_estimated"],2)
        self.assertEqual(stats["scenes_with_perceptual_signature"],3)
        self.assertEqual(stats["unique_images_selected"],3)
        self.assertEqual(errors,[])

    def test_missing_signatures_preserve_url_ranking(self):
        def search(query, folder, *, limit, timings, **kwargs):
            return [asset(folder,"a",None),asset(folder,"b",None)][:limit], []
        with TemporaryDirectory() as folder, patch.object(
                media, "commons_assets", side_effect=search):
            stats={}
            result,_=media.commons_scene_assets(
                ["river","river"],Path(folder),stats=stats,max_searches=1)
        self.assertEqual([result[i].title for i in (0,1)],["a","b"])
        self.assertEqual(stats["scenes_with_perceptual_signature"],0)
        self.assertEqual(stats["distinct_visual_patterns_estimated"],0)


if __name__ == "__main__":
    unittest.main()
