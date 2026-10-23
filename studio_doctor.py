"""Offline, minimal preflight for the local Free Studio Android/desktop server.

Checks functional FFmpeg encoders, essential filters, ffprobe, eSpeak French
speech, and writeable output without reading user media, using shell commands,
or accessing the network.
"""
from __future__ import annotations

import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MIN_FREE_BYTES_WARNING = 1024 * 1024 * 1024  # large videos need headroom
REQUIRED_FILTERS = ("zoompan", "xfade", "subtitles", "drawtext", "loudnorm")



def _verify_full_render(work: Path, ffmpeg: str) -> None:
    """Render and validate a 2-scene private-free MP4 in a disposable folder.

    The generated media is synthetic. No uploaded/gallery files are opened,
    and all artifacts are removed by the caller's TemporaryDirectory.
    """
    from video_qa import probe, validate

    assets = work / "synthetic-inputs"
    assets.mkdir(mode=0o700)
    image = assets / "01-photo.jpg"
    clip = assets / "02-video.mp4"
    output = work / "finished.mp4"
    for ffmpeg_args in (
        ["-f", "lavfi", "-i", "testsrc2=s=360x640:r=1",
         "-frames:v", "1", "-update", "1", str(image)],
        ["-f", "lavfi", "-i", "testsrc2=s=180x320:r=12:d=6",
         "-an", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(clip)],
    ):
        subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel",
                        "error", "-y", *ffmpeg_args],
                       capture_output=True, check=True, timeout=40)
    if not image.is_file() or not clip.is_file():
        raise RuntimeError("Missing synthetic source")
    original = (image.read_bytes(), clip.read_bytes())
    command = [
        sys.executable, str(Path(__file__).resolve().with_name("free_studio.py")),
        "--topic", "Vérification locale", "--script",
        "Un paysage ensoleillé apparaît. Puis une caméra filme un autre paysage.",
        "--lang", "fr", "--resolution", "360", "--fps", "12",
        "--media-dir", str(assets), "--caption-style", "classic",
        "--transition", "auto", "--output", str(output),
    ]
    # When Android stops responding during a render, terminate the encoder
    # *process group*, not only the supervising Python process. Otherwise
    # orphan FFmpeg children may continue using battery, CPU and storage.
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               start_new_session=(os.name == "posix"))
    try:
        process.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate()
        raise RuntimeError("Synthetic render exceeded time limit") from None
    if process.returncode or not output.is_file() or output.stat().st_size < 10000:
        raise RuntimeError("Incomplete synthetic render")
    manifest = output.with_suffix(".json")
    subtitles = output.with_suffix(".srt")
    if not manifest.is_file() or not subtitles.is_file():
        raise RuntimeError("Missing render metadata or subtitles")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if len(metadata.get("segments", ())) != 2:
        raise RuntimeError("Incomplete synthetic storyboard")
    media = metadata.get("media", ())
    if (len(media) != 2 or
            [item.get("kind") for item in media] != ["image", "video"] or
            any(item.get("source") != "local" for item in media)):
        raise RuntimeError("Synthetic media not used")
    quality = validate(output, metadata, subtitles, probe(output))
    if not quality.get("ok"):
        raise RuntimeError("MP4, captions or narration failed QA")
    if (image.read_bytes(), clip.read_bytes()) != original:
        raise RuntimeError("Source media unexpectedly changed")


def diagnose(output_dir: Path, *, full_render: bool = False) -> dict:
    """Return deterministic, privacy-safe checks; do not upload or read media."""
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    if sys.version_info < (3, 11):
        add("python", "error", "Python 3.11 ou plus récent est nécessaire.")
    else:
        add("python", "ok", "Python compatible.")
    tools = {command: shutil.which(command) for command in ("ffmpeg", "ffprobe", "espeak")}
    for command, executable in tools.items():
        if not executable:
            add(command, "error", f"Commande {command} introuvable.")
        else:
            add(command, "ok", f"Commande {command} disponible.")

    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".mpt-doctor-", dir=output_dir) as folder:
            work = Path(folder)
            sample = work / "sample.mp4"
            voice = work / "speech.wav"
            add("storage_write", "ok", "Stockage accessible en écriture.")
            try:
                free_bytes = shutil.disk_usage(work).free
                if free_bytes < MIN_FREE_BYTES_WARNING:
                    add("storage_space", "warning", "Moins de 1 Gio libre : prévoir davantage d'espace pour les vidéos.")
                else:
                    add("storage_space", "ok", "Au moins 1 Gio d'espace libre.")
            except OSError:
                add("storage_space", "warning", "Espace libre non mesurable.")

            if tools["ffmpeg"]:
                try:
                    filters = subprocess.run(
                        [tools["ffmpeg"], "-hide_banner", "-filters"],
                        capture_output=True, text=True, timeout=15, check=True).stdout
                    available = {line.split()[1] for line in filters.splitlines()
                                 if len(line.split()) >= 2 and line.lstrip().startswith(("T", ".", "S", "C"))}
                    # Support FFmpeg versions where the first column uses various flags.
                    missing = [f for f in REQUIRED_FILTERS if f not in available]
                    if missing:
                        add("video_filters", "error", "Filtres FFmpeg manquants : " + ", ".join(missing) + ".")
                    else:
                        add("video_filters", "ok", "Filtres de mouvement, transition, texte et son disponibles.")
                except (OSError, subprocess.SubprocessError, UnicodeError):
                    add("video_filters", "error", "Impossible de consulter les filtres FFmpeg.")
                try:
                    subprocess.run([
                        tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=blue:s=240x426:r=12:d=0.5",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "96k", "-shortest", str(sample),
                    ], capture_output=True, timeout=35, check=True)
                    if not sample.is_file() or sample.stat().st_size < 1000:
                        raise ValueError("Empty output")
                except (OSError, subprocess.SubprocessError, ValueError):
                    add("h264_aac_encode", "error", "Échec d'encodage MP4 H.264/AAC avec FFmpeg.")
                else:
                    if tools["ffprobe"]:
                        try:
                            result = subprocess.run([
                                tools["ffprobe"], "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                                "-of", "json", str(sample),
                            ], capture_output=True, text=True, timeout=15, check=True)
                            streams = json.loads(result.stdout)["streams"]
                            actual = {(s.get("codec_type"), s.get("codec_name")) for s in streams}
                            if not {("video", "h264"), ("audio", "aac")} <= actual:
                                raise ValueError("Unexpected codecs")
                        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                            add("h264_aac_encode", "error", "MP4 encodé mais codecs H.264/AAC non vérifiables.")
                        else:
                            add("h264_aac_encode", "ok", "MP4 H.264/AAC réellement encodé et vérifié.")
                    else:
                        add("h264_aac_encode", "error", "FFprobe requis pour vérifier l'export H.264/AAC.")
            if tools["espeak"]:
                try:
                    subprocess.run([
                        tools["espeak"], "-v", "fr", "-s", "145", "-w", str(voice),
                        "Vérification du moteur vocal français.",
                    ], capture_output=True, timeout=20, check=True)
                    if not voice.is_file() or voice.stat().st_size < 1000:
                        raise ValueError("Empty voice")
                except (OSError, subprocess.SubprocessError, ValueError):
                    add("french_voice", "error", "Impossible de produire une voix française avec eSpeak.")
                else:
                    add("french_voice", "ok", "Voix française eSpeak générée localement.")
    except (OSError, ValueError):
        add("storage_write", "error", "Dossier de sortie inaccessible en écriture.")

    if full_render:
        if any(item["status"] == "error" for item in checks):
            add("full_render", "error", "Prérequis incomplets : montage de test impossible.")
        else:
            try:
                with tempfile.TemporaryDirectory(prefix=".mpt-full-doctor-",
                                                 dir=output_dir) as folder:
                    _verify_full_render(Path(folder), tools["ffmpeg"])
            except (OSError, ValueError, RuntimeError, TypeError,
                    subprocess.SubprocessError, json.JSONDecodeError):
                # No exception paths or private filenames in JSON diagnostics.
                add("full_render", "error", "Le montage complet de test a échoué.")
            else:
                add("full_render", "ok", "Deux scènes synthétiques, narration française, sous-titres et MP4 vérifiés.")

    issues = sum(item["status"] == "error" for item in checks)
    warnings = sum(item["status"] == "warning" for item in checks)
    return {"ok": issues == 0, "errors": issues, "warnings": warnings, "checks": checks}


def print_report(report: dict, *, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return
    print("Diagnostic Free Studio — hors ligne, sans accès aux médias privés")
    for item in report["checks"]:
        print(f"[{item['status'].upper()}] {item['name']} : {item['detail']}")
    if report["ok"]:
        print("Prérequis essentiels validés. Un rendu réel sur votre appareil reste à tester.")
    else:
        is_termux = "com.termux" in os.environ.get("PREFIX", "")
        command = "pkg install -y python ffmpeg espeak" if is_termux else "sudo apt-get install -y ffmpeg espeak"
        print("Vérifiez les erreurs ci-dessus. Pour réinstaller les outils : " + command)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Vérifier hors ligne les prérequis de rendu local")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "outputs" / "web")
    parser.add_argument("--json", action="store_true", help="Résumé JSON sans données personnelles")
    args = parser.parse_args(argv)
    result = diagnose(args.output_dir)
    print_report(result, json_output=args.json)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
