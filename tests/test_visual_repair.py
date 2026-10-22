"""Post-encode cut/fade repair must be guarded and independently verified."""
from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import final_scene_review
import free_studio
from media_assets import MediaAsset
from visual_repair import repairable_boundary, improved_review, encoded_duration_ok


def media(name: str, *, kind: str = "video", url: str = "local") -> MediaAsset:
    return MediaAsset(Path(name), kind, url, "user-provided", "user", name)


def record(number: int, transition: str, grade: str, findings: list[str]) -> dict:
    return dict(boundary=number, transition=transition,
                review_confidence=grade, findings=findings)


def report(*records: dict, missing: int = 0, skipped: int = 0) -> dict:
    return dict(boundaries=list(records), sampled_count=len(records),
                samples_unavailable=missing, boundaries_skipped=skipped,
                warnings=[])


class RepairDecisionTests(unittest.TestCase):
    def setUp(self):
        self.assets = [media("a.mp4"), media("b.mp4")]

    def test_only_one_real_local_cut_high_confidence_flash_is_eligible(self):
        for kind in ("unexpected-black-flash", "unexpected-bright-flash"):
            with self.subTest(kind=kind):
                initial = report(record(1, "cut", "high", [kind]))
                self.assertEqual(repairable_boundary(
                    initial, ("cut",), self.assets), 0)
                after = report(record(1, "fade", "none", []))
                self.assertTrue(improved_review(initial, after, 0))
        all_fades = report(record(1, "fade", "high", ["unexpected-bright-flash"]))
        self.assertIsNone(repairable_boundary(all_fades, ("fade",), self.assets))
        poor = report(record(1, "cut", "low", ["large-exposure-change"]))
        self.assertIsNone(repairable_boundary(poor, ("cut",), self.assets))
        for m in (1, 2):
            self.assertIsNone(repairable_boundary(
                report(record(1, "cut", "high", ["unexpected-bright-flash"]),
                       missing=m if m == 1 else 0, skipped=1 if m == 2 else 0),
                ("cut",), self.assets))

    def test_never_change_explicit_or_remote_photo_or_unavailable_boundaries(self):
        original = report(record(1, "cut", "high", ["unexpected-black-flash"]))
        for assets in ((media("a.mp4", url="https://remote"), self.assets[1]),
                       (self.assets[0], media("b.jpg", kind="image")),
                       (self.assets[0], None)):
            self.assertIsNone(repairable_boundary(
                original, ("cut",), assets))
        self.assertIsNone(repairable_boundary(
            original, ("cut",), [media("a.mp4"), media("b.mp4"),
                                 media("c.mp4"), media("d.mp4"), media("e.mp4")]))
        self.assertIsNone(repairable_boundary(None, ("cut",), self.assets))
        self.assertIsNone(repairable_boundary(original, ("fade",), self.assets))

    def test_multiple_or_other_high_alerts_never_trigger_blind_repair(self):
        assets = [media("1.mp4"), media("2.mp4"), media("3.mp4")]
        alerts = report(
            record(1, "cut", "high", ["unexpected-bright-flash"]),
            record(2, "cut", "high", ["unexpected-black-flash"]))
        self.assertIsNone(repairable_boundary(alerts, ("cut", "cut"), assets))
        alerts = report(
            record(1, "cut", "high", ["unexpected-bright-flash"]),
            record(2, "fade", "high", ["unexpected-bright-flash"]))
        self.assertIsNone(repairable_boundary(alerts, ("cut", "fade"), assets))

    def test_poorer_or_incomplete_reencoded_result_is_rejected(self):
        start = report(record(1, "cut", "high", ["unexpected-bright-flash"]))
        for new in (
            report(record(1, "fade", "high", ["unexpected-bright-flash"])),
            report(record(1, "fade", "medium", ["abrupt-change-during-fade"])),
            report(record(1, "fade", "none", []), missing=1),
            report(record(1, "fade", "none", []), skipped=1),
            report(record(1, "cut", "none", [])),
            report(record(2, "fade", "none", [])),
        ):
            # Only a full, lower-risk re-scan with the expected transition
            # is acceptable; a metric decrease cannot hide poor timing.
            self.assertFalse(improved_review(start, new, 0), new)
        self.assertTrue(improved_review(
            start, report(record(1, "fade", "low", ["large-exposure-change"])), 0))
        before = report(record(1, "cut", "high", ["unexpected-bright-flash"]),
                        record(2, "cut", "none", []))
        after = report(record(1, "fade", "none", []),
                       record(2, "fade", "medium", ["abrupt-change-during-fade"]))
        self.assertFalse(improved_review(before, after, 0))

    def test_ffprobe_duration_verification_refuses_nonexistent_or_invalid(self):
        self.assertFalse(encoded_duration_ok(Path("missing.mp4"),
                                             expected=4.0, fps=12))
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = Path(tmp) / "corrupt.mp4"
            corrupt.write_bytes(b"x" * 3000)
            self.assertFalse(encoded_duration_ok(corrupt, expected=4.0, fps=12))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real encoded MP4 verification needs FFmpeg")
class RealTransitionRepairTests(unittest.TestCase):
    @staticmethod
    def fixture(target: Path, color: str) -> None:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c={color}:s=180x320:r=12:d=6",
             "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)],
            capture_output=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr[-1000:])

    def test_real_encoded_candidate_accepted_without_losing_duration_or_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "uploads"
            folder.mkdir()
            self.fixture(folder / "one.mp4", "0x747474")
            self.fixture(folder / "two.mp4", "0x898989")
            original_cut_warning = report(
                record(1, "cut", "high", ["unexpected-bright-flash"]))
            real_qa = final_scene_review.inspect_final_boundaries

            def qa_after_render(path, segments, transitions):
                if transitions[0] == "cut":
                    return original_cut_warning
                return real_qa(path, segments, transitions)

            target = root / "finished.mp4"
            with patch.object(free_studio, "choose_boundaries",
                              return_value=(("cut",), [])), patch.object(
                    free_studio, "inspect_final_boundaries",
                    side_effect=qa_after_render):
                meta = free_studio.render(
                    "Deux scènes", "Premier paysage magnifique. "
                    "Deuxième paysage magnifique.", target,
                    media_dir=folder, mute=True,
                    resolution=360, fps=12, caption_style="classic",
                    visual_style="cinematic", transition="auto")
            self.assertEqual(meta["scene_transitions"], ["fade"])
            self.assertEqual(meta["render_performance"]["postencode_transition_repair"],
                             "accepted")
            self.assertEqual(meta["final_visual_review"]["boundaries"][0]
                             ["review_confidence"] if
                             "review_confidence" in meta["final_visual_review"]["boundaries"][0]
                             else "none", "none")
            self.assertTrue(encoded_duration_ok(
                target, expected=meta["duration_seconds"], fps=12))
            self.assertEqual(json.loads(target.with_suffix(".json").read_text())
                             ["scene_transitions"], ["fade"])
            self.assertEqual(meta["final_visual_review"]["samples_unavailable"], 0)

    @unittest.skipUnless(shutil.which("espeak"), "Real local narration needed")
    def test_repaired_spoken_mp4_keeps_original_aac_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "uploads"
            media.mkdir()
            self.fixture(media / "a.mp4", "0x818181")
            self.fixture(media / "b.mp4", "0x848484")
            actual_review = final_scene_review.inspect_final_boundaries

            def report_original_then_real(path, segments, transitions):
                if transitions[0] == "cut":
                    baseline = actual_review(path, segments, transitions)
                    fake = [dict(row) for row in baseline["boundaries"]]
                    fake[0]["review_confidence"] = "high"
                    fake[0]["findings"] = ["unexpected-bright-flash"]
                    return dict(baseline, boundaries=fake)
                return actual_review(path, segments, transitions)

            output = root / "spoken.mp4"
            with patch.object(free_studio, "choose_boundaries",
                              return_value=(("cut",), [])), patch.object(
                    free_studio, "inspect_final_boundaries",
                    side_effect=report_original_then_real):
                result = free_studio.render(
                    "Présentation", "Première séquence avec une voix claire. "
                    "Deuxième séquence accompagnée de narration.",
                    output, media_dir=media, mute=False, tts="espeak",
                    resolution=360, fps=12, caption_style="classic",
                    visual_style="cinematic", transition="auto")
            self.assertEqual(result["render_performance"]
                             ["postencode_transition_repair"], "accepted")
            self.assertEqual(result["render_performance"]
                             ["repair_audio_integrity"], "aac-payload-sha256")
            self.assertEqual(result["scene_transitions"], ["fade"])
            metadata = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name", "-of", "json", str(output)],
                capture_output=True, text=True, check=True, timeout=20)
            streams = json.loads(metadata.stdout)["streams"]
            self.assertEqual([s["codec_name"] for s in streams
                              if s["codec_type"] == "audio"], ["aac"])

    def test_other_scene_pixel_drift_rejects_repaired_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "uploads"
            folder.mkdir()
            self.fixture(folder / "one.mp4", "0x747474")
            self.fixture(folder / "two.mp4", "0x898989")
            high = report(record(1, "cut", "high", ["unexpected-bright-flash"]))
            clean = report(record(1, "fade", "none", []))
            with patch.object(free_studio, "choose_boundaries",
                              return_value=(("cut",), [])), patch.object(
                    free_studio, "inspect_final_boundaries",
                    side_effect=[high, clean]), patch.object(
                    free_studio, "unchanged_scene_integrity",
                    return_value={
                        "ok": False, "sampled_frames": 2,
                        "worst_mae": 12.5,
                        "worst_changed_fraction": 0.12
                    }), patch.object(
                    free_studio, "encoded_duration_ok", return_value=True):
                meta = free_studio.render(
                    "Deux scènes", "Premier paysage magnifique. "
                    "Deuxième paysage magnifique.", root / "kept.mp4",
                    media_dir=folder, mute=True, resolution=360, fps=12,
                    caption_style="classic", visual_style="cinematic",
                    transition="auto")
            self.assertEqual(meta["scene_transitions"], ["cut"])
            perf = meta["render_performance"]
            self.assertEqual(perf["postencode_transition_repair"], "rejected")
            self.assertEqual(perf["repair_scene_integrity"], "rejected")
            self.assertEqual(perf["repair_scene_samples"], 2)
            self.assertTrue((root / "kept.mp4").is_file())

    def test_failing_encoded_duration_rejects_candidate_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "uploads"
            folder.mkdir()
            self.fixture(folder / "one.mp4", "0x747474")
            self.fixture(folder / "two.mp4", "0x898989")
            high = report(record(1, "cut", "high", ["unexpected-bright-flash"]))
            clean = report(record(1, "fade", "none", []))
            with patch.object(free_studio, "choose_boundaries",
                              return_value=(("cut",), [])), patch.object(
                    free_studio, "inspect_final_boundaries",
                    side_effect=[high, clean]), patch.object(
                    free_studio, "encoded_duration_ok", return_value=False):
                meta = free_studio.render(
                    "Deux scènes", "Premier paysage magnifique. "
                    "Deuxième paysage magnifique.", root / "kept.mp4",
                    media_dir=folder, mute=True,
                    resolution=360, fps=12, caption_style="classic",
                    visual_style="cinematic", transition="auto")
            self.assertEqual(meta["scene_transitions"], ["cut"])
            self.assertEqual(meta["render_performance"]["postencode_transition_repair"],
                             "rejected")
            self.assertTrue((root / "kept.mp4").is_file())
            self.assertIn("unexpected-bright-flash",
                          meta["final_visual_review"]["boundaries"][0]["findings"])


if __name__ == "__main__":
    unittest.main()
