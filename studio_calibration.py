"""Safe, opt-in offline H.264 calibration for the local Android web studio.

One benchmark at a time, never concurrent with active studio renders. Its
subprocess runs on this device, not on GitHub, and is killed on shutdown or
timeout. This service never changes the user's encoder settings automatically.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

from encoder_local_advice import advise
from encoder_profile_benchmark import config_ok

CALIBRATION_SECONDS = 2
CALIBRATION_TIMEOUT = 300
CALIBRATION_FPS = 24  # The local studio's current final-render default FPS.
ALLOWED_RESOLUTIONS = (360, 720)


class LocalCalibration:
    def __init__(self, output_root: Path, *,
                 python_executable: str | None = None,
                 script: Path | None = None):
        self.output = output_root.resolve() / "_encoder_calibration"
        self.script = script or Path(__file__).resolve().parent / "encoder_profile_benchmark.py"
        self.python_executable = python_executable or sys.executable
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._closed = False
        self._state: dict[str, object] = {
            "status": "idle", "resolution": None, "fps": CALIBRATION_FPS,
            "suggested_profile": None, "advice": None, "error": None,
            "started": None, "finished": None,
        }

    def status(self) -> dict[str, object]:
        with self._lock:
            return copy.deepcopy(self._state)

    def is_running(self) -> bool:
        with self._lock:
            return self._state["status"] == "running"

    def start(self, *, resolution: int, fps: int = CALIBRATION_FPS) -> dict[str, object]:
        if (type(resolution) is not int or resolution not in ALLOWED_RESOLUTIONS
                or type(fps) is not int or fps != CALIBRATION_FPS
                or not config_ok(resolution, fps, CALIBRATION_SECONDS)):
            raise ValueError("Calibration limitée à 360p ou 720p, 24 images/s")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg et FFprobe sont requis pour calibrer le téléphone")
        with self._lock:
            if self._closed:
                raise RuntimeError("Le studio s'arrête : calibration impossible")
            if self._state["status"] == "running":
                raise RuntimeError("Une calibration est déjà en cours")
            self._state = {
                "status": "running", "resolution": resolution, "fps": fps,
                "suggested_profile": None, "advice": None, "error": None,
                "started": int(time.time()), "finished": None,
            }
            self._thread = threading.Thread(
                target=self._work, args=(resolution, fps),
                daemon=True, name="mpt-encoder-calibration")
            self._thread.start()
            return self.status()

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        # The Python benchmark may have exited while its child FFmpeg still
        # runs. Signal the entire isolated process group, even if the leader
        # has already exited, and reap the parent when possible.
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # Force-stop still-living grandchildren (or the wrapper) after grace.
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.poll() is None:
            proc.kill()
        if proc.poll() is None:
            proc.wait(timeout=4)

    def _measure(self, resolution: int, fps: int) -> dict[str, object]:
        # No user file is read, no URL is opened, no shell is used.
        # The child processes FFmpeg on synthetic scenes only.
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.script = self.script.resolve()
        stderr_path = self.output.parent / ".encoder-calibration-stderr.log"
        if stderr_path.is_symlink():
            raise RuntimeError("Journal de calibration non sécurisé")
        with stderr_path.open("wb") as log:
            proc = subprocess.Popen(
                [self.python_executable, str(self.script),
                 "--output", str(self.output), "--resolution", str(resolution),
                 "--fps", str(fps), "--seconds", str(CALIBRATION_SECONDS)],
                cwd=self.script.parent, stdout=subprocess.DEVNULL, stderr=log,
                start_new_session=(os.name == "posix"),
            )
            with self._lock:
                self._process = proc
                cancelled = self._closed
            if cancelled:
                self._terminate(proc)
                raise RuntimeError("Calibration interrompue : arrêt du studio")
            try:
                code = proc.wait(timeout=CALIBRATION_TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                self._terminate(proc)
                raise RuntimeError("Calibration trop longue, interrompue pour protéger le téléphone") from exc
            finally:
                with self._lock:
                    if self._process is proc:
                        self._process = None
            if code != 0:
                raise RuntimeError("Échec de la calibration locale. Vérifiez FFmpeg et l'espace disponible")

        manifest = self.output / "metrics.json"
        if (manifest.is_symlink() or not manifest.is_file()
                or manifest.stat().st_size > 1024 * 1024):
            raise RuntimeError("Rapport de calibration absent ou non valide")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (data.get("resolution") != resolution or data.get("fps") != fps
                or data.get("seconds") != CALIBRATION_SECONDS):
            raise RuntimeError("Rapport de calibration incompatible")
        recommendation = advise(
            data, width=resolution, height=round(resolution * 16 / 9),
            fps=fps, duration=15.0, scenes=3)
        return recommendation

    def _work(self, resolution: int, fps: int) -> None:
        try:
            advice = self._measure(resolution, fps)
            with self._lock:
                if not self._closed:
                    self._state.update(
                        status="done", suggested_profile=advice["suggested_profile"],
                        advice=advice, finished=int(time.time()))
        except Exception as exc:
            # A worker failure must never leave the UI stuck at "running".
            with self._lock:
                if not self._closed:
                    self._state.update(
                        status="error", error=str(exc)[:230], finished=int(time.time()))
        finally:
            with self._lock:
                self._process = None

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            active = self._process
            worker = self._thread
            if self._state["status"] == "running":
                self._state.update(status="cancelled", error="Arrêt du studio",
                                   finished=int(time.time()))
        if active is not None:
            self._terminate(active)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=7)
