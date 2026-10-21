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

from media_assets import IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, VIDEO_EXTENSIONS, media_dimensions

from free_studio import ASPECTS, LANG_VOICES, ollama_script, split_script, template_script

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
MAX_REQUEST = 12_000
MAX_JOBS = 20
JOB_TIMEOUT = 1800
MAX_MEDIA_FILES = 24
MAX_MEDIA_BATCHES = 12
MAX_MEDIA_STORAGE = 800 * 1024 * 1024
MAX_VIDEO_UPLOAD = 350 * 1024 * 1024
STALE_EMPTY_BATCH_SECONDS = 24 * 60 * 60
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".mp4": "video/mp4", ".srt": "application/x-subrip; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


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
    visuals = value.get("visuals", "abstract")
    if visuals not in ("abstract", "commons", "local"):
        raise ValueError("Source des visuels invalide")
    speech_rate = value.get("speech_rate", 145)
    if type(speech_rate) is not int or not 80 <= speech_rate <= 220:
        raise ValueError("Vitesse de narration invalide")
    mute = value.get("mute", False)
    if type(mute) is not bool:
        raise ValueError("L'option sans voix doit être un booléen")
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
            "aspect": aspect, "resolution": resolution, "visuals": visuals, "speech_rate": speech_rate,
            "mute": mute, "tts": tts, "scene_queries": queries}


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
        self.upload_root = self.output_dir / "_uploaded_media"
        self.processes: dict[str, subprocess.Popen] = {}
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
                    record["status"] = "error"
                    record["error"] = "Rendu interrompu par l'arrêt du serveur Android. Relancez-le si nécessaire."
                    record["finished"] = int(time.time())
                # Avoid rewriting legacy recovered folders, which may have
                # different ownership or metadata layout.
                if state_file.exists() and record["status"] != data["status"]:
                    self._checkpoint(record)
                recovered.append(record)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        for job in sorted(recovered, key=lambda item: (item["created"], item["id"]))[-MAX_JOBS:]:
            self.jobs[job["id"]] = job

    def submit(self, raw: object) -> dict:
        job = validate_draft(raw)
        if job["tts"] == "piper" and not job["mute"] and not self.piper_model:
            raise ValueError("Le modèle vocal Piper n'est pas configuré")
        # Lock order always media_lock -> lock (same as delete_media_batch).
        # Validate all options and capacity BEFORE sealing an uploaded batch.
        with self.media_lock, self.lock:
            if job["visuals"] == "local":
                if job["media_batch"]:
                    directory = self.upload_root / job["media_batch"]
                    if not directory.is_dir() or not self._batch_files(directory):
                        raise ValueError("Importez au moins un média valide avant le rendu")
                elif not self.media_dir:
                    raise ValueError("Importez des vidéos ou configurez --media-dir")
            finished = [j for j in self.jobs.values()
                        if j["status"] in ("done", "error", "cancelled")]
            if len(self.jobs) - len(finished) >= MAX_JOBS:
                raise ValueError("File d'attente pleine. Réessayez après les rendus actifs")
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
                threading.Thread(target=self._worker, daemon=True, name="mpt-render-queue").start()
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
            if not self._safe_batch_directory(folder):
                continue
            # A failed, interrupted HTTP transfer can leave a hidden temp file.
            # Its age is checked independently of the enclosing batch.
            for candidate in folder.iterdir():
                if (candidate.name.startswith(".receiving-")
                        and candidate.is_file() and not candidate.is_symlink()
                        and candidate.stat().st_mtime < cutoff):
                    candidate.unlink()
            if ((folder / ".sealed").exists() or self._batch_files(folder)
                    or folder.stat().st_mtime >= cutoff):
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

    def _media_storage_locked(self) -> dict:
        """Snapshot under media_lock -> lock; only server-owned batch paths."""
        with self.lock:
            active = {j["parameters"].get("media_batch") for j in self.jobs.values()
                      if j["status"] in ("queued", "running")}
            completed = self._finished_source_batches_locked()
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
                ready = sealed and folder.name in completed and folder.name not in active
                batches.append({"id": folder.name, "bytes": size, "files": count,
                                "reclaimable": ready, "in_use": folder.name in active})
                total += size
        return {"bytes": total, "limit_bytes": MAX_MEDIA_STORAGE,
                "batch_count": len(batches),
                "reclaimable_bytes": sum(b["bytes"] for b in batches if b["reclaimable"]),
                "reclaimable_count": sum(b["reclaimable"] for b in batches),
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
            files = self._batch_files(folder)
            return {"id": ident, "files": [
                {"name": p.name, "bytes": p.stat().st_size} for p in files],
                "sealed": (folder / ".sealed").exists()}

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
            directory = self.upload_root / ident
            if not directory.is_dir() or (directory / ".sealed").exists():
                raise ValueError("Lot absent ou déjà utilisé pour un rendu")
            files = self._batch_files(directory)
            if len(files) >= MAX_MEDIA_FILES:
                raise ValueError("Maximum 24 médias par projet")
            # Include interrupted hidden .receiving-* files in the real
            # capacity budget: ignoring them could exhaust Android storage.
            used = sum(p.stat().st_size for batch in self.upload_root.iterdir()
                       if self._safe_batch_directory(batch)
                       for p in batch.iterdir() if p.is_file() and not p.is_symlink())
            if used + size > MAX_MEDIA_STORAGE:
                raise ValueError("Stockage média local plein (800 Mo maximum)")
            target = directory / f"{len(files) + 1:03d}_{name}"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, prefix=".receiving-",
                                                 suffix=suffix, delete=False) as output:
                    temporary = Path(output.name)
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(256 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Transfert incomplet : réessayez le fichier")
                        output.write(chunk)
                        remaining -= len(chunk)
                if not media_dimensions(temporary):
                    raise ValueError("Média illisible par FFprobe : import refusé")
                os.replace(temporary, target)
                return {"name": target.name, "bytes": size, "count": len(files) + 1}
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def delete_media_batch(self, ident: str) -> bool:
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            return False
        with self.media_lock:
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

    @staticmethod
    def public(job: dict) -> dict:
        metadata = job["metadata"] or {}
        return {"id": job["id"], "status": job["status"], "created": job["created"],
                "finished": job["finished"], "error": job["error"],
                "topic": job["parameters"]["topic"], "resolution": job["parameters"]["resolution"],
                "duration_seconds": metadata.get("duration_seconds"), "visual_mode": metadata.get("visual_mode"),
                "warnings": metadata.get("warnings", []), "has_credits": bool(metadata.get("media")),
                "segments": metadata.get("segments", []), "speech_engine": metadata.get("speech_engine")}

    def list_jobs(self) -> list[dict]:
        with self.lock:
            return [self.public(j) for j in reversed(list(self.jobs.values()))]

    def get(self, ident: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(ident)
            return self.public(job) if job else None

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
                if proc is not None:
                    try:
                        if os.name == "posix":
                            os.killpg(proc.pid, signal.SIGTERM)
                        else:
                            proc.terminate()
                    except ProcessLookupError:
                        pass
            return self.public(job)

    def _arguments(self, job: dict, dest: Path) -> list[str]:
        p = job["parameters"]
        args = [self.python_executable, str(ROOT / "free_studio.py"), "--topic", p["topic"],
                "--script-file", str(dest / "script.txt"), "--lang", p["lang"],
                "--aspect", p["aspect"], "--resolution", str(p["resolution"]),
                "--speech-rate", str(p["speech_rate"]), "--output", str(dest / "video.mp4")]
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
                self._checkpoint(queued)
            dest = self.output_dir / ident
            try:
                dest.mkdir(exist_ok=True)
                (dest / "script.txt").write_text(queued["parameters"]["script"], encoding="utf-8")
                with (dest / "render.log").open("wb") as log:
                    proc = subprocess.Popen(self._arguments(queued, dest), cwd=ROOT,
                                            stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=(os.name == "posix"))
                    with self.lock:
                        self.processes[ident] = proc
                        cancelled = queued["status"] == "cancelled"
                    if cancelled:
                        self.cancel(ident)
                    try:
                        code = proc.wait(timeout=JOB_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        self.cancel(ident)
                        try:
                            proc.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        raise RuntimeError("Délai maximal de rendu dépassé")
                with self.lock:
                    if queued["status"] == "cancelled":
                        continue
                if code != 0:
                    raise RuntimeError("Échec du rendu. Vérifiez FFmpeg, eSpeak et la disponibilité des médias")
                output = dest / "video.mp4"
                if not output.is_file() or output.stat().st_size < 1000:
                    raise RuntimeError("Fichier MP4 absent ou vide")
                metadata = json.loads((dest / "video.json").read_text(encoding="utf-8"))
                with self.lock:
                    queued["metadata"] = metadata
                    queued["status"] = "done"
                    self._checkpoint(queued)
            except Exception as exc:
                with self.lock:
                    if queued["status"] != "cancelled":
                        queued["error"] = str(exc)[:250]
                        queued["status"] = "error"
                        self._checkpoint(queued)
            finally:
                with self.lock:
                    queued["finished"] = int(time.time())
                    self._checkpoint(queued)
                    self.processes.pop(ident, None)

    def artifact(self, ident: str, name: str) -> Path | None:
        if name not in ("video.mp4", "video.srt", "video.json", "video.credits.txt"):
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

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.json_response(200, {"ok": True})
        if path.startswith("/api/"):
            if not self.require_auth():
                return
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
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(?:/(video\.mp4|video\.srt|video\.json|video\.credits\.txt))?", path)
            if match:
                ident, asset_name = match.groups()
                if asset_name:
                    item = self.server.jobs.artifact(ident, asset_name)
                    if not item:
                        return self.json_response(404, {"error": "Fichier indisponible"})
                    self._headers(200, MIME[item.suffix], item.stat().st_size, Content_Disposition=f'attachment; filename="{asset_name}"')
                    with item.open("rb") as stream:
                        shutil.copyfileobj(stream, self.wfile, length=256 * 1024)
                    return
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
        if not self.require_auth():
            return
        path = urlsplit(self.path).path
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", path)
        if match:
            cancelled = self.server.jobs.cancel(match.group(1))
            return self.json_response(200, cancelled) if cancelled else self.json_response(404, {"error": "Projet introuvable"})
        upload = re.fullmatch(r"/api/media/batches/([a-f0-9]{32})/files", path)
        if upload:
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
                        "/api/media/cleanup"):
            return self.json_response(404, {"error": "Introuvable"})
        try:
            payload = self.read_json()
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
    args = parser.parse_args(argv)
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
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
