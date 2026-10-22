"""Rendered-video visual boundary QA: not a substitute for artistic review."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import final_scene_review as qa
import free_studio
import video_qa


def ffmpeg(*args: str) -> None:
    process = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, timeout=65)
    if process.returncode:
        raise AssertionError(process.stderr[-1400:])


def scenes(*durations: float) -> list[dict]:
    position = 0.0
    output = []
    for d in durations:
        output.append({"start": position, "end": position + d, "text": "Scène"})
        position += d
    return output


class EncodedBoundaryUnitTests(unittest.TestCase):
    def test_validation_no_scenes_and_invalid_transition_metadata(self):
        with self.assertRaises(ValueError):
            qa.inspect_final_boundaries(Path("missing.mp4"), [], [])
        with self.assertRaises(ValueError):
            qa.inspect_final_boundaries(Path("missing.mp4"), scenes(2, 2), ["auto"])
        with self.assertRaises(ValueError):
            qa.inspect_final_boundaries(Path("missing.mp4"), scenes(2, 2), [])
        report = qa.inspect_final_boundaries(Path("missing.mp4"), scenes(3.0), [])
        self.assertEqual(report["boundaries"], [])
        self.assertEqual(report["samples_unavailable"], 0)

    def test_only_bounded_spread_boundaries_and_failing_samples_are_reported(self):
        many = scenes(*([2.0] * 18))
        values = Mock(return_value=tuple([110.0] * 12))
        with patch.object(qa, "sample_boundary_luminance", values):
            report = qa.inspect_final_boundaries(Path("missing.mp4"), many,
                                                 ["cut"] * 17)
        self.assertEqual(report["sampled_count"], 8)
        self.assertEqual(report["boundaries_skipped"], 9)
        self.assertEqual(report["boundaries"][0]["boundary"], 1)
        self.assertEqual(report["boundaries"][-1]["boundary"], 17)
        self.assertEqual(values.call_count, 8)
        self.assertEqual(report["warnings"], [])
        with patch.object(qa, "sample_boundary_luminance", return_value=None):
            missing = qa.inspect_final_boundaries(Path("missing.mp4"),
                                                  scenes(1.5, 1.5), ["fade"])
        self.assertEqual(missing["samples_unavailable"], 1)
        self.assertEqual(missing["sampled_count"], 0)
        self.assertTrue(any("unavailable" in w for w in missing["warnings"]))

    def test_encoded_flatness_and_abrupt_fade_flag_only_when_evident(self):
        clip = Path("does-not-need-to-exist.mp4")
        flat = (111.0,) * 12
        with patch.object(qa, "sample_boundary_luminance", return_value=flat):
            outcome = qa.inspect_final_boundaries(clip, scenes(2, 2), ["fade"])
        self.assertEqual(outcome["warnings"], [])
        self.assertEqual(outcome["boundaries"][0]["luma_delta"], 0)
        self.assertEqual(outcome["boundaries"][0]["review_confidence"], "none")
        self.assertFalse(outcome["boundaries"][0]["requires_manual_review"])
        abrupt = tuple([205.0] * 6 + [55.0] * 6)
        with patch.object(qa, "sample_boundary_luminance", return_value=abrupt):
            fade = qa.inspect_final_boundaries(clip, scenes(2, 2), ["fade"])
            cut = qa.inspect_final_boundaries(clip, scenes(2, 2), ["cut"])
        self.assertIn("abrupt-change-during-fade", fade["boundaries"][0]["findings"])
        self.assertNotIn("abrupt-change-during-fade", cut["boundaries"][0]["findings"])
        self.assertIn("large-exposure-change", cut["boundaries"][0]["findings"])
        self.assertEqual(fade["boundaries"][0]["review_confidence"], "medium")
        self.assertTrue(fade["boundaries"][0]["requires_manual_review"])
        self.assertEqual(cut["boundaries"][0]["review_confidence"], "low")
        self.assertFalse(cut["boundaries"][0]["requires_manual_review"])

    def test_bright_pulse_warning_requires_a_short_centered_spike(self):
        clip = Path("sample-only.mp4")
        flash = tuple([105.0] * 5 + [248.0] * 2 + [103.0] * 5)
        gradual = tuple(70.0 + 12.0 * index for index in range(12))
        lasting = tuple([90.0] * 6 + [250.0] * 6)
        with patch.object(qa, "sample_boundary_luminance", return_value=flash):
            result = qa.inspect_final_boundaries(clip, scenes(2, 2), ["cut"])
        record = result["boundaries"][0]
        self.assertIn("unexpected-bright-flash", record["findings"])
        self.assertEqual(record["review_confidence"], "high")
        self.assertTrue(record["requires_manual_review"])
        self.assertEqual(record["brief_bright_pulse_frames"], 2)
        self.assertTrue(any("very bright pulse" in w for w in result["warnings"]))
        with patch.object(qa, "sample_boundary_luminance", return_value=gradual):
            smooth = qa.inspect_final_boundaries(clip, scenes(2, 2), ["fade"])
        self.assertNotIn("unexpected-bright-flash", smooth["boundaries"][0]["findings"])
        self.assertNotIn("abrupt-change-during-fade", smooth["boundaries"][0]["findings"])
        with patch.object(qa, "sample_boundary_luminance", return_value=lasting):
            steady = qa.inspect_final_boundaries(clip, scenes(2, 2), ["cut"])
        self.assertNotIn("unexpected-bright-flash", steady["boundaries"][0]["findings"])

    def test_abrupt_fade_requires_a_dominant_step_at_edit_center(self):
        clip = Path("sample-only.mp4")
        center = tuple([205.0] * 6 + [55.0] * 6)
        early = tuple([205.0] * 4 + [55.0] * 8)
        gradual = (210., 210., 210., 210., 180., 145.,
                   115., 85., 60., 55., 55., 55.)
        for pixels, should_warn in ((center, True), (early, False),
                                    (gradual, False)):
            with self.subTest(pixels=pixels), patch.object(
                qa, "sample_boundary_luminance", return_value=pixels
            ):
                output = qa.inspect_final_boundaries(clip, scenes(2, 2), ["fade"])
            record = output["boundaries"][0]
            self.assertEqual("abrupt-change-during-fade" in record["findings"],
                             should_warn, record)
            self.assertGreaterEqual(record["max_step_fraction"], 0)
            self.assertLessEqual(record["max_step_fraction"], 1)
            self.assertEqual(record["dominant_step_near_boundary"],
                             should_warn)

    def test_intentional_fade_through_black_is_not_misreported_as_flash(self):
        # The same visible/dark/visible pixels are expected during a
        # deliberate fade through black, but suspicious for a hard cut.
        signal = tuple([200.0] * 4 + [2.0] * 4 + [195.0] * 4)
        with patch.object(qa, "sample_boundary_luminance", return_value=signal):
            fade = qa.inspect_final_boundaries(Path("example.mp4"),
                                               scenes(2, 2), ["fade"])
            cut = qa.inspect_final_boundaries(Path("example.mp4"),
                                              scenes(2, 2), ["cut"])
        self.assertNotIn("unexpected-black-flash", fade["boundaries"][0]["findings"])
        self.assertIn("unexpected-black-flash", cut["boundaries"][0]["findings"])

    def test_black_flash_requires_visible_sides_and_no_false_cut_rejection(self):
        signal = tuple([200.0] * 4 + [2.0] * 4 + [195.0] * 4)
        with patch.object(qa, "sample_boundary_luminance", return_value=signal):
            out = qa.inspect_final_boundaries(Path("example.mp4"),
                                              scenes(2, 2), ["cut"])
        self.assertEqual(out["boundaries"][0]["findings"],
                         ["unexpected-black-flash"])
        self.assertTrue(any("almost-black flash" in w for w in out["warnings"]))
        intentional_black_to_white = tuple([1.0] * 6 + [190.0] * 6)
        with patch.object(qa, "sample_boundary_luminance",
                          return_value=intentional_black_to_white):
            normal = qa.inspect_final_boundaries(Path("example.mp4"),
                                                 scenes(2, 2), ["cut"])
        self.assertEqual(normal["warnings"], [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg footage required")
class EncodedBoundaryRealMP4Tests(unittest.TestCase):
    def make_movie(self, path: Path, colors: tuple[tuple[str, float], ...]) -> None:
        command: list[str] = []
        for color, duration in colors:
            command.extend(["-f", "lavfi", "-i",
                            f"color=c={color}:s=160x288:r=12:d={duration}"])
        combine = "".join(f"[{i}:v]" for i in range(len(colors)))
        command += ["-filter_complex", combine +
                    f"concat=n={len(colors)}:v=1:a=0[v]",
                    "-map", "[v]", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)]
        ffmpeg(*command)

    def test_real_mp4_black_flash_between_visible_pictures_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "flash.mp4"
            self.make_movie(movie, (("white", 1.25), ("black", 0.5),
                                    ("white", 1.25)))
            review = qa.inspect_final_boundaries(movie, scenes(1.5, 1.5),
                                                 ["cut"])
            self.assertEqual(review["sampled_count"], 1, review)
            self.assertIn("unexpected-black-flash",
                          review["boundaries"][0]["findings"], review)
            self.assertEqual(review["samples_unavailable"], 0)

    def test_real_encoded_two_frame_white_pulse_is_flagged_but_not_a_cut_error(self):
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "pulse.mp4"
            self.make_movie(movie, (("gray", 1.4166667),
                                    ("white", 0.1666667),
                                    ("gray", 1.4166667)))
            result = qa.inspect_final_boundaries(movie, scenes(1.5, 1.5),
                                                 ["cut"])
            self.assertEqual(result["sampled_count"], 1, result)
            record = result["boundaries"][0]
            self.assertIn("unexpected-bright-flash", record["findings"], result)
            self.assertGreaterEqual(record["brief_bright_pulse_frames"], 1)

    def test_real_mp4_exposure_shift_is_measurable_but_intentional_cut_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "black-white.mp4"
            self.make_movie(movie, (("black", 1.5), ("white", 1.5)))
            review = qa.inspect_final_boundaries(movie, scenes(1.5, 1.5),
                                                 ["cut"])
            self.assertEqual(review["sampled_count"], 1, review)
            self.assertGreater(review["boundaries"][0]["luma_delta"], 190)
            self.assertEqual(review["warnings"], [])

    def test_real_free_studio_render_embeds_advisory_boundaries_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "media"
            media.mkdir()
            self.make_movie(media / "clip1.mp4", (("red", 4.0),))
            self.make_movie(media / "clip2.mp4", (("blue", 4.0),))
            output = root / "result.mp4"
            details = free_studio.render(
                "Rendu", "Première scène. Deuxième scène.", output,
                media_dir=media, mute=True, resolution=360, fps=12,
                transition="cut", caption_style="classic")
            review = details["final_visual_review"]
            self.assertIsNotNone(review)
            self.assertEqual(review["sampled_count"], 1, review)
            saved = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(saved["final_visual_review"]["sampled_count"], 1)
            self.assertTrue(output.is_file())
            audited = video_qa.audit(output)
            self.assertTrue(audited["ok"], audited["errors"])
            self.assertEqual(audited["boundary_visual_review"]["sampled_count"], 1)

    def test_missing_ffmpeg_sample_is_nonfatal_to_completed_render(self):
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "red.mp4"
            self.make_movie(movie, (("red", 3.0),))
            with patch.object(qa.subprocess, "run",
                              side_effect=subprocess.TimeoutExpired("ffmpeg", 14)):
                result = qa.inspect_final_boundaries(movie, scenes(1.5, 1.5),
                                                     ["fade"])
            self.assertEqual(result["samples_unavailable"], 1)


if __name__ == "__main__":
    unittest.main()
