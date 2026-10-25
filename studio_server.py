#!/usr/bin/env python3
"""Local, dependency-free Web studio for the existing Free Studio renderer.

The server binds to loopback by default; LAN mode requires an explicit token.
No paid APIs, shell execution, arbitrary file-serving or uploaded executable files.
"""
from __future__ import annotations

import argparse
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unicodedata
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from media_assets import (IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, VIDEO_EXTENSIONS,
                          _local_media_order, media_dimensions)
from capture_order import order_media_by_capture

from free_studio import ASPECTS, LANG_VOICES, ollama_script, split_script, template_script
from encoder_profiles import ENCODER_PROFILES
from render_progress import PROGRESS_NAME, read_progress
from render_review import summarize_completed_render
from render_wake_lock import RenderWakeLock
from studio_calibration import LocalCalibration, CALIBRATION_FPS

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
MAX_REQUEST = 12_000
MAX_JOBS = 20
JOB_TIMEOUT = 1800
MAX_MEDIA_FILES = 24
MAX_MEDIA_BATCHES = 12
MAX_MEDIA_STORAGE = 800 * 1024 * 1024
# Conservative free-space floor for final output and FFmpeg intermediates.
# These are not predictions of a particular video's required disk usage.
RENDER_MIN_FREE_BYTES = {360: 256 * 1024**2, 720: 512 * 1024**2,
                         1080: 1024 * 1024**2}
# Once encoding starts the free-space floor necessarily decreases. Abort only
# at a smaller emergency threshold, checked periodically rather than on every
# process poll, to leave space for Android and job-status checkpoints.
RENDER_CRITICAL_FREE_BYTES = 96 * 1024**2
RENDER_DISK_CHECK_SECONDS = 3.0
# Leave room for the OS, metadata, and an interrupted upload's cleanup.
UPLOAD_MIN_REMAINING_BYTES = 128 * 1024**2
MAX_VIDEO_UPLOAD = 350 * 1024 * 1024
STALE_EMPTY_BATCH_SECONDS = 24 * 60 * 60
ABANDONED_MEDIA_SECONDS = 7 * 24 * 60 * 60
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".mp4": "video/mp4", ".srt": "application/x-subrip; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


def parse_video_range(header: str, size: int) -> tuple[int, int] | None:
    """Parse a *single* HTTP byte range with inclusive ends.

    Browsers seek with ``bytes=start-`` or ``bytes=-suffix``. Multipart
    requests and malformed/unsatisfiable ranges are deliberately rejected
    (416) instead of constructing an unbounded in-memory response.
    """
    if not isinstance(header, str) or type(size) is not int or size <= 0:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not (match.group(1) or match.group(2)):
        return None
    try:
        first, last = match.groups()
        if not first:
            suffix = int(last)
            return (max(0, size - suffix), size - 1) if suffix > 0 else None
        start = int(first)
        end = min(size - 1, int(last)) if last else size - 1
        if start >= size or end < start:
            return None
        return start, end
    except ValueError:
        return None


def validate_draft(value: object, *, allow_empty_script: bool = False) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Objet JSON requis")
    topic = value.get("topic", "")
    if not isinstance(topic, str) or not 2 <= len(topic.strip()) <= 140 or "\n" in topic or "\r" in topic:
        raise ValueError("Sujet invalide : 2 à 140 caractères, une ligne")
    lang = value.get("lang", "fr")
    if lang not in LANG_VOICES:
        raise ValueError("Langue non prise en charge")
    script = value.get("script", "")
    if not isinstance(script, str):
        raise ValueError("La narration doit être du texte")
    if script.strip():
        sentences = split_script(script)
    elif allow_empty_script:
        sentences = []
    else:
        raise ValueError("Saisissez ou générez une narration avant le rendu")
    aspect = value.get("aspect", "9:16")
    if aspect not in ASPECTS:
        raise ValueError("Format vidéo invalide")
    resolution = value.get("resolution", 360)
    if type(resolution) is not int or resolution not in (360, 720, 1080):
        raise ValueError("Résolution invalide")
    encoder_profile = value.get("encoder_profile", "balanced")
    if type(encoder_profile) is not str or encoder_profile not in ENCODER_PROFILES:
        raise ValueError("Profil vidéo H.264 non pris en charge")
    visuals = value.get("visuals", "abstract")
    if visuals not in ("abstract", "commons", "local"):
        raise ValueError("Source des visuels invalide")
    speech_rate = value.get("speech_rate", 145)
    if type(speech_rate) is not int or not 80 <= speech_rate <= 220:
        raise ValueError("Vitesse de narration invalide")
    mute = value.get("mute", False)
    if type(mute) is not bool:
        raise ValueError("L'option sans voix doit être un booléen")
    keep_awake = value.get("keep_awake", False)
    if type(keep_awake) is not bool:
        raise ValueError("L'option anti-veille Termux doit être un booléen")
    tts = value.get("tts", "espeak")
    if tts not in ("espeak", "piper"):
        raise ValueError("Moteur vocal inconnu")
    queries = value.get("scene_queries", [])
    if isinstance(queries, str):
        queries = [q.strip() for q in queries.split("\n") if q.strip()]
    if not isinstance(queries, list) or any(not isinstance(q, str) or not 1 <= len(q.strip()) <= 110 or "|" in q for q in queries):
        raise ValueError("Les mots-clés doivent être des lignes de texte")
    if queries and (visuals != "commons" or len(queries) != len(sentences)):
        raise ValueError("Les mots-clés Wikimedia doivent correspondre exactement aux scènes")
    media_batch = value.get("media_batch", "")
    if not isinstance(media_batch, str) or (media_batch and
                                             not re.fullmatch(r"[a-f0-9]{32}", media_batch)):
        raise ValueError("Identifiant du lot de médias invalide")
    if media_batch and visuals != "local":
        raise ValueError("Le lot de médias nécessite le mode local")
    return {"media_batch": media_batch, "topic": topic.strip(), "lang": lang, "script": script.strip(), "sentences": sentences,
            "aspect": aspect, "resolution": resolution, "encoder_profile": encoder_profile,
            "visuals": visuals, "speech_rate": speech_rate,
            "mute": mute, "tts": tts, "scene_queries": queries,
            "keep_awake": keep_awake}


class Jobs:
    def __init__(self, output_dir: Path, *, media_dir: Path | None = None,
                 piper_model: Path | None = None, python_executable: str | None = None):
        self.output_dir = output_dir.resolve()
        self.media_dir = media_dir.resolve() if media_dir else None
        self.piper_model = piper_model.resolve() if piper_model else None
        self.python_executable = python_executable or sys.executable
        self.jobs: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.media_lock = threading.RLock()
        # Protect reservations only for brief bookkeeping. Never hold the
        # media lock while a phone streams hundreds of megabytes or ffprobe
        # examines the file: status, history and other imports must respond.
        self._uploads_in_progress: dict[str, tuple[int, Path]] = {}
        self.upload_root = self.output_dir / "_uploaded_media"
        self.processes: dict[str, subprocess.Popen] = {}
        self.calibration: LocalCalibration | None = None
        self.worker_thread: threading.Thread | None = None
        self._stopping = False
        self.running = False
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._restore_jobs()

    def _checkpoint(self, job: dict) -> None:
        """Persist *status only*; never duplicate narration, prompts or tokens.

        Atomic rename prevents a killed Android process from leaving a partial
        JSON record. Full render settings stay in this process and script.txt.
        """
        directory = self.output_dir / job["id"]
        directory.mkdir(mode=0o700, exist_ok=True)
        summary = {key: job.get(key) for key in ("id", "status", "created", "finished", "error")}
        summary["topic"] = job["parameters"]["topic"]
        summary["resolution"] = job["parameters"]["resolution"]
        summary["media_batch"] = job["parameters"].get("media_batch", "")
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=directory,
                    prefix=".state-", suffix=".tmp", delete=False) as stream:
                tmp = Path(stream.name)
                os.chmod(tmp, 0o600)
                json.dump(summary, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, directory / ".job_state.json")
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def _restore_jobs(self) -> None:
        """Recover past outputs after a Termux/process restart, never auto-rerun."""
        recovered = []
        for folder in self.output_dir.iterdir():
            if (folder.is_symlink() or not folder.is_dir()
                    or not re.fullmatch(r"[a-f0-9]{32}", folder.name)):
                continue
            state_file = folder / ".job_state.json"
            if state_file.is_symlink():
                continue
            try:
                if state_file.is_file():
                    if state_file.stat().st_size > 4096:
                        continue
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or data.get("id") != folder.name:
                        continue
                    if data.get("status") not in ("queued", "running", "done", "error", "cancelled"):
                        continue
                    if (type(data.get("created")) is not int
                            or data["created"] < 0):
                        continue
                    title = data.get("topic")
                    if not isinstance(title, str) or len(title) > 140:
                        continue
                    resolution = data.get("resolution")
                    if type(resolution) is not int or resolution not in (360, 720, 1080):
                        continue
                    media_batch = data.get("media_batch", "")
                    if not isinstance(media_batch, str) or (media_batch and
                           not re.fullmatch(r"[a-f0-9]{32}", media_batch)):
                        continue
                    record = {
                        "id": folder.name, "created": data["created"],
                        "status": data["status"], "finished": data.get("finished"),
                        "error": data.get("error"),
                        "parameters": {"topic": title, "resolution": resolution,
                                       "media_batch": media_batch},
                        "metadata": None,
                    }
                else:
                    # Older builds did not write a state checkpoint. Recover
                    # legitimate completed MP4 + manifest pairs if possible.
                    if not (folder / "video.mp4").is_file():
                        continue
                    record = {
                        "id": folder.name, "created": int((folder / "video.mp4").stat().st_mtime),
                        "status": "done", "finished": None, "error": None,
                        "parameters": {"topic": "Vidéo restaurée", "resolution": 360,
                                       "media_batch": ""},
                        "metadata": None,
                    }
                manifest = folder / "video.json"
                video = folder / "video.mp4"
                if record["status"] == "done":
                    if (manifest.is_symlink() or video.is_symlink()
                            or not manifest.is_file() or not video.is_file()
                            or video.stat().st_size < 1000
                            or manifest.stat().st_size > 2 * 1024 * 1024):
                        record["status"] = "error"
                        record["error"] = "Export incomplet après redémarrage"
                    else:
                        payload = json.loads(manifest.read_text(encoding="utf-8"))
                        if not isinstance(payload, dict) or "segments" not in payload:
                            raise ValueError("Invalid recovered MP4 manifest")
                        record["metadata"] = payload
                if record["status"] in ("queued", "running"):
                    # Android may kill the server after the renderer atomically
                    # published every export but before the final status
                    # checkpoint. Recover that *completed* bundle instead of
                    # losing a fully generated MP4. Never trust a partial
                    # .render-scratch/result.mp4 or restart an encoder here.
                    bundle = (self._recover_published_bundle(folder)
                              if record["status"] == "running" else None)
                    if bundle is not None:
                        record["status"] = "done"
                        record["metadata"] = bundle
                        record["error"] = None
                    else:
                        record["status"] = "error"
                        record["error"] = "Rendu interrompu par l'arrêt du serveur Android. Relancez-le si nécessaire."
                    record["finished"] = int(time.time())
                # Avoid rewriting legacy recovered folders, which may have
                # different ownership or metadata layout.
                if state_file.exists() and record["status"] != data["status"]:
                    self._checkpoint(record)
                # No renderer from the previous server instance can still be
                # supervised here. After checkpointing recovery, remove only
                # its private scratch and progress status. Never touch MP4,
                # SRT, manifest, source uploads or a symlinked scratch folder.
                if record["status"] in ("done", "error", "cancelled"):
                    self._reclaim_orphan_render_scratch(folder)
                recovered.append(record)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        for job in sorted(recovered, key=lambda item: (item["created"], item["id"]))[-MAX_JOBS:]:
            self.jobs[job["id"]] = job

    @staticmethod
    def _reclaim_orphan_render_scratch(folder: Path) -> None:
        """Bound startup cleanup to private files in a recovered job folder.

        Do not follow a symlink out of the job, even if an interrupted render
        left one behind. Ignore cleanup errors so a full/readonly filesystem
        never prevents the user from seeing previously completed exports.
        """
        scratch = folder / ".render-scratch"
        try:
            if scratch.is_dir() and not scratch.is_symlink():
                shutil.rmtree(scratch)
        except OSError:
            pass
        progress = folder / PROGRESS_NAME
        try:
            if progress.is_file() or progress.is_symlink():
                progress.unlink()
        except OSError:
            pass

    @staticmethod
    def _recover_published_bundle(folder: Path) -> dict | None:
        """Recover only complete, credible exports after an Android kill.

        The video renderer publishes the MP4, SRT, credits and JSON together.
        FFprobe must confirm that the final MP4 is playable; a file named
        video.mp4 or a half-written manifest is never sufficient evidence.
        """
        try:
            names = ("video.mp4", "video.srt", "video.credits.txt", "video.json")
            files = [folder / name for name in names]
            if any(p.is_symlink() or not p.is_file() or p.stat().st_size == 0
                   for p in files):
                return None
            mp4, srt, credits, manifest = files
            if (mp4.stat().st_size < 1000 or manifest.stat().st_size > 2 * 1024 * 1024
                    or srt.stat().st_size > 2 * 1024 * 1024
                    or credits.stat().st_size > 2 * 1024 * 1024):
                return None
            info = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(info, dict) or not isinstance(info.get("segments"), list)
                    or not isinstance(info.get("duration_seconds"), (int, float))
                    or not 0 < info["duration_seconds"] < JOB_TIMEOUT):
                return None
            checked = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name,width,height",
                 "-of", "json", str(mp4)], capture_output=True,
                timeout=8, check=False)
            if checked.returncode != 0:
                return None
            streams = json.loads(checked.stdout).get("streams", [])
            if not any(isinstance(stream, dict) and stream.get("codec_name") == "h264"
                       and type(stream.get("width")) is int and stream["width"] > 0
                       and type(stream.get("height")) is int and stream["height"] > 0
                       for stream in streams):
                return None
            return info
        except (OSError, ValueError, TypeError, json.JSONDecodeError,
                subprocess.SubprocessError):
            return None

    def _ensure_render_disk_space(self, resolution: int) -> None:
        """Reject a render before it can exhaust an Android/Termux volume.

        Both the private TMPDIR and final MP4 are under output_dir. This is
        only a minimum free-space guard, *not* an estimate of render size;
        the check is repeated when a queued job actually starts because
        other apps and renders can consume storage while it waits.
        """
        floor = RENDER_MIN_FREE_BYTES[resolution]
        try:
            free = shutil.disk_usage(self.output_dir).free
        except OSError as exc:
            raise ValueError("Espace libre impossible à vérifier. Vérifiez le stockage avant le rendu") from exc
        if free < floor:
            available_mib = free // (1024**2)
            floor_mib = floor // (1024**2)
            raise ValueError(
                f"Espace insuffisant pour le rendu {resolution}p : "
                f"{available_mib} Mio libres, seuil prudent {floor_mib} Mio. "
                "Libérez de la place puis réessayez ; aucun import ne sera supprimé")

    def _ensure_running_render_disk_space(self) -> None:
        """Fail a live encode before its scratch files exhaust Android storage.

        Unlike the resolution preflight, this emergency floor permits an
        already-running encode to consume its reserved headroom. A vanished
        volume or unreadable free-space report is a render failure, never an
        excuse to delete imported sources or completed exports.
        """
        try:
            free = shutil.disk_usage(self.output_dir).free
        except OSError as exc:
            raise RuntimeError(
                "Stockage impossible à surveiller pendant le rendu. "
                "L'encodage a été interrompu pour protéger le téléphone") from exc
        if free < RENDER_CRITICAL_FREE_BYTES:
            raise RuntimeError(
                f"Stockage critique pendant le rendu : {free // (1024**2)} Mio libres, "
                f"minimum de sécurité {RENDER_CRITICAL_FREE_BYTES // (1024**2)} Mio. "
                "Encodage arrêté ; libérez de la place et réessayez. "
                "Les vidéos importées restent conservées")

    def submit(self, raw: object) -> dict:
        job = validate_draft(raw)
        if job["tts"] == "piper" and not job["mute"] and not self.piper_model:
            raise ValueError("Le modèle vocal Piper n'est pas configuré")
        # Lock order always media_lock -> lock (same as delete_media_batch).
        # Validate all options and capacity BEFORE sealing an uploaded batch.
        with self.media_lock, self.lock:
            if self._stopping:
                raise ValueError("Serveur en cours d'arrêt : nouveau rendu refusé")
            if self.calibration is not None and self.calibration.is_running():
                raise ValueError("Calibration H.264 en cours : attendez sa fin avant le rendu")
            if job["visuals"] == "local":
                if job["media_batch"]:
                    if job["media_batch"] in self._uploads_in_progress:
                        raise ValueError("Import en cours : terminez l'envoi avant le rendu")
                    directory = self.upload_root / job["media_batch"]
                    if not directory.is_dir() or not self._batch_files(directory):
                        raise ValueError("Importez au moins un média valide avant le rendu")
                elif not self.media_dir:
                    raise ValueError("Importez des vidéos ou configurez --media-dir")
            finished = [j for j in self.jobs.values()
                        if j["status"] in ("done", "error", "cancelled")]
            if len(self.jobs) - len(finished) >= MAX_JOBS:
                raise ValueError("File d'attente pleine. Réessayez après les rendus actifs")
            # Reject low-space requests *before* creating a checkpoint or
            # sealing imported media. The browser can retry after cleanup.
            self._ensure_render_disk_space(job["resolution"])
            ident = uuid4().hex
            record = {"id": ident, "status": "queued", "created": int(time.time()),
                      "finished": None, "error": None, "parameters": job, "metadata": None}
            # Ensure a successful enqueue can survive a process restart.
            self._checkpoint(record)
            try:
                if job["media_batch"]:
                    (self.upload_root / job["media_batch"] / ".sealed").touch(exist_ok=True)
            except OSError:
                shutil.rmtree(self.output_dir / ident, ignore_errors=True)
                raise
            while len(self.jobs) >= MAX_JOBS and finished:
                old = finished.pop(0)
                self.jobs.pop(old["id"], None)
                # Finished exports remain on disk and can be recovered later.
            self.jobs[ident] = record
            if not self.running:
                self.running = True
                self.worker_thread = threading.Thread(
                    target=self._worker, daemon=True, name="mpt-render-queue")
                self.worker_thread.start()
            return self.public(record)

    @staticmethod
    def _batch_files(directory: Path) -> list[Path]:
        return sorted((p for p in directory.iterdir()
                       if p.is_file() and not p.is_symlink() and not p.name.startswith(".")),
                      key=lambda p: p.name)

    @staticmethod
    def _safe_batch_directory(path: Path) -> bool:
        return bool(re.fullmatch(r"[a-f0-9]{32}", path.name)
                    and path.is_dir() and not path.is_symlink())

    def _prune_stale_empty_media_locked(self) -> None:
        """Only remove old, *empty*, unsealed imports and orphan temp uploads.

        Called under media_lock. Never automatically delete a user media file or
        a sealed render source, even if the server has been closed for months.
        """
        if not self.upload_root.is_dir():
            return
        cutoff = time.time() - STALE_EMPTY_BATCH_SECONDS
        for folder in self.upload_root.iterdir():
            if not self._safe_batch_directory(folder) or folder.name in self._uploads_in_progress:
                continue
            # A failed, interrupted HTTP transfer can leave a hidden temp file.
            # Its age is checked independently of the enclosing batch.
            for candidate in folder.iterdir():
                if (candidate.name.startswith(".receiving-")
                        and candidate.is_file() and not candidate.is_symlink()
                        and candidate.stat().st_mtime < cutoff):
                    candidate.unlink()
            # Remove the directory only if it is genuinely empty. Unknown
            # hidden files or symlinks may belong to the user.
            if folder.stat().st_mtime >= cutoff or any(folder.iterdir()):
                continue
            shutil.rmtree(folder)

    def _finished_source_batches_locked(self) -> set[str]:
        """Find finished sources, including older jobs evicted from RAM.

        Completion requires the checkpoint AND the original MP4 + manifest on
        disk; an interrupted/error job is not deemed safe for reclamation.
        """
        completed: set[str] = set()
        for folder in self.output_dir.iterdir():
            if not self._safe_batch_directory(folder):
                continue
            checkpoint = folder / ".job_state.json"
            video = folder / "video.mp4"
            manifest = folder / "video.json"
            if (checkpoint.is_symlink() or video.is_symlink() or manifest.is_symlink()
                    or not checkpoint.is_file() or checkpoint.stat().st_size > 4096
                    or not video.is_file() or video.stat().st_size < 1000
                    or not manifest.is_file() or not 2 <= manifest.stat().st_size <= 2 * 1024 * 1024):
                continue
            try:
                saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or saved.get("status") != "done":
                    continue
                ident = saved.get("media_batch")
                if isinstance(ident, str) and re.fullmatch(r"[a-f0-9]{32}", ident):
                    completed.add(ident)
            except (ValueError, UnicodeError, OSError):
                continue
        return completed

    def _batch_job_references_locked(self) -> dict[str, list[tuple[str, float]]]:
        """Resolve source use from disk and memory, not just the last 20 jobs.

        A batch without a validated on-disk reference is never treated as a
        failed render; it can qualify only as an old, unsealed draft.
        """
        references: dict[str, list[tuple[str, float]]] = {}
        for folder in self.output_dir.iterdir():
            if not self._safe_batch_directory(folder):
                continue
            state = folder / ".job_state.json"
            if state.is_symlink() or not state.is_file():
                continue
            try:
                if not 2 <= state.stat().st_size <= 4096:
                    continue
                data = json.loads(state.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("id") != folder.name:
                    continue
                ident = data.get("media_batch")
                status = data.get("status")
                if (isinstance(ident, str) and re.fullmatch(r"[a-f0-9]{32}", ident)
                        and status in ("queued", "running", "done", "error", "cancelled")):
                    references.setdefault(ident, []).append((status, state.stat().st_mtime))
            except (OSError, ValueError, UnicodeError):
                continue
        with self.lock:
            for job in self.jobs.values():
                ident = job["parameters"].get("media_batch")
                if isinstance(ident, str) and re.fullmatch(r"[a-f0-9]{32}", ident):
                    checkpoint = self.output_dir / job["id"] / ".job_state.json"
                    last_change = (checkpoint.stat().st_mtime if checkpoint.is_file()
                                   and not checkpoint.is_symlink() else time.time())
                    references.setdefault(ident, []).append((job["status"], last_change))
        return references

    @staticmethod
    def _last_batch_change(folder: Path) -> float:
        # A freshly updated file, even inside an old folder, prevents pruning.
        return max([folder.stat().st_mtime] +
                   [p.lstat().st_mtime for p in folder.iterdir()])

    def _media_storage_locked(self) -> dict:
        """Snapshot under media_lock -> lock; only server-owned batch paths."""
        with self.lock:
            active = {j["parameters"].get("media_batch") for j in self.jobs.values()
                      if j["status"] in ("queued", "running")}
            completed = self._finished_source_batches_locked()
        references = self._batch_job_references_locked()
        cutoff = time.time() - ABANDONED_MEDIA_SECONDS
        batches = []
        total = 0
        if self.upload_root.is_dir():
            for folder in self.upload_root.iterdir():
                if not self._safe_batch_directory(folder):
                    continue
                regular = [p for p in folder.iterdir()
                           if p.is_file() and not p.is_symlink()]
                size = sum(p.stat().st_size for p in regular)
                count = len(self._batch_files(folder))
                sealed = (folder / ".sealed").is_file()
                uploading = folder.name in self._uploads_in_progress
                ready = (sealed and folder.name in completed and
                         folder.name not in active and not uploading)
                job_refs = references.get(folder.name, [])
                statuses = [status for status, _ in job_refs]
                last_activity = max([self._last_batch_change(folder)] +
                                    [changed for _, changed in job_refs])
                # Never remove active, completed or unclassified sealed media.
                # A failed batch must have a recorded, *only* terminal failure.
                abandoned_reason = None
                if (not ready and folder.name not in active and not uploading
                        and count and last_activity < cutoff):
                    if not sealed and not statuses:
                        abandoned_reason = "draft"
                    elif (sealed and statuses and
                          all(s in ("error", "cancelled") for s in statuses)):
                        abandoned_reason = "failed"
                batches.append({"id": folder.name, "bytes": size, "files": count,
                                "reclaimable": ready, "in_use": folder.name in active,
                                "uploading": uploading,
                                "abandoned": bool(abandoned_reason),
                                "abandoned_reason": abandoned_reason})
                total += size
        return {"bytes": total, "limit_bytes": MAX_MEDIA_STORAGE,
                "batch_count": len(batches),
                "reclaimable_bytes": sum(b["bytes"] for b in batches if b["reclaimable"]),
                "reclaimable_count": sum(b["reclaimable"] for b in batches),
                "abandoned_bytes": sum(b["bytes"] for b in batches if b["abandoned"]),
                "abandoned_count": sum(b["abandoned"] for b in batches),
                "batches": sorted(batches, key=lambda b: b["id"])}

    def media_storage(self) -> dict:
        with self.media_lock:
            return self._media_storage_locked()

    def cleanup_finished_media(self) -> dict:
        """Explicit action: delete uploaded sources from completed renders only.

        Final MP4, subtitle, project checkpoint, and manifest are untouched.
        A fresh snapshot under the write lock blocks racing render submissions.
        """
        with self.media_lock:
            before = self._media_storage_locked()
            deleted = []
            reclaimed = 0
            for batch in before["batches"]:
                if not batch["reclaimable"]:
                    continue
                folder = self.upload_root / batch["id"]
                if not self._safe_batch_directory(folder):
                    continue
                shutil.rmtree(folder)
                deleted.append(batch["id"])
                reclaimed += batch["bytes"]
            return {"deleted_batches": deleted, "reclaimed_bytes": reclaimed,
                    "storage": self._media_storage_locked()}

    def cleanup_abandoned_media(self, *, protect_batch: str = "") -> dict:
        """Explicit, confirmed-by-client removal of unused uploads >7 days.

        Never remove active render sources, finished render sources, unsealed
        newer drafts, or any directory selected in the current browser.
        """
        if not isinstance(protect_batch, str) or (protect_batch and
                not re.fullmatch(r"[a-f0-9]{32}", protect_batch)):
            raise ValueError("Identifiant de projet protégé invalide")
        with self.media_lock:
            snapshot = self._media_storage_locked()
            deleted = []
            reclaimed = 0
            for batch in snapshot["batches"]:
                if not batch["abandoned"] or batch["id"] == protect_batch:
                    continue
                folder = self.upload_root / batch["id"]
                if not self._safe_batch_directory(folder):
                    continue
                shutil.rmtree(folder)
                deleted.append(batch["id"])
                reclaimed += batch["bytes"]
            return {"deleted_batches": deleted, "reclaimed_bytes": reclaimed,
                    "storage": self._media_storage_locked()}

    def create_media_batch(self) -> str:
        with self.media_lock:
            self.upload_root.mkdir(parents=True, exist_ok=True)
            self._prune_stale_empty_media_locked()
            if sum(self._safe_batch_directory(p) for p in self.upload_root.iterdir()) >= MAX_MEDIA_BATCHES:
                raise ValueError("Limite de lots atteinte : nettoyez les imports terminés")
            ident = uuid4().hex
            (self.upload_root / ident).mkdir(mode=0o700)
            return ident

    def batch_info(self, ident: str) -> dict | None:
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            return None
        with self.media_lock:
            folder = self.upload_root / ident
            if not folder.is_dir():
                return None
            files, ordering = order_media_by_capture(
                self._batch_files(folder), fallback_key=_local_media_order)
            return {"id": ident, "files": [
                {"name": p.name, "bytes": p.stat().st_size} for p in files],
                "ordering": ordering, "sealed": (folder / ".sealed").exists()}

    def _reserved_unwritten_media_bytes_locked(self) -> int:
        """Count declared, not-yet-written upload bytes towards total capacity.

        Actual bytes, including orphan .receiving files, are accounted for
        by the normal disk scan. Only the remaining declared bytes need to be
        added; counting the full reservation would double-count partials.
        """
        reserved = 0
        for size, partial in self._uploads_in_progress.values():
            try:
                already_written = partial.stat().st_size
            except OSError:
                already_written = 0
            reserved += max(0, size - already_written)
        return reserved

    def save_media(self, ident: str, encoded_name: str, size: int, stream) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            raise ValueError("Lot de médias inconnu")
        try:
            name = unquote(encoded_name, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Nom du fichier invalide") from exc
        if (not 1 <= len(name) <= 120 or name in (".", "..")
                or name.startswith(".") or "/" in name or "\\" in name
                or any(unicodedata.category(ch).startswith("C") for ch in name)):
            raise ValueError("Nom du fichier invalide")
        suffix = Path(name).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
            raise ValueError("Format non pris en charge : JPEG, PNG, WebP, MP4, MOV, WebM, MKV")
        limit = MAX_IMAGE_BYTES if suffix in IMAGE_EXTENSIONS else MAX_VIDEO_UPLOAD
        if not isinstance(size, int) or size <= 0 or size > limit:
            raise ValueError("Taille de média invalide ou supérieure à la limite")
        with self.media_lock:
            if self.calibration is not None and self.calibration.is_running():
                raise ValueError("Calibration H.264 active : import suspendu pour protéger le téléphone")
            directory = self.upload_root / ident
            if not directory.is_dir() or (directory / ".sealed").exists():
                raise ValueError("Lot absent ou déjà utilisé pour un rendu")
            if ident in self._uploads_in_progress:
                raise ValueError("Import déjà en cours sur ce lot")
            files = self._batch_files(directory)
            if len(files) >= MAX_MEDIA_FILES:
                raise ValueError("Maximum 24 médias par projet")
            # Reserve the full *declared* size of all concurrent uploads,
            # while counting written partial files only once. Include orphan
            # .receiving files from interrupted Android processes as before.
            used = sum(p.stat().st_size for batch in self.upload_root.iterdir()
                       if self._safe_batch_directory(batch)
                       for p in batch.iterdir() if p.is_file() and not p.is_symlink())
            unwritten = self._reserved_unwritten_media_bytes_locked()
            if used + unwritten + size > MAX_MEDIA_STORAGE:
                raise ValueError("Stockage média local plein (800 Mo maximum)")
            # Multiple simultaneous uploads can each reserve their full size.
            # disk_usage.free already excludes bytes written by other uploads,
            # so reserve only their still-unwritten portion here.
            try:
                free = shutil.disk_usage(self.upload_root).free
            except OSError as exc:
                raise ValueError("Espace libre impossible à vérifier avant l'import") from exc
            if free < size + unwritten + UPLOAD_MIN_REMAINING_BYTES:
                raise ValueError(
                    "Espace insuffisant pour importer ce média : libérez du "
                    "stockage sur le téléphone puis réessayez. Les anciens "
                    "imports ne sont pas supprimés")
            target = directory / f"{len(files) + 1:03d}_{name}"
            # A unique upload per batch prevents duplicate numeric names and
            # stale response races; different batches may transfer concurrently.
            output = tempfile.NamedTemporaryFile(
                dir=directory, prefix=".receiving-", suffix=suffix, delete=False)
            temporary = Path(output.name)
            self._uploads_in_progress[ident] = (size, temporary)
        try:
            # The slow section deliberately runs WITHOUT media_lock.
            with output:
                remaining = size
                while remaining:
                    chunk = stream.read(min(256 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Transfert incomplet : réessayez le fichier")
                    output.write(chunk)
                    remaining -= len(chunk)
            if not media_dimensions(temporary):
                raise ValueError("Média illisible par FFprobe : import refusé")
            with self.media_lock:
                # Submit/delete cannot race an active reservation, and no
                # client can overwrite committed originals during validation.
                if not directory.is_dir() or (directory / ".sealed").exists():
                    raise ValueError("Lot supprimé ou déjà utilisé")
                os.replace(temporary, target)
                return {"name": target.name, "bytes": size, "count": len(files) + 1}
        finally:
            with self.media_lock:
                self._uploads_in_progress.pop(ident, None)
                temporary.unlink(missing_ok=True)

    def delete_media_batch(self, ident: str) -> bool:
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            return False
        with self.media_lock:
            if ident in self._uploads_in_progress:
                raise ValueError("Import en cours : suppression impossible")
            directory = self.upload_root / ident
            if not directory.is_dir():
                return False
            with self.lock:
                if any(j["status"] in ("queued", "running") and
                       j["parameters"].get("media_batch") == ident
                       for j in self.jobs.values()):
                    raise ValueError("Import utilisé par un rendu en cours")
            shutil.rmtree(directory)
            return True

    def public(self, job: dict) -> dict:
        metadata = job["metadata"] or {}
        progress = (read_progress(self.output_dir / job["id"] / PROGRESS_NAME)
                    if job["status"] == "running" else None)
        return {"progress": progress,
                "keep_awake_requested": bool(job["parameters"].get("keep_awake", False)),
                "keep_awake_active": bool(job.get("_wake_lock_active", False)
                                          if job["status"] == "running" else False),
                "id": job["id"], "status": job["status"], "created": job["created"],
                "finished": job["finished"], "error": job["error"],
                "topic": job["parameters"]["topic"], "resolution": job["parameters"]["resolution"],
                "duration_seconds": metadata.get("duration_seconds"), "visual_mode": metadata.get("visual_mode"),
                "warnings": metadata.get("warnings", []), "has_credits": bool(metadata.get("media")),
                "has_publication": bool(isinstance(metadata.get("publication"), dict)
                    and metadata["publication"].get("filename") == "video.publish.txt"),
                "segments": metadata.get("segments", []), "speech_engine": metadata.get("speech_engine"),
                "render_review": (summarize_completed_render(metadata)
                                  if job["status"] == "done" else None)}

    def list_jobs(self) -> list[dict]:
        with self.lock:
            return [self.public(j) for j in reversed(list(self.jobs.values()))]

    def get(self, ident: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(ident)
            return self.public(job) if job else None

    @staticmethod
    def _signal_render_group(proc: subprocess.Popen, sig: int) -> None:
        """Signal the *whole* isolated renderer process tree on Android/POSIX.

        FFmpeg is normally a child of free_studio.py, not the Popen leader.
        Killing only that leader leaves the actual encode consuming resources.
        """
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            # The leader/group already exited; cancellation is idempotent.
            pass

    @classmethod
    def _reap_render_group(cls, proc: subprocess.Popen) -> None:
        """Bound graceful shutdown; then kill and reap even surviving children.

        Always signal the process group after the leader has exited: FFmpeg may
        ignore SIGTERM while the Python wrapper has already terminated.
        """
        cls._signal_render_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        finally:
            cls._signal_render_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
            if proc.poll() is None:
                proc.wait(timeout=5)

    def cancel(self, ident: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(ident)
            if not job:
                return None
            if job["status"] in ("queued", "running"):
                job["status"] = "cancelled"
                job["finished"] = int(time.time())
                self._checkpoint(job)
            proc = self.processes.get(ident)
            if proc is not None and job["status"] == "cancelled":
                self._signal_render_group(proc, signal.SIGTERM)
            return self.public(job)

    def shutdown(self) -> None:
        """Cancel all queued/active renders and reap the FFmpeg process tree.

        Called when the Termux server is closed with Ctrl+C. Avoid leaving
        costly encoders behind after the HTTP listener has stopped. A worker
        may still be starting Popen, so first mark all jobs cancelled, then
        wait briefly for that worker to observe their state.
        """
        with self.lock:
            self._stopping = True
            active = [ident for ident, job in self.jobs.items()
                      if job["status"] in ("queued", "running")]
            worker = self.worker_thread
        for ident in active:
            self.cancel(ident)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=8)
            if worker.is_alive():
                with self.lock:
                    processes = list(self.processes.values())
                for proc in processes:
                    self._signal_render_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
                worker.join(timeout=3)

    def _arguments(self, job: dict, dest: Path) -> list[str]:
        p = job["parameters"]
        args = [self.python_executable, str(ROOT / "free_studio.py"), "--topic", p["topic"],
                "--script-file", str(dest / "script.txt"), "--lang", p["lang"],
                "--aspect", p["aspect"], "--resolution", str(p["resolution"]),
                "--speech-rate", str(p["speech_rate"]),
                "--encoder-profile", p.get("encoder_profile", "balanced"),
                "--output", str(dest / "video.mp4")]
        if p["mute"]:
            args.append("--mute")
        if p["tts"] == "piper" and not p["mute"]:
            args.extend(["--tts", "piper", "--piper-model", str(self.piper_model)])
        if p["visuals"] == "commons":
            args.append("--commons")
            if p["scene_queries"]:
                args.extend(["--scene-queries", "|".join(p["scene_queries"])])
        elif p["visuals"] == "local":
            source = ((self.upload_root / p["media_batch"]) if p["media_batch"]
                      else self.media_dir)
            args.extend(["--media-dir", str(source)])
        return args

    def _worker(self) -> None:
        while True:
            with self.lock:
                queued = next((j for j in self.jobs.values() if j["status"] == "queued"), None)
                if queued is None:
                    self.running = False
                    return
                queued["status"] = "running"
                ident = queued["id"]
                try:
                    self._checkpoint(queued)
                except OSError:
                    # A full or read-only Android volume must not kill the
                    # only worker and strand every later queued project.
                    queued["status"] = "error"
                    queued["error"] = "Stockage indisponible pour enregistrer le rendu"
                    queued["finished"] = int(time.time())
                    continue
            dest = self.output_dir / ident
            # Isolate FFmpeg/TTS intermediates inside the current job. If an
            # Android SIGKILL interrupts TemporaryDirectory's normal cleanup,
            # the supervising worker can still reclaim these private files.
            scratch = dest / ".render-scratch"
            wake_lock = RenderWakeLock(queued["parameters"].get("keep_awake", False))
            try:
                # A queue may sit idle while storage is consumed elsewhere.
                # In that case surface a job error without launching FFmpeg.
                self._ensure_render_disk_space(queued["parameters"]["resolution"])
                # Never delay a queued/low-storage rejection for Android wake lock.
                # A wake lock is best-effort and only requested by the creator.
                active = wake_lock.acquire()
                with self.lock:
                    queued["_wake_lock_active"] = active
                dest.mkdir(exist_ok=True)
                if scratch.is_symlink():
                    raise ValueError("Temporary render directory must not be a symlink")
                scratch.mkdir(mode=0o700, exist_ok=True)
                render_env = os.environ.copy()
                render_env["TMPDIR"] = str(scratch)
                render_env["MPT_STUDIO_PROGRESS"] = "1"
                (dest / PROGRESS_NAME).unlink(missing_ok=True)
                # User narration and FFmpeg diagnostics are private even if
                # Termux inherited a permissive process umask. Reject stale
                # symlinks before opening either sensitive file.
                script_path = dest / "script.txt"
                log_path = dest / "render.log"
                if script_path.is_symlink() or log_path.is_symlink():
                    raise ValueError("Render files must not be symlinks")
                with os.fdopen(os.open(script_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                                       getattr(os, "O_NOFOLLOW", 0), 0o600),
                               "w", encoding="utf-8") as script_stream:
                    os.chmod(script_path, 0o600)
                    script_stream.write(queued["parameters"]["script"])
                with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                                       getattr(os, "O_NOFOLLOW", 0), 0o600), "wb") as log:
                    os.chmod(log_path, 0o600)
                    proc = subprocess.Popen(self._arguments(queued, dest), cwd=ROOT,
                                            stdout=log, stderr=subprocess.STDOUT,
                                            env=render_env,
                                            start_new_session=(os.name == "posix"))
                    with self.lock:
                        self.processes[ident] = proc
                        cancelled = queued["status"] == "cancelled"
                    # The user may have cancelled while Popen was starting,
                    # before the process was present in self.processes.
                    if cancelled:
                        self._signal_render_group(proc, signal.SIGTERM)
                    deadline = time.monotonic() + JOB_TIMEOUT
                    next_disk_check = time.monotonic() + RENDER_DISK_CHECK_SECONDS
                    while not cancelled:
                        with self.lock:
                            cancelled = queued["status"] == "cancelled"
                        if cancelled:
                            break
                        now = time.monotonic()
                        # A running encode can fill its output volume even
                        # after submission passed the conservative preflight.
                        # Reap the entire FFmpeg process group on disk pressure;
                        # never delete completed exports or original uploads.
                        if now >= next_disk_check and proc.poll() is None:
                            try:
                                self._ensure_running_render_disk_space()
                            except RuntimeError:
                                self._reap_render_group(proc)
                                raise
                            next_disk_check = now + RENDER_DISK_CHECK_SECONDS
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            # Automatic timeout is a render *error*, not a
                            # user-initiated cancellation. Keep the correct
                            # reason visible in the job history and log.
                            self._reap_render_group(proc)
                            raise RuntimeError("Délai maximal de rendu dépassé")
                        try:
                            code = proc.wait(timeout=min(0.5, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    if cancelled:
                        self._reap_render_group(proc)
                        continue
                with self.lock:
                    cancelled = queued["status"] == "cancelled"
                if cancelled:
                    # The Python wrapper may exit on SIGTERM before our poll
                    # notices cancellation, leaving an encoder that ignored
                    # that signal in the otherwise-live process group.
                    self._reap_render_group(proc)
                    continue
                if code != 0:
                    self._reap_render_group(proc)
                    raise RuntimeError("Échec du rendu. Vérifiez FFmpeg, eSpeak et la disponibilité des médias")
                output = dest / "video.mp4"
                if not output.is_file() or output.stat().st_size < 1000:
                    raise RuntimeError("Fichier MP4 absent ou vide")
                metadata = json.loads((dest / "video.json").read_text(encoding="utf-8"))
                # Never publish `done` while the previous render's stage file
                # is still visible. The status should describe a finished
                # export, not a worker that has yet to clear live progress.
                (dest / PROGRESS_NAME).unlink(missing_ok=True)
                with self.lock:
                    # Final publication and cancellation are mutually
                    # exclusive, even when the request arrives during JSON
                    # parsing. Reap any leftover encoder outside the lock.
                    cancelled = queued["status"] == "cancelled"
                    if not cancelled:
                        queued["metadata"] = metadata
                        queued["status"] = "done"
                        self._checkpoint(queued)
                if cancelled:
                    self._reap_render_group(proc)
                    continue
            except Exception as exc:
                with self.lock:
                    if queued["status"] != "cancelled":
                        queued["error"] = str(exc)[:250]
                        queued["status"] = "error"
                        self._checkpoint(queued)
            finally:
                # Relinquish only a lock acquired by this job, on success,
                # failure, cancellation or shutdown via the worker's finally.
                wake_lock.release()
                with self.lock:
                    queued["_wake_lock_active"] = False
                # Parent-owned scratch is the only directory removed. Never
                # touch user-imported media or other jobs' completed exports.
                if scratch.is_dir() and not scratch.is_symlink():
                    shutil.rmtree(scratch, ignore_errors=True)
                (dest / PROGRESS_NAME).unlink(missing_ok=True)
                with self.lock:
                    queued["finished"] = int(time.time())
                    self._checkpoint(queued)
                    self.processes.pop(ident, None)

    def artifact(self, ident: str, name: str) -> Path | None:
        if name not in ("video.mp4", "video.srt", "video.json", "video.credits.txt",
                         "video.publish.txt"):
            return None
        with self.lock:
            j = self.jobs.get(ident)
            if not j or j["status"] != "done":
                return None
        candidate = self.output_dir / ident / name
        return candidate if candidate.is_file() else None


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "MPTFreeStudio/1.0"
    protocol_version = "HTTP/1.1"

    def _headers(self, status: int, content_type: str, content_length: int, **extra: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for k, v in extra.items():
            self.send_header(k.replace("_", "-"), v)
        self.end_headers()

    def json_response(self, status: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def same_origin(self) -> bool:
        host = self.headers.get("Host", "")
        if self.server.server_address[0] in ("127.0.0.1", "localhost", "::1"):
            # Reject DNS rebinding with an attacker-owned Host header.
            if host.lower() not in (f"127.0.0.1:{self.server.server_port}",
                                    f"localhost:{self.server.server_port}",
                                    f"[::1]:{self.server.server_port}"):
                return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
            return parsed.scheme in ("http", "https") and parsed.netloc.lower() == self.headers.get("Host", "").lower()
        except ValueError:
            return False

    def authorized(self) -> bool:
        token = self.server.auth_token
        if not token:
            return True
        value = self.headers.get("Authorization", "")
        return hmac.compare_digest(value, "Bearer " + token)

    def require_auth(self) -> bool:
        if not self.same_origin():
            self.json_response(HTTPStatus.FORBIDDEN, {"error": "Origine non autorisée"})
            return False
        if not self.authorized():
            self.json_response(HTTPStatus.UNAUTHORIZED, {"error": "Jeton d'accès requis"})
            return False
        return True

    def read_json(self) -> object:
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise ValueError("Content-Type application/json requis")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Longueur invalide") from exc
        if length < 2 or length > MAX_REQUEST:
            raise ValueError("Taille de requête invalide")
        try:
            return json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON invalide") from exc

    def serve_artifact(self, item: Path, filename: str) -> None:
        """Stream MP4 files with bounded memory, allowing native seeking.

        Only `Jobs.artifact()` can select a published deliverable. Keep all
        existing security checks in `do_GET` before this is reached.
        """
        if filename != "video.mp4":
            self._headers(200, MIME[item.suffix], item.stat().st_size,
                          Content_Disposition=f'attachment; filename="{filename}"')
            with item.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile, length=256 * 1024)
            return
        with item.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            requested = self.headers.get("Range")
            headers = {"Accept_Ranges": "bytes",
                       "Content_Disposition": 'inline; filename="video.mp4"'}
            if requested is not None:
                interval = parse_video_range(requested, size)
                if interval is None:
                    self._headers(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                                  "video/mp4", 0,
                                  Accept_Ranges="bytes",
                                  Content_Range=f"bytes */{size}")
                    return
                first, last = interval
                headers["Content_Range"] = f"bytes {first}-{last}/{size}"
                code = HTTPStatus.PARTIAL_CONTENT
            else:
                first, last = 0, size - 1
                code = HTTPStatus.OK
            remaining = last - first + 1
            self._headers(code, "video/mp4", remaining, **headers)
            stream.seek(first)
            try:
                while remaining:
                    chunk = stream.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # Android video controls cancel range requests on seek.
                return

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.json_response(200, {"ok": True})
        if path.startswith("/api/"):
            if not self.require_auth():
                return
            if path == "/api/encoder/calibration":
                return self.json_response(200, self.server.calibration.status())
            if path == "/api/config":
                return self.json_response(200, {"langs": list(LANG_VOICES), "aspects": list(ASPECTS),
                                                "has_local_media": bool(self.server.jobs.media_dir),
                                                "has_piper": bool(self.server.jobs.piper_model),
                                                "has_ollama": bool(self.server.ollama_model), "default_resolution": 360})
            if path == "/api/jobs":
                return self.json_response(200, {"jobs": self.server.jobs.list_jobs()})
            if path == "/api/media/storage":
                return self.json_response(200, self.server.jobs.media_storage())
            media = re.fullmatch(r"/api/media/batches/([a-f0-9]{32})", path)
            if media:
                details = self.server.jobs.batch_info(media.group(1))
                return self.json_response(200, details) if details else self.json_response(404, {"error": "Lot introuvable"})
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(?:/(video\.mp4|video\.srt|video\.json|video\.credits\.txt|video\.publish\.txt))?", path)
            if match:
                ident, asset_name = match.groups()
                if asset_name:
                    item = self.server.jobs.artifact(ident, asset_name)
                    if not item:
                        return self.json_response(404, {"error": "Fichier indisponible"})
                    return self.serve_artifact(item, asset_name)
                job = self.server.jobs.get(ident)
                if job:
                    return self.json_response(200, job)
            return self.json_response(404, {"error": "Introuvable"})
        static = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/app.css": "app.css"}.get(path)
        if static is None:
            return self.json_response(404, {"error": "Introuvable"})
        file = WEB / static
        data = file.read_bytes()
        self._headers(200, MIME[file.suffix], len(data))
        self.wfile.write(data)

    def do_POST(self) -> None:
        # Several endpoints intentionally do not consume a request body
        # (cancel/delete). Closing after POST prevents leftover JSON bytes
        # from being interpreted as a new HTTP/1.1 request on keep-alive.
        self.close_connection = True
        if not self.require_auth():
            return
        path = urlsplit(self.path).path
        if path == "/api/encoder/calibration":
            try:
                data = self.read_json()
                if (type(data) is not dict or set(data) != {"resolution", "fps"}):
                    raise ValueError("Choisissez une résolution et une cadence valides")
                jobs = self.server.jobs
                # Always acquire media_lock before jobs.lock, matching submit().
                # Release both before sending an HTTP response: slow mobile
                # clients must not block background rendering or cancellation.
                with jobs.media_lock, jobs.lock:
                    if jobs._stopping or any(
                            job["status"] in ("queued", "running")
                            for job in jobs.jobs.values()):
                        busy = "Un rendu est déjà actif. Terminez-le avant la calibration"
                    elif jobs._uploads_in_progress:
                        busy = "Import de média actif. Terminez-le avant la calibration"
                    else:
                        busy = None
                        result = self.server.calibration.start(
                            resolution=data["resolution"], fps=data["fps"])
                if busy:
                    return self.json_response(409, {"error": busy})
                return self.json_response(202, result)
            except (ValueError, RuntimeError, OSError) as exc:
                return self.json_response(400, {"error": str(exc)[:230]})
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", path)
        if match:
            cancelled = self.server.jobs.cancel(match.group(1))
            return self.json_response(200, cancelled) if cancelled else self.json_response(404, {"error": "Projet introuvable"})
        upload = re.fullmatch(r"/api/media/batches/([a-f0-9]{32})/files", path)
        if upload:
            # An abandoned phone connection cannot monopolize a reservation
            # indefinitely; apply a generous inactivity timeout per socket.
            self.connection.settimeout(90)
            # A failed upload must never leave unread body bytes on a persistent
            # connection, where they could be mistaken for the next request.
            self.close_connection = True
            if self.headers.get("Transfer-Encoding"):
                return self.json_response(400, {"error": "Transfert HTTP segmenté non pris en charge"})
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/octet-stream":
                return self.json_response(415, {"error": "Flux binaire requis"})
            try:
                length_text = self.headers.get("Content-Length", "")
                if not re.fullmatch(r"[1-9][0-9]*", length_text):
                    raise ValueError("Longueur de média invalide")
                result = self.server.jobs.save_media(
                    upload.group(1), self.headers.get("X-Media-Name", ""),
                    int(length_text), self.rfile)
                return self.json_response(201, result)
            except (ValueError, OSError) as exc:
                return self.json_response(400, {"error": str(exc)[:250]})
        removal = re.fullmatch(r"/api/media/batches/([a-f0-9]{32})/delete", path)
        if removal:
            try:
                deleted = self.server.jobs.delete_media_batch(removal.group(1))
                return self.json_response(200 if deleted else 404, {"deleted": deleted})
            except (ValueError, OSError) as exc:
                return self.json_response(409, {"error": str(exc)[:250]})
        if path not in ("/api/draft", "/api/jobs", "/api/media/batches",
                        "/api/media/cleanup", "/api/media/cleanup-abandoned"):
            return self.json_response(404, {"error": "Introuvable"})
        try:
            payload = self.read_json()
            if path == "/api/media/cleanup-abandoned":
                if (not isinstance(payload, dict) or
                        set(payload) - {"protect_batch"}):
                    raise ValueError("Requête de nettoyage invalide")
                return self.json_response(200, self.server.jobs.cleanup_abandoned_media(
                    protect_batch=payload.get("protect_batch", "")))
            if path == "/api/media/cleanup":
                if payload != {}:
                    raise ValueError("Requête de nettoyage invalide")
                return self.json_response(200, self.server.jobs.cleanup_finished_media())
            if path == "/api/media/batches":
                if payload != {}:
                    raise ValueError("Objet de création de lot invalide")
                return self.json_response(201, {"id": self.server.jobs.create_media_batch()})
            if path == "/api/draft":
                job = validate_draft(payload, allow_empty_script=True)
                if self.server.ollama_model:
                    script = ollama_script(job["topic"], job["lang"], self.server.ollama_model, "http://127.0.0.1:11434")
                    mode = "local-ollama-ai"
                else:
                    script = template_script(job["topic"], job["lang"])
                    mode = "deterministic-template-not-ai"
                return self.json_response(200, {"script": script, "mode": mode, "segments": split_script(script)})
            return self.json_response(202, self.server.jobs.submit(payload))
        except (ValueError, OSError, RuntimeError) as exc:
            return self.json_response(400, {"error": str(exc)[:250]})

    def log_message(self, format: str, *args: object) -> None:
        # Avoid logging bearer tokens or user-authored prompt content.
        return


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], jobs: Jobs, *, auth_token: str = "", ollama_model: str = ""):
        self.jobs = jobs
        self.calibration = LocalCalibration(
            jobs.output_dir, python_executable=jobs.python_executable)
        jobs.calibration = self.calibration
        self.auth_token = auth_token
        self.ollama_model = ollama_model
        super().__init__(address, StudioHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free Studio : interface web locale")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 par défaut ; utiliser 0.0.0.0 pour le LAN")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.getenv("MPT_STUDIO_TOKEN", ""), help="Obligatoire hors boucle locale")
    parser.add_argument("--media-dir", type=Path, help="Images/vidéos locales autorisées, lecture seule")
    parser.add_argument("--piper-model", type=Path, help="Modèle Piper local ONNX (optionnel)")
    parser.add_argument("--ollama-model", default="", help="Modèle Ollama local pour écrire les scénarios")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "web")
    parser.add_argument("--doctor", action="store_true",
                        help="Vérifier FFmpeg, H.264/AAC, la voix française et le stockage, sans lancer le serveur")
    parser.add_argument("--doctor-json", action="store_true",
                        help="Effectuer le même diagnostic et produire un JSON sans données privées")
    parser.add_argument("--doctor-full", action="store_true",
                        help="Tester un vrai montage de 2 scènes synthétiques avec voix, sous-titres et MP4")
    parser.add_argument("--doctor-full-json", action="store_true",
                        help="Même test complet, rapport JSON sans fichiers personnels")
    args = parser.parse_args(argv)
    if args.doctor or args.doctor_json or args.doctor_full or args.doctor_full_json:
        from studio_doctor import diagnose, print_report
        report = diagnose(args.output_dir,
                          full_render=args.doctor_full or args.doctor_full_json)
        print_report(report, json_output=args.doctor_json or args.doctor_full_json)
        return 0 if report["ok"] else 1
    if args.host not in ("127.0.0.1", "localhost", "::1") and len(args.token) < 16:
        parser.error("Pour une écoute réseau, fournir --token (au moins 16 caractères aléatoires)")
    if args.media_dir and not args.media_dir.is_dir():
        parser.error("Dossier média absent")
    if args.piper_model and not (args.piper_model.is_file() and args.piper_model.with_suffix(".onnx.json").is_file()):
        parser.error("Modèle Piper .onnx et .onnx.json requis")
    for dependency in ("ffmpeg", "ffprobe", "espeak"):
        if not shutil.which(dependency):
            parser.error(f"Dépendance système manquante : {dependency}")
    jobs = Jobs(args.output_dir, media_dir=args.media_dir, piper_model=args.piper_model)
    server = StudioServer((args.host, args.port), jobs, auth_token=args.token, ollama_model=args.ollama_model)
    print(f"Free Studio Web : http://{args.host}:{server.server_port} — Ctrl+C pour arrêter", flush=True)
    previous_term_handler = None
    if os.name == "posix" and threading.current_thread() is threading.main_thread():
        previous_term_handler = signal.getsignal(signal.SIGTERM)
        def stop_on_term(_signum, _frame):
            # Gracefully cancel encoder subprocess groups when Android/Termux
            # requests shutdown instead of leaving an orphan FFmpeg process.
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop_on_term)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        if previous_term_handler is not None:
            signal.signal(signal.SIGTERM, previous_term_handler)
        try:
            server.calibration.shutdown()
            jobs.shutdown()
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
