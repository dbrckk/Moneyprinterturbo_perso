"""Prove guarded post-encode fade retries with normalized 5-scene FFmpeg clips."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import final_scene_review
import free_studio
from media_assets import MediaAsset
from visual_repair import encoded_duration_ok, improved_review, repairable_boundary


def run_ffmpeg(*arguments: str) -> None:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         *arguments], capture_output=True, timeout=70)
    if process.returncode:
        raise AssertionError(process.stderr[-1800:])


class ExtendedRepairRulesTests(unittest.TestCase):
    def test_one_alert_can_be_repaired_in_eight_scenes(self):
        assets = [MediaAsset(Path(f"scene{i}.mp4"), "video", "local",
                 "user-provided", "user", str(i)) for i in range(8)]
        old_records = [dict(boundary=i, transition="cut" if i == 3 else "fade",
                            review_confidence="high" if i == 3 else "none",
                            findings=["unexpected-bright-flash"] if i == 3 else [])
                       for i in range(1, 8)]
        before = dict(boundaries=old_records, samples_unavailable=0,
                      boundaries_skipped=0)
        transitions = tuple(row["transition"] for row in old_records)
        self.assertEqual(repairable_boundary(before, transitions, assets), 2)
        updated = [dict(row) for row in old_records]
        updated[2] = dict(updated[2], review_confidence="none", transition="fade",
                          findings=[])
        after = dict(before, boundaries=updated)
        self.assertTrue(improved_review(before, after, 2))
        swapped_other = [dict(row) for row in updated]
        swapped_other[4]["transition"] = "cut"
        self.assertFalse(improved_review(
            before, dict(after, boundaries=swapped_other), 2))
        reordered = [dict(row) for row in old_records]
        reordered[3]["boundary"] = 3
        self.assertIsNone(repairable_boundary(
            dict(before, boundaries=reordered), transitions, assets))

    def test_nine_scene_cohorts_are_not_auto_repaired(self):
        assets = [MediaAsset(Path(f"c{i}.mp4"), "video", "local",
                 "user-provided", "user", str(i)) for i in range(9)]
        report = dict(boundaries=[
            dict(boundary=i, transition="cut" if i == 1 else "fade",
                 review_confidence="high" if i == 1 else "none",
                 findings=["unexpected-black-flash"] if i == 1 else [])
            for i in range(1, 9)], samples_unavailable=0, boundaries_skipped=0)
        self.assertIsNone(repairable_boundary(
            report, ("cut",) + ("fade",) * 7, assets))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Needs actual FFmpeg H.264/AAC encodes")
class ExtendedRepairRealMP4Tests(unittest.TestCase):
    def test_real_five_scene_fused_render_reuses_clips_for_verified_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "local"
            local.mkdir()
            initial_bytes = {}
            for index in range(5):
                filename = local / f"clip-{index+1:02}.mp4"
                run_ffmpeg("-f", "lavfi", "-i",
                           "color=c=0x808080:s=180x320:r=12:d=4.2",
                           "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           str(filename))
                initial_bytes[filename.name] = filename.read_bytes()
            actual_qa = final_scene_review.inspect_final_boundaries

            def staged_review(path, segments, transitions):
                verified = actual_qa(path, segments, transitions)
                if transitions[0] == "cut":
                    self.assertEqual(verified["sampled_count"], 4)
                    modified = [dict(value) for value in verified["boundaries"]]
                    modified[0]["review_confidence"] = "high"
                    modified[0]["findings"] = ["unexpected-bright-flash"]
                    return dict(verified, boundaries=modified)
                return verified

            target = root / "export.mp4"
            with patch.object(free_studio, "choose_boundaries",
                              return_value=(("cut", "fade", "fade", "fade"), [])):
                with patch.object(free_studio, "inspect_final_boundaries",
                                  side_effect=staged_review):
                    result = free_studio.render(
                        "Montage long",
                        "Première scène. Deuxième scène. Troisième scène. "
                        "Quatrième scène. Cinquième scène.",
                        target, media_dir=local, mute=True, resolution=360,
                        fps=12, caption_style="classic",
                        visual_style="cinematic", transition="auto")
            self.assertEqual(result["scene_transitions"],
                             ["fade", "fade", "fade", "fade"])
            self.assertEqual(result["render_performance"]["compositing_pipeline"],
                             "fused-xfade")
            self.assertEqual(result["render_performance"]["postencode_transition_repair"],
                             "accepted")
            self.assertEqual(
                result["render_performance"]["postencode_transition_repair_pipeline"],
                "reused-normalized-scenes")
            self.assertEqual(result["final_visual_review"]["sampled_count"], 4)
            self.assertTrue(encoded_duration_ok(
                target, expected=result["duration_seconds"], fps=12))
            self.assertEqual(json.loads(target.with_suffix(".json").read_text())
                             ["scene_transitions"], ["fade"] * 4)
            for filename in local.iterdir():
                self.assertEqual(filename.read_bytes(), initial_bytes[filename.name])

    def test_repaired_file_cannot_drop_narration_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video_only = root / "silent.mp4"
            spoken = root / "with-audio.mp4"
            run_ffmpeg(
                "-f", "lavfi", "-i",
                "color=c=0x777777:s=180x320:r=12:d=3",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-t", "3.0", str(video_only))
            run_ffmpeg(
                "-f", "lavfi", "-i",
                "color=c=0x777777:s=180x320:r=12:d=3",
                "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-t", "3.0", str(spoken))
            self.assertTrue(encoded_duration_ok(
                video_only, expected=3.0, fps=12))
            self.assertTrue(encoded_duration_ok(
                spoken, expected=3.0, fps=12))
            self.assertFalse(encoded_duration_ok(
                video_only, expected=3.0, fps=12, reference=spoken))
            self.assertFalse(encoded_duration_ok(
                spoken, expected=3.0, fps=12, reference=video_only))
            self.assertFalse(encoded_duration_ok(
                spoken, expected=61.0, fps=12))


if __name__ == "__main__":
    unittest.main()
