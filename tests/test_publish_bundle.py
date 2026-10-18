"""Regression checks for publishing complete video deliverables safely."""
from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from publish_bundle import publish_bundle
import free_studio


class PublicationUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "scratch"
        self.work.mkdir()
        self.output = self.root / "public"
        self.output.mkdir()
        self.mp4 = self.output / "video.mp4"
        self.srt = self.output / "video.srt"
        self.json = self.output / "video.json"
        self.credits = self.output / "video.credits.txt"
        self.quality = self.output / "video.quality.json"
        self.storyboard = self.output / "video.storyboard.png"
        self.ass = self.output / "video.ass"
        self.alignment = self.output / "video.alignment.json"
        self.sources = {}
        for name, target in (("mp4", self.mp4), ("srt", self.srt),
                             ("json", self.json), ("credits", self.credits)):
            target.write_bytes(("old-" + name).encode())
            source = self.work / name
            source.write_bytes(("new-" + name).encode())
            self.sources[target] = source
        for obsolete in (self.quality, self.storyboard, self.ass, self.alignment):
            obsolete.write_bytes(b"old report data")
        self.obsolete = [self.quality, self.storyboard, self.ass, self.alignment]

    def test_all_artifacts_publish_and_expire_stale_reports(self):
        with patch("publish_bundle.os.replace", wraps=os.replace) as move:
            publish_bundle(self.sources, remove=self.obsolete)
        for target, source in self.sources.items():
            self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertFalse(any(path.exists() for path in self.obsolete))
        self.assertEqual(move.call_args_list[-1].args[1], self.mp4)
        self.assertFalse(list(self.output.glob(".mpt-publish-*")))

    def test_staging_error_never_touches_existing_export(self):
        with patch("publish_bundle.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                publish_bundle(self.sources, remove=self.obsolete)
        for name, target in (("mp4", self.mp4), ("srt", self.srt),
                             ("json", self.json), ("credits", self.credits)):
            self.assertEqual(target.read_bytes(), ("old-" + name).encode())
        self.assertTrue(all(p.exists() for p in self.obsolete))

    def test_mid_publish_failure_rolls_back_old_files_and_new_files(self):
        fresh = self.output / "video.alternative.txt"
        extra = self.work / "alternative"
        extra.write_bytes(b"new-only")
        proposed = {fresh: extra, **self.sources}
        real_replace = os.replace
        failures = [0]

        def fail_once(src, dst):
            if Path(dst) == self.credits and not failures[0]:
                failures[0] = 1
                raise OSError("simulated write failure")
            return real_replace(src, dst)

        with patch("publish_bundle.os.replace", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "simulated write failure"):
                publish_bundle(proposed, remove=self.obsolete)
        self.assertEqual(failures[0], 1)
        self.assertFalse(fresh.exists())
        for name, target in (("mp4", self.mp4), ("srt", self.srt),
                             ("json", self.json), ("credits", self.credits)):
            self.assertEqual(target.read_bytes(), ("old-" + name).encode())
        self.assertTrue(all(p.exists() for p in self.obsolete))
        self.assertFalse(list(self.output.glob(".mpt-publish-*")))

    def test_rejects_duplicate_paths_empty_sources_and_mixed_directories(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            publish_bundle(self.sources, remove=[self.srt])
        with self.assertRaisesRegex(ValueError, "same destination"):
            publish_bundle({self.mp4: self.sources[self.mp4],
                            self.root / "unrelated.mp4": self.sources[self.json]})
        self.sources[self.mp4].write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty staged"):
            publish_bundle(self.sources)
        self.assertEqual(self.mp4.read_bytes(), b"old-mp4")


@unittest.skipUnless(all(shutil.which(x) for x in ("ffmpeg", "ffprobe")),
                     "FFmpeg and ffprobe required")
class RendererPublicationTests(unittest.TestCase):
    def test_second_render_invalidates_former_qa_storyboard_and_kinetic_sidecars(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "video.mp4"
            free_studio.render("Parks", "Sunny countryside.", target,
                               resolution=360, fps=12, mute=True,
                               caption_style="kinetic")
            self.assertTrue(target.with_suffix(".ass").is_file())
            self.assertTrue(target.with_suffix(".alignment.json").is_file())
            for suffix in (".quality.json", ".storyboard.png"):
                target.with_suffix(suffix).write_bytes(b"stale")
            info = free_studio.render(
                "Parks", "A clean second render.", target,
                resolution=360, fps=12, mute=True, caption_style="classic")
            self.assertEqual(info["caption_style"], "classic")
            for suffix in (".quality.json", ".storyboard.png",
                           ".ass", ".alignment.json"):
                self.assertFalse(target.with_suffix(suffix).exists())
            self.assertIn("A clean second render.",
                          target.with_suffix(".srt").read_text(encoding="utf-8"))
            self.assertTrue(target.with_suffix(".credits.txt").is_file())
            self.assertFalse(list(root.glob(".mpt-publish-*")))


if __name__ == "__main__":
    unittest.main()
