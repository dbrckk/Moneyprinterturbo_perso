"""Pure on-device benchmark advice tests; no real device claims."""
from __future__ import annotations

import copy
import unittest

from encoder_local_advice import advise


def fixture(fps=12, encoded_fps=None):
    encoded_fps = encoded_fps or {"current": 60., "crisper": 55., "compact": 45., "detail": 42.}
    specs = {"current": ("veryfast", 23), "crisper": ("veryfast", 20),
             "compact": ("faster", 23), "detail": ("faster", 20)}
    profiles = [
        {"name": name, "preset": p, "crf": c, "mean_encoded_fps": encoded_fps[name],
         "mean_ssim": .91, "total_size_bytes": 15000, "total_wall_seconds": 1.5}
        for name, (p, c) in specs.items()
    ]
    cases = []
    for name in ("motion", "fine-lines"):
        cases.append({"name": name, "encodes": [
            {"name": item["name"], "preset": item["preset"], "crf": item["crf"],
             "encode_wall_seconds": .75, "encoded_fps": encoded_fps[item["name"]],
             "encoded_size_bytes": 7500}
            for item in profiles]})
    return {"production_profile_unchanged": True, "resolution": 360,
            "fps": fps, "seconds": 3, "metric": "ffmpeg-ssim-all",
            "machine": "aarch64", "profiles": profiles, "scenarios": cases}


def evaluate(report, **kw):
    settings = dict(width=360, height=640, fps=12, duration=15., scenes=3)
    settings.update(kw)
    return advise(report, **settings)


class EncoderAdviceTests(unittest.TestCase):
    def test_fast_local_360p_can_suggest_premium_without_activating_it(self):
        result = evaluate(fixture())
        self.assertEqual(result["suggested_profile"], "premium")
        self.assertFalse(result["applies_automatically"])
        self.assertIn("thermal", result["warning"])
        self.assertEqual(result["benchmark"]["machine"], "aarch64")

    def test_slow_premium_chooses_detail_or_balanced(self):
        report = fixture(encoded_fps={"current": 60., "crisper": 50.,
                                      "compact": 10., "detail": 10.})
        self.assertEqual(evaluate(report)["suggested_profile"], "detail")
        report = fixture(encoded_fps={"current": 60., "crisper": 10.,
                                      "compact": 10., "detail": 10.})
        self.assertEqual(evaluate(report)["suggested_profile"], "balanced")

    def test_cannot_extrapolate_to_720p_or_higher_fps(self):
        report = fixture()
        self.assertEqual(evaluate(report, width=720, height=1280)["suggested_profile"],
                         "balanced")
        self.assertEqual(evaluate(report, fps=24)["suggested_profile"], "balanced")

    def test_existing_policy_remains_stricter_than_benchmark(self):
        report = fixture()
        self.assertEqual(evaluate(report, duration=70)["suggested_profile"], "detail")
        self.assertEqual(evaluate(report, duration=180)["suggested_profile"], "balanced")

    def test_rejects_corrupt_incomplete_and_mismatched_reports(self):
        good = fixture()
        for mutate in (
            lambda r: r.update(production_profile_unchanged=False),
            lambda r: r.update(metric="psnr"),
            lambda r: r.update(resolution=999),
            lambda r: r["profiles"].pop(),
            lambda r: r["profiles"][0].update(crf=19),
            lambda r: r["profiles"][0].update(mean_encoded_fps=float("nan")),
            lambda r: r["scenarios"].pop(),
            lambda r: r["scenarios"][0]["encodes"].pop(),
            lambda r: r["scenarios"][0]["encodes"][0].update(crf=12),
            lambda r: r["scenarios"][0]["encodes"][0].update(encoded_fps=0),
        ):
            report = copy.deepcopy(good)
            mutate(report)
            with self.subTest(report=report), self.assertRaises(ValueError):
                evaluate(report)

    def test_invalid_export_workload_rejected(self):
        for args in ({"width": 361}, {"fps": 0}, {"duration": float("nan")},
                     {"scenes": 0}, {"height": True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                evaluate(fixture(), **args)


if __name__ == "__main__":
    unittest.main()
