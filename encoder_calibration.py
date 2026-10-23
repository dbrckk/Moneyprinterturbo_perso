"""Optional bounded local H.264 speed calibration; never accesses gallery media.

Three tiny synthetic x264 encodes run only after an explicit local user action.
Measured throughput is advisory, host-bound and short-lived. The resolver
retains static quality/resource caps and never trusts this as temperature,
battery, memory, or proof of production encoding throughput.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time

CALIBRATION_VERSION = 1
CALIBRATION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
SAMPLE_WIDTH = 360
SAMPLE_HEIGHT = 640
SAMPLE_FPS = 12
SAMPLE_FRAMES = 24
PROFILES = (("balanced", "veryfast", 23), ("detail", "veryfast", 20),
            ("premium", "faster", 20))
MAX_ENCODER_TIMEOUT = 30
MAX_MANIFEST_BYTES = 8192


def _host_binding() -> str:
    """Non-reversible host-environment label, not a device serial/IMEI."""
    scope = "|".join((platform.system(), platform.machine(), platform.release()))
    return hashlib.sha256(("mpt-encoder-calibration-v1|" + scope).encode()).hexdigest()


def _valid_fps(value: object) -> bool:
    return (type(value) in (float, int) and math.isfinite(value)
            and 0.1 <= value <= 600.0)


def read_calibration(path: Path | None, *, now: float | None = None) -> dict | None:
    """Fail closed on missing, stale, malformed or nonlocal synthetic evidence."""
    if path is None or not isinstance(path, Path):
        return None
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        current = time.time() if now is None else now
        if (not isinstance(data, dict)
                or type(data.get("version")) is not int
                or data["version"] != CALIBRATION_VERSION
                or data.get("source") != "synthetic-local-x264"
                or data.get("host_binding") != _host_binding()
                or type(data.get("created_at")) is not int
                or not isinstance(current, (int, float))
                or not math.isfinite(current)
                or not 0 <= current - data["created_at"] <= CALIBRATION_MAX_AGE_SECONDS
                or data.get("sample") != {
                    "width": SAMPLE_WIDTH, "height": SAMPLE_HEIGHT,
                    "fps": SAMPLE_FPS, "frames": SAMPLE_FRAMES
                }):
            return None
        measurements = data.get("measurements")
        if not isinstance(measurements, list) or len(measurements) != len(PROFILES):
            return None
        for item, (name, preset, crf) in zip(measurements, PROFILES):
            if (not isinstance(item, dict) or item.get("name") != name
                    or item.get("preset") != preset or item.get("crf") != crf
                    or not _valid_fps(item.get("encoded_fps"))
                    or not _valid_fps(item.get("wall_seconds"))
                    or abs(item["encoded_fps"] - SAMPLE_FRAMES / item["wall_seconds"])
                    > max(0.25, 0.05 * item["encoded_fps"])):
                return None
        return data
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return None


def status_for(path: Path) -> dict[str, object]:
    """Return only safe, non-identifying status fields to the local UI."""
    found = read_calibration(path)
    if found is None:
        return {"available": False, "status": "missing_or_expired"}
    measurements = found["measurements"]
    return {
        "available": True, "status": "ready",
        "created_at": found["created_at"],
        "expires_at": found["created_at"] + CALIBRATION_MAX_AGE_SECONDS,
        "encoded_fps": {row["name"]: round(row["encoded_fps"], 1)
                        for row in measurements},
        "sample_fps": SAMPLE_FPS,
        "scope": "synthetic-360p-only",
    }


def _encode_one(work: Path, name: str, preset: str, crf: int) -> dict:
    output = work / f"{name}.mp4"
    source = (f"testsrc2=s={SAMPLE_WIDTH}x{SAMPLE_HEIGHT}:"
              f"r={SAMPLE_FPS}:d={SAMPLE_FRAMES/SAMPLE_FPS}")
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", source,
        "-vf", "drawgrid=w=19:h=19:t=1:c=white@0.35,format=yuv420p",
        "-frames:v", str(SAMPLE_FRAMES),
        "-an", "-c:v", "libx264", "-preset", preset,
        "-crf", str(crf), "-threads:v", "2", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ]
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True,
                            timeout=MAX_ENCODER_TIMEOUT, check=True)
    wall = time.perf_counter() - started
    if (result.returncode != 0 or not output.is_file()
            or not 1000 < output.stat().st_size < 10 * 1024 * 1024
            or not math.isfinite(wall) or wall <= 0):
        raise RuntimeError("Local encoder sample was not produced safely")

    metadata = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,nb_frames,width,height,avg_frame_rate",
        "-of", "json", str(output),
    ], capture_output=True, check=True, timeout=12, text=True)
    probe = json.loads(metadata.stdout)["streams"]
    if len(probe) != 1:
        raise RuntimeError("Encoder sample has unexpected number of video streams")
    stream = probe[0]
    if (stream.get("codec_name") != "h264"
            or stream.get("width") != SAMPLE_WIDTH
            or stream.get("height") != SAMPLE_HEIGHT
            or int(stream.get("nb_frames", -1)) != SAMPLE_FRAMES
            or stream.get("avg_frame_rate") != f"{SAMPLE_FPS}/1"):
        raise RuntimeError("Local sample codec, duration or frame rate mismatch")
    return {
        "name": name, "preset": preset, "crf": crf,
        "wall_seconds": round(wall, 5),
        "encoded_fps": round(SAMPLE_FRAMES / wall, 4),
    }


def calibrate(path: Path) -> dict[str, object]:
    """Run bounded real H.264 encodes and atomically commit validated evidence."""
    if not isinstance(path, Path) or path.is_symlink():
        raise ValueError("Calibration target must be a regular local file")
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_file():
        raise ValueError("Calibration target cannot be a directory")
    with tempfile.TemporaryDirectory(prefix=".mpt-encoder-cal-", dir=directory) as folder:
        work = Path(folder)
        measurements = [_encode_one(work, name, preset, crf)
                        for name, preset, crf in PROFILES]
        created = int(time.time())
        data = {
            "version": CALIBRATION_VERSION,
            "source": "synthetic-local-x264",
            "host_binding": _host_binding(),
            "created_at": created,
            "sample": {"width": SAMPLE_WIDTH, "height": SAMPLE_HEIGHT,
                       "fps": SAMPLE_FPS, "frames": SAMPLE_FRAMES},
            "measurements": measurements,
        }
        provisional = work / "calibration.json"
        provisional.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(provisional, 0o600)
        if read_calibration(provisional, now=created) is None:
            raise RuntimeError("Local calibration failed schema validation")
        os.replace(provisional, path)
        return status_for(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure tiny local x264 test clips")
    parser.add_argument("--output", type=Path, default=Path("outputs/.encoder_calibration.json"))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(calibrate(args.output), ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Calibration impossible : {str(exc)[:230]}\n")


if __name__ == "__main__":
    raise SystemExit(main())
