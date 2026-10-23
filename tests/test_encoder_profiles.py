"""Allowlisted mobile-aware final encoder profiles, all FFmpeg code paths."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import encoder_profiles as ep
import fast_abstract
import fast_media
import free_studio
import studio_server
import video_qa
from cinematic import transition_plan
from xfade_final import final_xfade_command


class EncoderPolicyTests(unittest.TestCase):
    def choice(self, name="balanced", **overrides):
        data = dict(width=360, height=640, fps=12, duration=10.0, scenes=3)
        data.update(overrides)
        return ep.resolve_encoder_profile(name, **data)

    def test_old_export_is_bit_identical_in_encoder_configuration(self):
        base = self.choice()
        self.assertEqual((base.requested, base.applied, base.preset, base.crf),
                         ("balanced", "balanced", "veryfast", 23))
        self.assertIsNone(base.fallback_reason)
        self.assertEqual(base.to_dict()["policy"],
                         "conservative-pixel-frame-budget-v1")

    def test_automatic_choice_only_increases_detail_within_small_budget(self):
        light = self.choice("auto")
        self.assertEqual((light.applied, light.preset, light.crf),
                         ("detail", "veryfast", 20))
        heavy = self.choice("auto", width=1080, height=1920, fps=30,
                            duration=60.0)
        self.assertEqual((heavy.applied, heavy.preset, heavy.crf),
                         ("balanced", "veryfast", 23))
        self.assertIsNone(light.fallback_reason)

    def test_premium_budget_and_explicit_safe_downgrades(self):
        fast = self.choice("premium", duration=12)
        self.assertEqual((fast.applied, fast.preset, fast.crf),
                         ("premium", "faster", 20))
        mid = self.choice("premium", width=720, height=1280, fps=24,
                          duration=20.0)
        self.assertEqual((mid.applied, mid.preset, mid.crf),
                         ("detail", "veryfast", 20))
        self.assertIn("premium", mid.fallback_reason)
        self.assertIn("detail", mid.fallback_reason)
        large = self.choice("premium", width=1080, height=1920,
                            duration=50, scenes=15)
        self.assertEqual(large.applied, "balanced")
        self.assertIn("balanced", large.fallback_reason)
        detail_limited = self.choice("detail", width=720, height=1280,
                                     fps=30, duration=120.0)
        self.assertEqual(detail_limited.applied, "balanced")

    def test_invalid_profiles_and_workload_rejected_before_encoder(self):
        cases = (None, "", "--preset slower", 23, True, "crisper", "high", "PREMIUM")
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.choice(candidate)
        for updates in ({"width": True}, {"width": 361}, {"height": 4097},
                        {"fps": "24"}, {"fps": 0}, {"fps": 70},
                        {"scenes": True}, {"scenes": 19}, {"duration": float("nan")},
                        {"duration": -1}, {"duration": True}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.choice("balanced", **updates)

    def test_preset_allowlist_rejects_independent_shell_options(self):
        for pair in (("veryfast", 23), ("veryfast", 20), ("faster", 20)):
            self.assertTrue(ep.valid_ffmpeg_settings(*pair))
        for pair in (("slower", 23), ("veryfast", 0), ("veryfast", True),
                     ("faster", 23), ("-b:v 1000", 20), (None, 23)):
            self.assertFalse(ep.valid_ffmpeg_settings(*pair))

    def test_single_pass_fused_and_legacy_commands_use_same_profile(self):
        abstractions = dict(durations=[2.5, 2.5], width=360, height=640,
                            fps=12, transition="fade",
                            visual_filters=["format=yuv420p"], audio=None,
                            output=Path("out.mp4"), total=5.0)
        final = fast_abstract.build_onepass_abstract_command(
            **abstractions, preset="faster", crf=20)
        self.assertEqual(final[final.index("-preset") + 1], "faster")
        self.assertEqual(final[final.index("-crf") + 1], "20")
        media_opts = {**abstractions, "boundary_transitions": ("fade",)}
        from types import SimpleNamespace
        media_cmd = fast_media.build_onepass_media_command(
            [SimpleNamespace(kind="video", file=Path("clip.mp4")), None],
            **media_opts, preset="faster", crf=20)
        self.assertEqual(media_cmd[media_cmd.index("-preset") + 1], "faster")
        self.assertEqual(media_cmd[media_cmd.index("-crf") + 1], "20")
        plan = transition_plan([2.5, 2.5], fps=12, transition="fade")
        cmd = final_xfade_command(
            [Path("a.mp4"), Path("b.mp4")], plan, fps=12,
            transition="fade", overlays=["format=yuv420p"],
            duration=plan.total_frames / 12, output=Path("clip.mp4"),
            preset="faster", crf=20)
        self.assertEqual(cmd[cmd.index("-preset") + 1], "faster")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "20")

    def test_reject_invalid_flags_in_both_single_pass_paths(self):
        abstraction = dict(durations=[2.5, 2.5], width=360, height=640,
                           fps=12, transition="fade",
                           visual_filters=["format=yuv420p"], audio=None,
                           output=Path("out.mp4"), total=5.0)
        for preset, crf in (("bad", 23), ("veryfast", True), ("faster", 23)):
            with self.subTest(preset=preset, crf=crf), self.assertRaises(ValueError):
                fast_abstract.build_onepass_abstract_command(
                    **abstraction, preset=preset, crf=crf)
            with self.assertRaises(ValueError):
                fast_media.build_onepass_media_command(
                    [type("Image", (), {"kind": "image", "file": Path("photo.jpg")})()],
                    durations=[5.0], width=360, height=640, fps=12,
                    transition="fade", visual_filters=["format=yuv420p"],
                    audio=None, output=Path("out.mp4"), total=5.0,
                    preset=preset, crf=crf)

    def test_backend_job_contract_and_old_jobs_safe(self):
        valid = dict(topic="Test", script="Première phrase.",
                     lang="fr", aspect="9:16", resolution=360,
                     visuals="abstract", speech_rate=145, mute=True,
                     tts="espeak")
        for name in ep.ENCODER_PROFILES:
            result = studio_server.validate_draft({**valid, "encoder_profile": name})
            self.assertEqual(result["encoder_profile"], name)
        self.assertEqual(studio_server.validate_draft(valid)["encoder_profile"],
                         "balanced")
        for bad in (True, "premium -y", [], 9, None, "FAST"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                studio_server.validate_draft({**valid, "encoder_profile": bad})
        with tempfile.TemporaryDirectory() as work:
            jobs = studio_server.Jobs(Path(work) / "output")
            legacy = studio_server.validate_draft(valid)
            del legacy["encoder_profile"]
            args = jobs._arguments({"parameters": legacy}, Path(work))
            self.assertEqual(args[args.index("--encoder-profile") + 1], "balanced")
            chosen = studio_server.validate_draft({**valid, "encoder_profile": "premium"})
            args = jobs._arguments({"parameters": chosen}, Path(work))
            self.assertEqual(args[args.index("--encoder-profile") + 1], "premium")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real FFmpeg and ffprobe required for final encoder tests")
class RealEncoderProfileTests(unittest.TestCase):
    def check_encoded(self, path: Path, info: dict, profile: str) -> None:
        self.assertTrue(path.is_file())
        self.assertEqual(info["encoder_profile"]["applied"], profile)
        self.assertEqual(info["render_performance"]["final_encoder_preset"],
                         info["encoder_profile"]["preset"])
        self.assertEqual(info["render_performance"]["final_encoder_crf"],
                         info["encoder_profile"]["crf"])
        self.assertEqual(json.loads(path.with_suffix(".json").read_text())
                         ["encoder_profile"], info["encoder_profile"])
        qa = video_qa.validate(
            path, info, path.with_suffix(".srt"), video_qa.probe(path))
        self.assertTrue(qa["ok"], qa["errors"])

    def test_real_abstract_detail_and_legacy_export_share_safe_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for legacy in (False, True):
                output = root / ("legacy.mp4" if legacy else "onepass.mp4")
                info = free_studio.render(
                    "Qualité", "La mer est calme. Le ciel est bleu.",
                    output, mute=True, fps=12, resolution=360,
                    transition="fade", caption_style="classic",
                    encoder_profile="detail", use_fused_xfade=not legacy)
                self.check_encoded(output, info, "detail")
                self.assertEqual(info["encoder_profile"]["crf"], 20)

    def test_real_direct_media_premium_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            clip = source / "one.mp4"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i",
                 "testsrc2=s=180x320:r=12:d=4", "-an", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", str(clip)],
                check=True, capture_output=True, timeout=50)
            prior = clip.read_bytes()
            output = root / "premium.mp4"
            info = free_studio.render(
                "Premium", "Voici une première vidéo. Voici une seconde vidéo.",
                output, mute=True, fps=12, resolution=360,
                transition="fade", caption_style="classic", media_dir=source,
                encoder_profile="premium")
            self.check_encoded(output, info, "premium")
            self.assertEqual(info["encoder_profile"]["preset"], "faster")
            self.assertEqual(clip.read_bytes(), prior)

    def test_real_1080p_premium_downgraded_before_render(self):
        # Validate decision only: 1080p rendering on CI/mobile is too costly.
        selected = ep.resolve_encoder_profile(
            "premium", width=1080, height=1920, fps=24,
            duration=9.0, scenes=2)
        self.assertEqual(selected.applied, "balanced")
        self.assertIsNotNone(selected.fallback_reason)


if __name__ == "__main__":
    unittest.main()
