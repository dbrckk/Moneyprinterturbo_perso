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

    def submit(self, raw: object) -> dict:
        job = validate_draft(raw)
        if job["visuals"] == "local" and not job["media_batch"] and not self.media_dir:
            raise ValueError("Importez des vidéos ou configurez --media-dir")
        if job["tts"] == "piper" and not job["mute"] and not self.piper_model:
            raise ValueError("Le modèle vocal Piper n'est pas configuré")
        # Lock ordering matches delete_media_batch (media_lock -> lock).
        # Seal only after every other validation succeeds and queue capacity
        # is confirmed; rejected jobs must not consume an imported batch.
        with self.media_lock:
            if job["media_batch"]:
                directory = self.upload_root / job["media_batch"]
                if not directory.is_dir() or not self._batch_files(directory):
                    raise ValueError("Importez au moins un média valide avant le rendu")
            with self.lock:
                # Keep a bounded history; never silently discard pending jobs.
            finished = [j for j in self.jobs.values() if j["status"] in ("done", "error", "cancelled")]
            while len(self.jobs) >= MAX_JOBS and finished:
                old = finished.pop(0)
                self.jobs.pop(old["id"], None)
                # Deliberately do not delete output files: user may still own the link.
                if len(self.jobs) >= MAX_JOBS:
                    raise ValueError("File d'attente pleine. Réessayez après les rendus actifs")
                if job["media_batch"]:
                    (self.upload_root / job["media_batch"] / ".sealed").touch(exist_ok=True)
                ident = uuid4().hex
            record = {"id": ident, "status": "queued", "created": int(time.time()),
                      "finished": None, "error": None, "parameters": job, "metadata": None}
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

    def create_media_batch(self) -> str:
        with self.media_lock:
            self.upload_root.mkdir(parents=True, exist_ok=True)
            if sum(p.is_dir() for p in self.upload_root.iterdir()) >= MAX_MEDIA_BATCHES:
                raise ValueError("Limite de lots atteinte : supprimez un ancien import")
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
            used = sum(p.stat().st_size for batch in self.upload_root.iterdir()
                       if batch.is_dir() for p in self._batch_files(batch))
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
            dest = self.output_dir / ident
            try:
                dest.mkdir(exist_ok=False)
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
            except Exception as exc:
                with self.lock:
                    if queued["status"] != "cancelled":
                        queued["error"] = str(exc)[:250]
                        queued["status"] = "error"
            finally:
                with self.lock:
                    queued["finished"] = int(time.time())
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
        if path not in ("/api/draft", "/api/jobs", "/api/media/batches"):
            return self.json_response(404, {"error": "Introuvable"})
        try:
            payload = self.read_json()
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
