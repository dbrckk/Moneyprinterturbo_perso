#!/usr/bin/env python3
"""Opt-in offline 6-scene Android benchmark with real MP4 exports and QA.

Synthetic media only: no gallery access, network, external models, or paid APIs.
A comparison renders the same project twice, allowing a serial/parallel stage
choice to be evaluated *on the user's own device*. It is intentionally not
part of the default server startup or ordinary CI suite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from free_studio import render, _scene_encoding_workers, _available_render_memory_bytes
from video_qa import probe, validate

SCRIPT = (
    "La lumière du matin apparaît sur le paysage. "
    "Une caméra accompagne les premiers mouvements. "
    "La ville change de couleur et d'ambiance. "
    "Les plans suivants révèlent de nouveaux détails. "
    "Le montage conserve le rythme et la narration. "
    "Enfin les images laissent place à une conclusion."
)


def ffmpeg(*argv: str) -> None:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-y", "-filter_threads", "1", *argv]
    completed = subprocess.run(command, capture_output=True, timeout=60)
    if completed.returncode:
        raise RuntimeError("Synthetic FFmpeg test fixture failed")


def write_synthetic_png(path: Path, variant: int, *, width: int = 360,
                        height: int = 640) -> None:
    """Create a valid RGB PNG with moving-like colour gradients via stdlib.

    No Pillow, gallery access, internet, or extra FFmpeg process needed.
    The pattern includes vertical and horizontal variation and is deliberately
    synthetic test content, never represented as a real photograph.
    """
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    compressor = zlib.compressobj(level=6)
    encoded = bytearray()
    for y in range(height):
        row = bytearray((0,))  # PNG scanline filter type 0.
        for x in range(width):
            row.extend(((x + variant * 47) % 256,
                        (y // 2 + variant * 61) % 256,
                        (x // 3 + y // 4 + variant * 27) % 256))
        encoded.extend(compressor.compress(row))
    encoded.extend(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", bytes(encoded)) + chunk(b"IEND", b""))


def make_fixtures(folder: Path) -> dict[str, str]:
    """Generate one H.264 motion clip and three photo patterns, then copies."""
    folder.mkdir(parents=True, exist_ok=True)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=240x426:r=12:d=3",
           "-an", "-c:v", "libx264", "-preset", "ultrafast",
           "-threads:v", "2", "-pix_fmt", "yuv420p",
           str(folder / "02-video.mp4"))
    for index, number in enumerate((1, 3, 5)):
        write_synthetic_png(folder / f"{number:02}-image.png", index)
    shutil.copyfile(folder / "02-video.mp4", folder / "04-video.mp4")
    shutil.copyfile(folder / "02-video.mp4", folder / "06-video.mp4")
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.iterdir()}


def benchmark(root: Path, *, resolution: int = 720,
              fps: int = 12, compare_workers: bool = False) -> dict:
    if resolution not in (360, 720) or fps not in (12, 24):
        raise ValueError("Long montage test: only 360/720p and 12/24 FPS supported")
    width, height = resolution, resolution * 16 // 9
    available = _available_render_memory_bytes()
    selected = _scene_encoding_workers(width, height, 6)
    if compare_workers and selected != 2:
        raise ValueError("Comparaison parallèle refusée : 2 Gio libres et 2 CPU requis")
    inputs = root / "fixtures"
    hashes = make_fixtures(inputs)
    workers = (1, 2) if compare_workers else (selected,)
    rows: list[dict] = []
    elapsed_values: list[float] = []
    for count in workers:
        target = root / f"finished-{count}.mp4"
        if count == 2 and compare_workers:
            latest = _available_render_memory_bytes()
            if latest is None or latest < 2 * 1024**3:
                raise ValueError("Mémoire insuffisante avant le deuxième rendu parallèle")
        started = time.perf_counter()
        with patch("free_studio._scene_encoding_workers", return_value=count):
            info = render(
                "Montage long local", SCRIPT, target, lang="fr",
                resolution=resolution, fps=fps, media_dir=inputs,
                mute=False, caption_style="classic", transition="fade",
                use_media_onepass=False)
        elapsed = time.perf_counter() - started
        elapsed_values.append(elapsed)
        quality = validate(target, info, target.with_suffix(".srt"), probe(target))
        if not quality["ok"]:
            raise RuntimeError("Synthetic long-render output failed quality validation")
        if len(info["segments"]) != 6 or len(info["media"]) != 6:
            raise RuntimeError("Long-render benchmark dropped scenes or input media")
        perf = info["render_performance"]
        if perf["compositing_pipeline"] != "fused-xfade":
            raise RuntimeError("Long-render benchmark did not exercise fused crossfades")
        rows.append({
            "workers": count, "total_seconds": round(elapsed, 3),
            "scene_encoding_seconds": perf["scene_encoding_seconds"],
            "actual_scene_encoding_workers": perf["scene_encoding_workers"],
            "final_encoding_seconds": perf["final_encoding_seconds"],
            "output_bytes": target.stat().st_size,
            "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "output_codec": quality["checks"]["video_codec"],
            "output_pixel_format": quality["checks"]["pixel_format"],
            "audio_codec": quality["checks"].get("audio_codec"),
            "duration_seconds": info["duration_seconds"],
            "scenes": len(info["segments"]), "QA": "passed",
        })
        print(f"{resolution}p · {count} worker(s) · {elapsed:.2f}s · QA passed", flush=True)
    if hashes != {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in inputs.iterdir()}:
        raise RuntimeError("Benchmark modified its original synthetic sources")
    # Comparing only elapsed time is unsafe: a faster pipeline could have
    # silently skipped media, frames or audio. Require the *entire* delivered
    # MP4 to be identical before reporting a speedup from worker concurrency.
    comparison = None
    if compare_workers:
        serial, parallel = rows
        identical = serial["output_sha256"] == parallel["output_sha256"]
        comparison = {
            "serial_seconds": serial["total_seconds"],
            "parallel_seconds": parallel["total_seconds"],
            "speedup_ratio": round(elapsed_values[0] /
                                   max(elapsed_values[1], 1e-9), 3),
            "elapsed_seconds_saved": round(serial["total_seconds"] -
                                           parallel["total_seconds"], 3),
            "output_byte_identical": identical,
        }
        if not identical:
            raise RuntimeError("Worker comparison produced different MP4 bytes; "
                               "no speedup may be recommended")
    report = {
        "source": "six fully synthetic local PNG/MP4 sources; not user footage",
        "environment": "local FFmpeg/eSpeak, no network; on-device only if run on the device",
        "resolution": resolution, "fps": fps,
        "memory_available_at_start_bytes": available,
        "selected_worker_count": selected,
        "comparison": comparison,
        "note": "Stage times are end-to-end CPU timings, not measured peak RAM, heat, battery or typical phone performance",
        "rows": rows,
    }
    (root / "long-montage-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=int, choices=(360, 720), default=720)
    parser.add_argument("--fps", type=int, choices=(12, 24), default=12)
    parser.add_argument("--compare-workers", action="store_true",
                        help="Rendu 720p deux fois (1 puis 2 workers) : peut chauffer le téléphone")
    parser.add_argument("--out-dir", type=Path,
                        help="Directory for generated synthetic media/results (otherwise auto-delete)")
    args = parser.parse_args(argv)
    if args.compare_workers and args.resolution != 720:
        parser.error("Comparaison des workers disponible seulement en 720p")
    if args.out_dir:
        folder = args.out_dir.expanduser().resolve()
        if folder.exists() and any(folder.iterdir()):
            parser.error("La destination doit être un dossier vide pour éviter d'effacer des fichiers")
        folder.mkdir(parents=True, exist_ok=True)
        report = benchmark(folder, resolution=args.resolution,
                           fps=args.fps, compare_workers=args.compare_workers)
    else:
        with tempfile.TemporaryDirectory(prefix="mpt-long-montage-") as folder:
            report = benchmark(Path(folder), resolution=args.resolution,
                               fps=args.fps, compare_workers=args.compare_workers)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
