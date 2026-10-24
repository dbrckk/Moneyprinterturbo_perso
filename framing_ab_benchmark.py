#!/usr/bin/env python3
"""Offline, reviewable A/B benchmark for geometric and motion-guided video crops.

All eight source videos are generated locally by FFmpeg and contain NO user footage.
The goal is regression evidence, not a trained detector or artistic-quality
score. A GitHub-hosted run measures a GitHub machine, never a phone.
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import replace
import html
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from fast_media import build_onepass_media_command
from media_assets import MediaAsset
from motion_framing import plan_motion_crops
from scene_framing import needs_video_soft_matte

FPS = 12
SECONDS = 2.5
FRAMES = round(FPS * SECONDS)
WIDTH, HEIGHT = 360, 640
SAMPLES_W, SAMPLES_H = 90, 160
# The categories and expected results are *curated synthetic scenarios*,
# not real-world model accuracy data. Changing the list needs review.
CASES = (
    ("sport-right", "Isolated moving athlete — right edge", "shift", 640, 480),
    ("sport-left", "Isolated moving athlete — left edge", "shift", 640, 480),
    ("global-camera-motion", "Busy moving camera / full frame", "center", 640, 480),
    ("two-athletes", "Two similarly active edge subjects", "center", 640, 480),
    ("dark-night", "Very low contrast nighttime movement", "center", 640, 480),
    ("blinking-sign", "Stationary flashing light at the edge", "center", 640, 480),
    ("panoramic", "Extremely wide footage, both edge subjects", "matte", 1280, 720),
    ("long-crossing", "Nine-second subject crosses from right to left", "matte", 640, 480),
)


def run_command(command: list[str], *, timeout: int = 90) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(
            "FFmpeg/FFprobe failed: " +
            completed.stderr[-1600:].decode("utf-8", errors="replace"))
    return completed


def scene_seconds(name: str) -> float:
    return 9.0 if name == "long-crossing" else SECONDS


def synthetic_video(name: str, destination: Path) -> None:
    """Generate fixed known-content test clips using only core FFmpeg filters."""
    specs = {entry[0]: entry for entry in CASES}
    if name not in specs:
        raise ValueError("Unsupported synthetic benchmark case")
    size = "1280x720" if name == "panoramic" else "640x480"
    seconds = scene_seconds(name)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    base = ("testsrc2" if name == "global-camera-motion" else
            "color=c=0x090909" if name == "dark-night" else "color=c=gray")
    source_filter = (f"testsrc2=s={size}:r={FPS}:d={seconds + .5}" if name == "global-camera-motion"
                     else f"{base}:s={size}:r={FPS}:d={seconds + .5}")
    cmd += ["-f", "lavfi", "-i", source_filter]
    overlay = None
    if name in ("sport-right", "sport-left", "dark-night", "two-athletes",
                "panoramic", "blinking-sign", "long-crossing"):
        tint = "0x121212" if name == "dark-night" else "red"
        block_width = 180 if name == "panoramic" else 78
        block_height = 270 if name == "panoramic" else 160
        cmd += ["-f", "lavfi", "-i",
                f"color=c={tint}:s={block_width}x{block_height}:r={FPS}:d={seconds + .5}"]
        if name in ("two-athletes", "panoramic"):
            cmd += ["-f", "lavfi", "-i",
                    f"color=c=red:s={block_width}x{block_height}:r={FPS}:d=3"]
        if name == "long-crossing":
            overlay = ("[0:v][1:v]overlay="
                       "x='if(lt(t,4.5),520+35*sin(t*4),45+30*sin(t*4))':"
                       "y=150:shortest=1,format=yuv420p[v]")
        elif name == "panoramic":
            overlay = ("[0:v][1:v]overlay=x=30:y=220:shortest=1[p];"
                       "[p][2:v]overlay=x=1070:y=220:shortest=1,"
                       "format=yuv420p[v]")
        elif name == "two-athletes":
            overlay = ("[0:v][1:v]overlay=x='45+30*sin(t*4)':y=150:shortest=1[p];"
                       "[p][2:v]overlay=x='520-30*sin(t*4)':y=150:shortest=1,"
                       "format=yuv420p[v]")
        elif name == "sport-left":
            overlay = ("[0:v][1:v]overlay=x='45+30*sin(t*4)':y=150:"
                       "shortest=1,format=yuv420p[v]")
        elif name == "blinking-sign":
            overlay = ("[0:v][1:v]overlay=x=520:y=150:shortest=1:"
                       "enable='lt(mod(t,1),0.5)',format=yuv420p[v]")
        else:
            overlay = ("[0:v][1:v]overlay=x='520+35*sin(t*4)':y=150:"
                       "shortest=1,format=yuv420p[v]")
    if overlay is not None:
        cmd += ["-filter_complex", overlay, "-map", "[v]"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-frames:v", str(round(seconds * FPS)), "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
            str(destination)]
    run_command(cmd)
    if not destination.is_file() or destination.stat().st_size < 1800:
        raise RuntimeError("Empty synthetic benchmark source")


def probe_export(path: Path, *, seconds: float = SECONDS) -> dict[str, object]:
    result = run_command([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,avg_frame_rate,nb_frames",
        "-of", "json", str(path)], timeout=15)
    try:
        stream = json.loads(result.stdout)["streams"][0]
        frames = int(stream["nb_frames"])
        rate = stream["avg_frame_rate"].split("/")
        fps = float(rate[0]) / float(rate[1])
        if (frames != round(seconds * FPS) or int(stream["width"]) != WIDTH or
                int(stream["height"]) != HEIGHT or stream["codec_name"] != "h264"
                or abs(fps - FPS) > .01):
            raise ValueError("Encoded frames/format differs from expected")
        return {"frames": frames, "fps": fps, "bytes": path.stat().st_size}
    except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise RuntimeError("Missing or incorrect real MP4 output") from exc


def red_coverage(path: Path, *, seconds: float = 1.0) -> float:
    """Measure a synthetic RED marker's actual visible area in decoded MP4."""
    result = run_command([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{seconds:.3f}", "-i", str(path),
        "-frames:v", "1", "-vf",
        f"scale={SAMPLES_W}:{SAMPLES_H}:flags=area,format=rgb24",
        "-f", "rawvideo", "pipe:1"], timeout=30)
    pixels = result.stdout
    if len(pixels) != SAMPLES_W * SAMPLES_H * 3:
        raise RuntimeError("Decoded marker frame unavailable")
    hits = sum(pixels[i] > 140 and pixels[i+1] < 100
               and pixels[i+2] < 100
               for i in range(0, len(pixels), 3))
    return round(hits / (SAMPLES_W * SAMPLES_H), 5)


def expected_guard(case: dict[str, object]) -> bool:
    mode = case["expected"]
    actual = case["applied"]
    if mode != actual:
        return False
    if mode == "shift":
        # The known synthetic red marker is out of the center crop. A
        # successful guided crop must recover visible marker pixels.
        return (case["guided_red_fraction"] >
                case["center_red_fraction"] + .004)
    if mode == "matte":
        if case["name"] == "long-crossing":
            return (case["guided_red_fraction"] >
                    case["center_red_fraction"] + .002 and
                    case["late_guided_red_fraction"] >
                    case["late_center_red_fraction"] + .002)
        return (case["guided_red_fraction"] >
                case["center_red_fraction"] + .002)
    return True


def render_report(metrics: dict[str, object]) -> str:
    """Portable, offline, phone-friendly report with side-by-side actual MP4s."""
    escape = lambda value: html.escape(str(value), quote=True)
    panels = []
    for case in metrics["cases"]:
        name = escape(case["name"])
        why = escape(case["description"])
        videos = []
        for label, key in (("A · cadrage centré", "center_video"),
                           ("B · cadrage automatique", "guided_video")):
            videos.append(
                '<figure><figcaption>' + escape(label) + '</figcaption>'
                '<video controls muted playsinline preload="metadata"'
                ' src="' + escape(case[key]) + '"></video></figure>')
        status = "Règle validée" if case["passes_expected_guard"] else "À examiner"
        panels.append(
            '<section><h2>' + why + '</h2><p><strong>' + escape(status) +
            '</strong> · mode prévu : ' + escape(case["expected"]) +
            ' · obtenu : ' + escape(case["applied"]) +
            ' · proportion rouge visible A : ' + escape(case["center_red_fraction"]) +
            ' · B : ' + escape(case["guided_red_fraction"]) +
            (' · fin A : ' + escape(case["late_center_red_fraction"]) +
             ' · fin B : ' + escape(case["late_guided_red_fraction"]) if
             case.get("late_center_red_fraction") is not None else '') +
            '</p><div class="pair">' + ''.join(videos) + '</div></section>')
    css = """
    body{background:#101624;color:#f8fafc;font:16px system-ui,sans-serif;
    margin:0 auto;max-width:1050px;padding:16px;line-height:1.5}
    h1{font-size:1.6rem}h2{font-size:1.14rem;margin:0 0 10px}
    p{color:#cbd5e1}section{border:1px solid #3a4559;border-radius:12px;
    padding:14px;margin:18px 0}.pair{display:grid;
    grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    figure{margin:0}video{width:100%;max-height:470px;background:#000;
    border-radius:8px}figcaption{padding:8px 0;color:#dbeafe}
    @media(max-width:650px){.pair{grid-template-columns:1fr}}
    """
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy"'
            ' content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;;'
            ' media-src &#39;self&#39;;">'
            '<title>MoneyPrinter — A/B cadrage réel</title><style>' + css +
            '</style></head><body><h1>Comparaison A/B du cadrage</h1>'
            '<p>Huit séquences <strong>synthétiques</strong> avec contenus '
            'connus : mouvement sportif latéral, caméra mobile, deux sujets, '
            'faible lumière, lumière clignotante, panorama et déplacement de bord à bord sur neuf secondes. '
            'Deux vrais MP4 par cas. La mesure rouge porte sur des marqueurs '
            'de test et n’est <strong>ni une métrique de qualité artistique '
            'ni une précision sur des personnes réelles</strong>. '
            'Aucune vidéo privée, réseau, IA payante ou télémétrie.</p>'
            + ''.join(panels) +
            '<p>Les temps de traitement mesurés sur GitHub Actions '
            'ne prédisent pas ceux du téléphone Android.</p></body></html>')


def run_benchmark(output: Path) -> dict:
    """Run a fixed-cost seven-scene A/B; atomic publish or preserve previous."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and FFprobe required")
    if output.expanduser().is_symlink():
        raise ValueError("Refusing symlink benchmark destination")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if not output.is_dir():
            raise ValueError("Benchmark path is not a directory")
        existing = output / "metrics.json"
        if any(output.iterdir()) and (not existing.is_file() or
                '"benchmark_kind": "synthetic-framing-ab-v1"' not in
                existing.read_text(encoding="utf-8", errors="replace")[:2500]):
            raise ValueError("Refusing to overwrite unrelated output")
    with tempfile.TemporaryDirectory(prefix="mpt-framing-ab-", dir=output.parent) as temp:
        work = Path(temp)
        staged = work / "finished"
        videos = staged / "videos"
        videos.mkdir(parents=True)
        rows = []
        start = time.monotonic()
        for name, title, expected, sw, sh in CASES:
            source = work / f"{name}.mp4"
            seconds = scene_seconds(name)
            synthetic_video(name, source)
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            asset = MediaAsset(
                file=source, kind="video", source_url="local",
                license="generated-synthetic", author="benchmark",
                title=title, source_width=sw, source_height=sh)
            # Old production crop deliberately ignores known source geometry;
            # this baseline is *only* for generated benchmark footage.
            baseline = replace(asset, source_width=None, source_height=None)
            chosen, notes = plan_motion_crops(
                [asset], [seconds], width=WIDTH, height=HEIGHT)
            guided = chosen[0]
            applied = ("matte" if needs_video_soft_matte(
                guided, width=WIDTH, height=HEIGHT) else
                "shift" if guided.crop_horizontal_anchor is not None else "center")
            relative_center = f"videos/{name}-center.mp4"
            relative_guided = f"videos/{name}-guided.mp4"
            timings = []
            for selected, destination in ((baseline, staged / relative_center),
                                          (guided, staged / relative_guided)):
                command = build_onepass_media_command(
                    [selected], [seconds], width=WIDTH, height=HEIGHT,
                    fps=FPS, transition="cut", visual_filters=["format=yuv420p"],
                    audio=None, output=destination, total=seconds)
                t0 = time.monotonic()
                run_command(command, timeout=120)
                timings.append(round(time.monotonic() - t0, 3))
                probe_export(destination, seconds=seconds)
            row = {
                "name": name, "description": title, "expected": expected,
                "seconds": seconds,
                "applied": applied, "anchor": guided.crop_horizontal_anchor,
                "center_video": relative_center, "guided_video": relative_guided,
                "center_red_fraction": red_coverage(staged / relative_center),
                "guided_red_fraction": red_coverage(staged / relative_guided),
                "late_center_red_fraction": red_coverage(staged / relative_center, seconds=7.5) if seconds > 7 else None,
                "late_guided_red_fraction": red_coverage(staged / relative_guided, seconds=7.5) if seconds > 7 else None,
                "encode_wall_seconds": {"center": timings[0], "guided": timings[1]},
                "source_intact": hashlib.sha256(source.read_bytes()).hexdigest() == source_digest,
                "notes": notes,
            }
            row["passes_expected_guard"] = expected_guard(row)
            rows.append(row)
        report = {
            "benchmark_kind": "synthetic-framing-ab-v1",
            "description": "Actual FFmpeg synthetic side-by-side A/B; not real-world accuracy",
            "fps": FPS, "seconds": SECONDS, "width": WIDTH, "height": HEIGHT,
            "cases": rows, "cases_evaluated": len(rows),
            "expected_guards_passed": sum(x["passes_expected_guard"] for x in rows),
            "duration_wall_seconds": round(time.monotonic() - start, 3),
            "human_reviews_completed": 0,
            "production_source_unchanged": True,
            "battery_or_thermal_data": False,
        }
        (staged / "metrics.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (staged / "report.html").write_text(
            render_report(report), encoding="utf-8")
        previous = work / "old"
        if output.exists():
            output.replace(previous)
        try:
            staged.replace(output)
        except OSError:
            if previous.exists():
                previous.replace(output)
            raise
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/framing-ab-benchmark"))
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(args.output)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Framing benchmark unavailable: {exc}\n")
    print(json.dumps({
        "cases_evaluated": result["cases_evaluated"],
        "expected_guards_passed": result["expected_guards_passed"],
        "seconds": result["duration_wall_seconds"],
        "decisions": [{"case": x["name"], "expected": x["expected"],
                       "actual": x["applied"], "passed": x["passes_expected_guard"]}
                      for x in result["cases"]],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
