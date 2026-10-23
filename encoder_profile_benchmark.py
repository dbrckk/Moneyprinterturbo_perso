#!/usr/bin/env python3
"""Offline A/B benchmark of final H.264 encoder settings for MoneyPrinter.

This opt-in benchmark creates short deterministic FFV1 reference clips and
several final H.264 exports, then measures FFmpeg SSIM, output size, CPU time
and elapsed encode time. It does NOT claim subjective visual quality, device
battery consumption, or that a GitHub runner represents Android hardware.

Never changes production rendering settings or a user's uploaded videos.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import platform
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - OS dependent
    resource = None


# Fixed allowlist: no arbitrary filtergraph or shell arguments accepted.
PROFILES = (
    ("current", "veryfast", 23),
    ("crisper", "veryfast", 20),
    ("compact", "faster", 23),
    ("detail", "faster", 20),
)
SCENARIOS = (
    ("motion", "Dynamic shapes and colors"),
    ("fine-lines", "Moving shapes with thin graphic lines"),
)
_MAX_OUTPUT_BYTES = 20 * 1024 * 1024
_SSIM = re.compile(r"SSIM [^\n]*?All:([0-9]+(?:\.[0-9]+)?)")


def config_ok(resolution: int, fps: int, seconds: int) -> bool:
    return (type(resolution) is int and resolution in (360, 720)
            and type(fps) is int and fps in (12, 24, 30)
            and type(seconds) is int and 2 <= seconds <= 4
            and resolution * fps * seconds <= 720 * 24 * 4)


def _cpu_usage() -> float | None:
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _execute(args: list[str], *, timeout: int = 95) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg command failed ({result.returncode}): "
            + result.stderr[-1200:])
    return result


def _profile_path(scene: str, name: str) -> str:
    return f"videos/{scene}-{name}.mp4"


def _video_probe(path: Path, *, width: int, height: int, fps: int,
                 frames: int, codec: str) -> None:
    result = _execute([
        "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,nb_frames,nb_read_frames,avg_frame_rate",
        "-of", "json", str(path),
    ], timeout=20)
    try:
        info = json.loads(result.stdout)["streams"][0]
        observed_fps = info["avg_frame_rate"].split("/")
        rate = int(observed_fps[0]) / int(observed_fps[1])
        if (int(info["width"]) != width or int(info["height"]) != height
                or info["codec_name"] != codec
                or int(info.get("nb_frames", info.get("nb_read_frames", -1))) != frames
                or not math.isfinite(rate) or abs(rate - fps) > 0.01):
            raise ValueError("Encoder produced different dimensions/frame count/FPS")
    except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise RuntimeError("Invalid or altered encoded video stream") from exc


def _make_reference(scene: str, destination: Path, *,
                    resolution: int, fps: int, seconds: int) -> None:
    width, height = resolution, round(resolution * 16 / 9)
    frames = fps * seconds
    if scene == "motion":
        source = f"testsrc2=s={width}x{height}:r={fps}:d={seconds}"
        vf = "format=yuv420p"
    elif scene == "fine-lines":
        source = f"testsrc2=s={width}x{height}:r={fps}:d={seconds}"
        vf = "drawgrid=w=17:h=17:t=1:c=white@0.45,format=yuv420p"
    else:
        raise ValueError("Unknown curated benchmark scene")
    _execute([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", source, "-vf", vf, "-frames:v", str(frames),
        "-an", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p",
        str(destination),
    ])
    _video_probe(destination, width=width, height=height, fps=fps,
                 frames=frames, codec="ffv1")


def _encode(reference: Path, destination: Path, *,
            preset: str, crf: int, resolution: int,
            fps: int, seconds: int) -> dict:
    if (preset not in {p[1] for p in PROFILES}
            or crf not in {p[2] for p in PROFILES}):
        raise ValueError("Only benchmarked encoder profiles are supported")
    started = time.perf_counter()
    before_cpu = _cpu_usage()
    _execute([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(reference), "-map", "0:v:0", "-an",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-frames:v", str(fps * seconds),
        "-movflags", "+faststart", str(destination),
    ])
    elapsed = time.perf_counter() - started
    after_cpu = _cpu_usage()
    _video_probe(destination, width=resolution, height=round(resolution * 16 / 9),
                 fps=fps, frames=fps * seconds, codec="h264")
    size = destination.stat().st_size
    if not 1000 <= size <= _MAX_OUTPUT_BYTES:
        raise RuntimeError("Benchmark output empty or unexpectedly large")
    return {
        "encode_wall_seconds": round(elapsed, 3),
        "encode_cpu_seconds": (
            round(max(0.0, after_cpu - before_cpu), 3)
            if before_cpu is not None and after_cpu is not None else None),
        "encoded_size_bytes": size,
        "encoded_fps": round(fps * seconds / max(elapsed, 1e-6), 2),
    }


def _ssim(reference: Path, encoded: Path) -> float:
    result = _execute([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info",
        "-i", str(encoded), "-i", str(reference),
        "-filter_complex", "[0:v][1:v]ssim",
        "-an", "-f", "null", "-",
    ], timeout=60)
    matches = _SSIM.findall(result.stderr)
    if not matches:
        raise RuntimeError("SSIM filter output unavailable; no quality claim possible")
    value = float(matches[-1])
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise RuntimeError("FFmpeg reported invalid SSIM")
    return round(value, 6)


def _pareto_profiles(profiles: list[dict]) -> None:
    """Tag non-dominated profiles without calling them objectively superior."""
    # Higher SSIM is better; smaller bytes are cheaper.
    # Elapsed time is too noisy to serve as a hard Pareto criterion.
    for item in profiles:
        item["sample_pareto"] = not any(
            other["name"] != item["name"]
            and other["mean_ssim"] >= item["mean_ssim"]
            and other["total_size_bytes"] <= item["total_size_bytes"]
            and (other["mean_ssim"] > item["mean_ssim"]
                 or other["total_size_bytes"] < item["total_size_bytes"])
            for other in profiles)


def render_html(report: dict) -> str:
    """Standalone mobile-readable A/B report; HTML inputs are escaped."""
    escape = lambda value: html.escape(str(value), quote=True)
    rows = []
    for profile in report["profiles"]:
        rows.append(
            '<tr>'
            f'<td><strong>{escape(profile["name"])}</strong></td>'
            f'<td>{escape(profile["preset"])}</td>'
            f'<td>{escape(profile["crf"])}</td>'
            f'<td>{profile["mean_ssim"]:.5f}</td>'
            f'<td>{profile["total_size_bytes"] / 1024:.0f}</td>'
            f'<td>{profile["total_wall_seconds"]:.2f}</td>'
            f'<td>{"✓" if profile["sample_pareto"] else "—"}</td></tr>')
    cards = []
    for case in report["scenarios"]:
        previews = []
        for entry in case["encodes"]:
            previews.append(
                '<figure><figcaption>'
                f'<strong>{escape(entry["name"])}</strong> · '
                f'SSIM {escape(entry["ssim"])} · '
                f'{escape(round(entry["encoded_size_bytes"] / 1024))} KiB'
                '</figcaption>'
                f'<video controls muted playsinline preload="none" '
                f'src="{escape(entry["video"])}"></video></figure>')
        cards.append(
            f'<section><h2>{escape(case["label"])}</h2>'
            + '<div class="clips">' + ''.join(previews) + '</div></section>')
    css = """
    body{font:16px system-ui,sans-serif;background:#0f172a;color:#f8fafc;
    max-width:1160px;margin:auto;padding:20px;line-height:1.55}
    h1{font-size:1.55rem}h2{font-size:1.16rem}
    p,small{color:#cbd5e1}.table-scroll{overflow-x:auto}
    table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
    th,td{border-bottom:1px solid #334155;text-align:left;padding:9px}
    .clips{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
    figure{margin:0;border:1px solid #475569;padding:10px;border-radius:8px}
    video{width:100%;max-height:440px;background:black;border-radius:6px}
    figcaption{padding:4px 0 10px}a{color:#93c5fd}
    @media(max-width:640px){.clips{grid-template-columns:1fr}body{padding:12px}}
    """
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; media-src \'self\'; '
        'style-src \'unsafe-inline\';">'
        '<title>MoneyPrinter — benchmark encodeur</title>'
        '<style>' + css + '</style></head><body>'
        '<h1>MoneyPrinter — comparaison H.264</h1>'
        '<p>Échantillons synthétiques locaux, pas des vidéos utilisateur. '
        'SSIM mesure une similarité objective et ne certifie pas le rendu artistique. '
        'Les temps sont propres au matériel de cette exécution (GitHub ≠ Android). '
        'Le classement Pareto ci-dessous porte uniquement sur SSIM et taille '
        'observés, et ne modifie jamais les réglages de production.</p>'
        f'<p>Résolution {escape(report["resolution"])}p · '
        f'{escape(report["fps"])} images/s · '
        f'{escape(report["seconds"])} s par scène. '
        f'Matériel : {escape(report["machine"])}.</p>'
        '<div class="table-scroll"><table><thead><tr>'
        '<th>Profil</th><th>Preset</th><th>CRF</th><th>SSIM moyen</th>'
        '<th>KiB total</th><th>Temps (s)</th><th>Pareto*</th>'
        '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
        + ''.join(cards) + '<p><small>* Échantillon synthétique uniquement. '
        'Voir metrics.json pour les mesures complètes, sans résultat inventé.'
        '</small></p></body></html>\n'
    )


def run_benchmark(output: Path, *, resolution: int = 360,
                  fps: int = 12, seconds: int = 3) -> dict:
    """Run bounded local encodes. Returns factual metrics, not an auto-switch."""
    if not config_ok(resolution, fps, seconds):
        raise ValueError("Unsupported benchmark size/FPS/duration budget")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and FFprobe are required for offline benchmark")
    # Keep the last complete benchmark intact until *all* outputs pass.
    # No partial MP4 may become visible beside stale metrics.json after a
    # failed FFmpeg encode, a missing SSIM filter or a truncated media stream.
    if output.expanduser().is_symlink():
        raise ValueError("Benchmark output directory must not be a symlink")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if not output.is_dir():
            raise ValueError("Benchmark output path is not a directory")
        old_manifest = output / "metrics.json"
        if any(output.iterdir()) and (not old_manifest.is_file() or
                '"production_profile_unchanged": true' not in
                old_manifest.read_text(encoding="utf-8", errors="replace")[:3000]):
            raise ValueError("Refusing to overwrite an unrelated output directory")
    with tempfile.TemporaryDirectory(
        prefix="mpt-encode-bench-", dir=output.parent
    ) as tmp:
        work = Path(tmp)
        staged = work / "finished"
        videos = staged / "videos"
        videos.mkdir(parents=True)
        scenarios = []
        for name, label in SCENARIOS:
            reference = work / f"reference-{name}.mkv"
            _make_reference(name, reference, resolution=resolution, fps=fps,
                            seconds=seconds)
            candidates = []
            for profile_name, preset, crf in PROFILES:
                relative = _profile_path(name, profile_name)
                destination = staged / relative
                measures = _encode(
                    reference, destination, preset=preset, crf=crf,
                    resolution=resolution, fps=fps, seconds=seconds)
                measures["ssim"] = _ssim(reference, destination)
                candidates.append({"name": profile_name, "preset": preset,
                                   "crf": crf, "video": relative, **measures})
            scenarios.append({"name": name, "label": label, "encodes": candidates})
        profiles = []
        for name, preset, crf in PROFILES:
            gathered = [next(entry for entry in scene["encodes"] if entry["name"] == name)
                        for scene in scenarios]
            wall = sum(entry["encode_wall_seconds"] for entry in gathered)
            profiles.append({
                "name": name, "preset": preset, "crf": crf,
                "mean_ssim": round(sum(item["ssim"] for item in gathered) / len(gathered), 6),
                "total_size_bytes": sum(item["encoded_size_bytes"] for item in gathered),
                "total_wall_seconds": round(wall, 3),
                "mean_encoded_fps": round(sum(item["encoded_fps"] for item in gathered) / len(gathered), 2),
                "total_cpu_seconds": (
                    round(sum(item["encode_cpu_seconds"] for item in gathered), 3)
                    if all(item["encode_cpu_seconds"] is not None for item in gathered)
                    else None),
            })
        _pareto_profiles(profiles)
        report = {
            "description": "Measured synthetic FFmpeg H.264 A/B; no quality certification or auto-switch",
            "resolution": resolution, "fps": fps, "seconds": seconds,
            "reference_codec": "ffv1-yuv420p-lossless",
            "metric": "ffmpeg-ssim-all",
            "machine": platform.machine(),
            "ffmpeg_version": _execute(["ffmpeg", "-version"], timeout=15).stdout.splitlines()[0],
            "hardware_notice": "Timing belongs to this host only; no Android battery/RAM estimate",
            "production_profile_unchanged": True,
            "scenarios": scenarios, "profiles": profiles,
        }
        (staged / "metrics.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (staged / "report.html").write_text(
            render_html(report), encoding="utf-8")
        # An intra-filesystem directory rename makes the new JSON, previews and
        # mobile report appear together. The previous complete result is kept in
        # a temporary slot until the new directory is published successfully.
        old = work / "previous"
        if output.exists():
            output.replace(old)
        try:
            staged.replace(output)
        except OSError:
            if old.exists():
                old.replace(output)
            raise
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/encoder-benchmark"))
    parser.add_argument("--resolution", type=int, default=360, choices=(360, 720))
    parser.add_argument("--fps", type=int, default=12, choices=(12, 24, 30))
    parser.add_argument("--seconds", type=int, default=3, choices=(2, 3, 4))
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(args.output, resolution=args.resolution,
                               fps=args.fps, seconds=args.seconds)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(status=2, message=f"Benchmark unavailable: {exc}\n")
    summary = {"output": str(args.output),
               "profiles": [{"name": p["name"], "mean_ssim": p["mean_ssim"],
                             "size_bytes": p["total_size_bytes"],
                             "seconds": p["total_wall_seconds"]}
                            for p in report["profiles"]]}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
