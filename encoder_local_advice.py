#!/usr/bin/env python3
"""Read a *local* H.264 benchmark and provide conservative, non-binding advice.

No network, no phone identifiers, no writes to the renderer's settings.
Only a benchmark explicitly run on the user's Android handset is meaningful
for that device. A GitHub Actions report must not be used for calibration.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from encoder_profiles import resolve_encoder_profile

_PAIRS = {
    "balanced": ("current", "veryfast", 23),
    "detail": ("crisper", "veryfast", 20),
    "premium": ("detail", "faster", 20),
}


def _finite_positive(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Missing or invalid positive benchmark metric")
    return float(value)


def advise(report: dict, *, width: int, height: int,
           fps: int, duration: float, scenes: int) -> dict[str, object]:
    """Return an advisory within production's existing strict workload limits.

    Require complete scenario coverage, fixed benchmark schema, and repeated
    encode measurements. A short synthetic test is never a thermal or battery
    safety guarantee; always use a large throughput margin.
    """
    if type(report) is not dict or report.get("production_profile_unchanged") is not True:
        raise ValueError("Not a MoneyPrinter offline benchmark")
    from encoder_profile_benchmark import config_ok, SCENARIOS
    resolution = report.get("resolution")
    rate = report.get("fps")
    seconds = report.get("seconds")
    if not config_ok(resolution, rate, seconds):
        raise ValueError("Unsupported benchmark configuration")
    if report.get("metric") != "ffmpeg-ssim-all":
        raise ValueError("Benchmark quality metric mismatch")
    cases = report.get("scenarios")
    if type(cases) is not list or len(cases) != len(SCENARIOS):
        raise ValueError("Benchmark scenarios incomplete")
    names = {name for name, _ in SCENARIOS}
    if {case.get("name") for case in cases if type(case) is dict} != names:
        raise ValueError("Unexpected benchmark scenarios")
    profiles = report.get("profiles")
    if type(profiles) is not list or len(profiles) != 4:
        raise ValueError("Benchmark profiles incomplete")
    profile_by_name = {}
    for entry in profiles:
        if type(entry) is not dict or type(entry.get("name")) is not str:
            raise ValueError("Malformed benchmark profile")
        name = entry["name"]
        if name in profile_by_name:
            raise ValueError("Duplicate benchmark profile")
        profile_by_name[name] = entry
    if set(profile_by_name) != {"current", "crisper", "compact", "detail"}:
        raise ValueError("Benchmark profile set mismatch")

    throughput = {}
    for key, (benchmark_name, preset, crf) in _PAIRS.items():
        entry = profile_by_name[benchmark_name]
        if entry.get("preset") != preset or entry.get("crf") != crf:
            raise ValueError("Benchmark encoder flags mismatch")
        _finite_positive(entry.get("total_wall_seconds"))
        _finite_positive(entry.get("total_size_bytes"))
        _finite_positive(entry.get("mean_ssim"))
        throughput[key] = _finite_positive(entry.get("mean_encoded_fps"))

    # Each scenario must include actual measurements of the same codec.
    # A mean FPS can hide the worst (fine-detail) clip: decide from the
    # slowest actually measured scenario instead.
    scenario_fps: dict[str, list[float]] = {k: [] for k in _PAIRS}
    scenario_by_benchmark = {v[0]: k for k, v in _PAIRS.items()}
    for case in cases:
        entries = case.get("encodes")
        if type(entries) is not list or len(entries) != 4:
            raise ValueError("Incomplete benchmark scenario encodes")
        seen = set()
        for item in entries:
            if type(item) is not dict or item.get("name") not in profile_by_name:
                raise ValueError("Unknown scenario encode")
            name = item["name"]
            if name in seen:
                raise ValueError("Duplicate scenario encode")
            seen.add(name)
            p = profile_by_name[name]
            if item.get("preset") != p.get("preset") or item.get("crf") != p.get("crf"):
                raise ValueError("Inconsistent scenario encoder flags")
            _finite_positive(item.get("encode_wall_seconds"))
            measured_fps = _finite_positive(item.get("encoded_fps"))
            _finite_positive(item.get("encoded_size_bytes"))
            if name in scenario_by_benchmark:
                scenario_fps[scenario_by_benchmark[name]].append(measured_fps)
        if len(seen) != 4:
            raise ValueError("Incomplete scenario profile coverage")

    for key, samples in scenario_fps.items():
        if len(samples) != len(SCENARIOS):
            raise ValueError("Missing measured scenario FPS")
        # The benchmark's display-only mean is not a safety limit.
        throughput[key] = min(samples)

    baseline = resolve_encoder_profile(
        "balanced", width=width, height=height, fps=fps,
        duration=duration, scenes=scenes)
    detail = resolve_encoder_profile(
        "detail", width=width, height=height, fps=fps,
        duration=duration, scenes=scenes)
    premium = resolve_encoder_profile(
        "premium", width=width, height=height, fps=fps,
        duration=duration, scenes=scenes)

    # Never extrapolate an undersized synthetic clip to higher resolution/FPS.
    same_scale = width * height <= resolution * round(resolution * 16 / 9)
    same_rate = fps <= rate
    # 2x+ throughput headroom and a minimum 67% of baseline FPS.
    # This is advice, not an automatic policy change.
    enough = lambda key: (throughput[key] >= fps * 2.0 and
                          throughput[key] >= throughput["balanced"] * 0.67)
    selected = baseline
    reason = "Conservative default: benchmark does not establish sustained device performance"
    if same_scale and same_rate:
        if premium.applied == "premium" and enough("premium"):
            selected = premium
            reason = "Short local synthetic encode has 2x FPS headroom for premium"
        elif detail.applied == "detail" and enough("detail"):
            selected = detail
            reason = "Short local synthetic encode has 2x FPS headroom for detail"
    return {
        "suggested_profile": selected.applied,
        "requested_export": {"width": width, "height": height, "fps": fps,
                             "duration": duration, "scenes": scenes},
        "benchmark": {"resolution": resolution, "fps": rate,
                      "seconds": seconds, "machine": str(report.get("machine", "unknown"))[:80]},
        "measured_encoded_fps": {k: round(v, 2) for k, v in throughput.items()},
        "throughput_basis": "slowest-encoded-scenario-fps",
        "reason": reason,
        "applies_automatically": False,
        "warning": ("Advisory only. Synthetic 2–4s encodes do not measure thermal "
                    "throttling, RAM, battery, or subjective video quality. "
                    "Do not use GitHub-hosted results as Android calibration."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, default=Path("outputs/encoder-benchmark/metrics.json"))
    parser.add_argument("--width", type=int, default=360)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--scenes", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if args.metrics.stat().st_size > 1024 * 1024:
            raise ValueError("Benchmark metrics too large")
        report = json.loads(args.metrics.read_text(encoding="utf-8"))
        result = advise(report, width=args.width, height=args.height,
                        fps=args.fps, duration=args.duration, scenes=args.scenes)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Calibration unavailable: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
