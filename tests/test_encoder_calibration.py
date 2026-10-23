"""Host-local one-tap H.264 calibration: freshness, resource bounds and real MP4s."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

import encoder_calibration as calibration
import encoder_profiles
import free_studio
import video_qa


def valid_measurements(*, balanced=100.0, detail=85.0, premium=65.0):
    return [
        dict(name=name, preset=preset, crf=crf,
             wall_seconds=round(calibration.SAMPLE_FRAMES / rate, 5),
             encoded_fps=rate)
        for (name, preset, crf), rate in zip(
            calibration.PROFILES, (balanced, detail, premium))
    ]


def fake_evidence(*, elapsed=0, host_binding=None, rates=None):
    return {
        "version": calibration.CALIBRATION_VERSION,
        "source": "synthetic-local-x264",
        "host_binding": host_binding or calibration._host_binding(),
        "created_at": int(time.time()) - elapsed,
        "sample": dict(width=360, height=640, fps=12, frames=24),
        "measurements": rates or valid_measurements(),
    }


class CalibrationSchemaTests(unittest.TestCase):
    def test_calibration_missing_expired_foreign_corrupt_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            calibration_file = directory / ".calibration.json"
            self.assertIsNone(calibration.read_calibration(calibration_file))
            self.assertFalse(calibration.status_for(calibration_file)["available"])
            for mutated in (
                {"host_binding": "other-device"},
                {"version": True},
                {"version": 99},
                {"source": "github-actions"},
                {"created_at": int(time.time()) + 400},
                {"created_at": int(time.time()) - 15 * 86400},
                {"sample": {"width": 720, "height": 1280, "fps": 12, "frames": 24}},
                {"measurements": []},
                {"measurements": valid_measurements(detail=9999)},
                {"measurements": valid_measurements(detail=0.2)},
            ):
                document = fake_evidence()
                document.update(mutated)
                calibration_file.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(mutated=mutated):
                    self.assertIsNone(calibration.read_calibration(calibration_file))
            # Very quick valid encodes must not be mislabeled corrupt due
            # to elapsed time below 0.1 second.
            calibration_file.write_text(json.dumps(fake_evidence(
                rates=valid_measurements(balanced=300, detail=280, premium=220))),
                encoding="utf-8")
            self.assertIsNotNone(calibration.read_calibration(calibration_file))
            calibration_file.write_text("bad JSON", encoding="utf-8")
            self.assertIsNone(calibration.read_calibration(calibration_file))
            calibration_file.write_text(json.dumps(fake_evidence()), encoding="utf-8")
            alias = directory / "linked.json"
            alias.symlink_to(calibration_file)
            self.assertIsNone(calibration.read_calibration(alias))
            self.assertIsNotNone(calibration.read_calibration(calibration_file))
            calibration_file.write_text("x" * (calibration.MAX_MANIFEST_BYTES + 1))
            self.assertIsNone(calibration.read_calibration(calibration_file))

    def test_atomic_failed_calibration_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "calibration.json"
            original = json.dumps(fake_evidence())
            output.write_text(original, encoding="utf-8")
            with patch.object(calibration, "_encode_one",
                              side_effect=RuntimeError("synthetic encode unavailable")):
                with self.assertRaises(RuntimeError):
                    calibration.calibrate(output)
            self.assertEqual(output.read_text(encoding="utf-8"), original)
            self.assertFalse(list(Path(folder).glob(".mpt-encoder-cal-*")))

    def test_auto_uses_verified_360p_evidence_not_gitlab_or_720p_extrapolation(self):
        workload = dict(width=360, height=640, fps=12, duration=8.0, scenes=3)
        data = fake_evidence()
        measured = encoder_profiles.resolve_encoder_profile(
            "auto", **workload, calibration=data)
        self.assertEqual(measured.applied, "premium")
        self.assertTrue(measured.calibration_used)
        self.assertEqual(measured.policy, "local-360p-calibration-v1")
        self.assertTrue(measured.to_dict()["calibration_used"])
        # Host evidence can only inform the opt-in automatic mode.
        requested = encoder_profiles.resolve_encoder_profile(
            "balanced", **workload, calibration=data)
        self.assertFalse(requested.calibration_used)
        self.assertEqual(requested.applied, "balanced")
        slow = fake_evidence(rates=valid_measurements(
            balanced=18.0, detail=13.0, premium=9.0))
        selected = encoder_profiles.resolve_encoder_profile(
            "auto", **workload, calibration=slow)
        self.assertEqual(selected.applied, "balanced")
        self.assertTrue(selected.calibration_used)
        middle = fake_evidence(rates=valid_measurements(
            balanced=65.0, detail=46.0, premium=19.0))
        selected = encoder_profiles.resolve_encoder_profile(
            "auto", **workload, calibration=middle)
        self.assertEqual(selected.applied, "detail")
        hd = encoder_profiles.resolve_encoder_profile(
            "auto", width=720, height=1280, fps=24, duration=8.0,
            scenes=3, calibration=data)
        unchanged = encoder_profiles.resolve_encoder_profile(
            "auto", width=720, height=1280, fps=24, duration=8.0,
            scenes=3)
        self.assertEqual(hd, unchanged)
        self.assertFalse(hd.calibration_used)
        too_many_scenes = encoder_profiles.resolve_encoder_profile(
            "auto", width=360, height=640, fps=12, duration=40.0,
            scenes=8, calibration=data)
        self.assertFalse(too_many_scenes.calibration_used)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Actual libx264 encoding required")
class RealLocalCalibrationTests(unittest.TestCase):
    def test_real_3_synthetic_mp4_encodes_and_auto_render_same_host(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence = root / ".encoder_calibration.json"
            result = calibration.calibrate(evidence)
            self.assertTrue(result["available"], result)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(set(result["encoded_fps"]),
                             {"balanced", "detail", "premium"})
            on_disk = calibration.read_calibration(evidence)
            self.assertIsNotNone(on_disk)
            self.assertEqual(on_disk["source"], "synthetic-local-x264")
            self.assertEqual(len(on_disk["measurements"]), 3)
            self.assertNotIn("serial", evidence.read_text(encoding="utf-8"))
            self.assertFalse(list(root.glob(".mpt-encoder-cal-*")))
            output = root / "finished.mp4"
            info = free_studio.render(
                "Mon appareil", "Une scène de test synthétique.",
                output, resolution=360, fps=12, mute=True,
                caption_style="classic", encoder_profile="auto",
                encoder_calibration_path=evidence)
            saved = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(saved["encoder_profile"], info["encoder_profile"])
            self.assertTrue(saved["encoder_profile"]["calibration_used"])
            self.assertEqual(saved["encoder_profile"]["policy"],
                             "local-360p-calibration-v1")
            quality = video_qa.validate(
                output, info, output.with_suffix(".srt"),
                video_qa.probe(output))
            self.assertTrue(quality["ok"], quality["errors"])
            missing = root / "no-calibration.json"
            fallback = encoder_profiles.resolve_encoder_profile(
                "auto", width=360, height=640, fps=12,
                duration=4.0, scenes=1,
                calibration=calibration.read_calibration(missing))
            self.assertFalse(fallback.calibration_used)


if __name__ == "__main__":
    unittest.main()
