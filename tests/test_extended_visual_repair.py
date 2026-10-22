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
from visual_repair import (encoded_duration_ok, improved_review,
                           repairable_boundary, targeted_review_improved)


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

    def test_target_only_qa_precheck_requires_single_improved_edited_boundary(self):
        initial_rows = [
            dict(boundary=i, transition="cut" if i == 2 else "fade",
                 review_confidence="high" if i == 2 else "none",
                 findings=["unexpected-black-flash"] if i == 2 else [])
            for i in range(1, 5)]
        before = dict(boundaries=initial_rows,
                      samples_unavailable=0, boundaries_skipped=0)
        changed = dict(boundary=2, transition="fade",
                       review_confidence="none", findings=[])
        partial = dict(boundaries=[changed], samples_unavailable=0,
                       boundaries_skipped=3)
        self.assertTrue(targeted_review_improved(before, partial, 1))
        for candidate in (
            dict(partial, samples_unavailable=1),
            dict(partial, boundaries_skipped=0),
            dict(partial, boundaries=[dict(changed, review_confidence="medium")]),
            dict(partial, boundaries=[dict(changed, transition="cut")]),
            dict(partial, boundaries=[dict(changed, boundary=3)]),
            dict(partial, boundaries=[])):
            with self.subTest(preview=candidate):
                self.assertFalse(targeted_review_improved(before, candidate, 1))
        self.assertFalse(targeted_review_improved(before, partial, 0))

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

            def staged_review(path, segments, transitions, *,
                              selected_boundaries=None):
                verified = actual_qa(
                    path, segments, transitions,
                    selected_boundaries=selected_boundaries)
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
            self.assertEqual(result["render_performance"]["repair_target_precheck"],
                             "passed")
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
            # Remuxing copies exactly the same compressed AAC payload.
            remux = root / "remux.mp4"
            run_ffmpeg("-i", str(spoken), "-map", "0:v:0", "-map", "0:a:0",
                       "-c", "copy", str(remux))
            self.assertTrue(encoded_duration_ok(
                remux, expected=3.0, fps=12, reference=spoken))
            # An AAC tone with matching channels/sample-rate/duration but
            # different compressed samples must NOT pass integrity checks.
            altered = root / "altered.mp4"
            run_ffmpeg(
                "-f", "lavfi", "-i",
                "color=c=0x777777:s=180x320:r=12:d=3",
                "-f", "lavfi", "-i",
                "sine=frequency=660:sample_rate=48000:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-t", "3.0", str(altered))
            self.assertTrue(encoded_duration_ok(
                altered, expected=3.0, fps=12))
            self.assertFalse(encoded_duration_ok(
                altered, expected=3.0, fps=12, reference=spoken))


if __name__ == "__main__":
    unittest.main()
