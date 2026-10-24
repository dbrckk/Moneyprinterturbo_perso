"""Private, best-effort renderer stage heartbeat for the local Android studio.

There is deliberately no estimated percent or remaining duration: media
selection and H.264 encoding vary wildly by device, resolution and source.
Only a finite allowlist of stage identifiers and integer timestamps is stored.
Never put narration, filenames, tokens, user media, paths or FFmpeg logs here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import time

STAGES = ("voice", "assets", "scenes", "encode", "review", "publish")
PROGRESS_NAME = ".render_progress.json"
MAX_PROGRESS_BYTES = 512


class ProgressReporter:
    def __init__(self, output_dir: Path):
        self.path = output_dir / PROGRESS_NAME
        self.started = int(time.time())
        self.step = 0

    def emit(self, stage: str) -> None:
        """Atomic update: a broken status sidecar must never fail an export."""
        if stage not in STAGES:
            raise ValueError("Unknown render stage")
        step = STAGES.index(stage) + 1
        if step < self.step:
            raise ValueError("Render stages must not run backwards")
        self.step = step
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=".render-stage-", suffix=".tmp", delete=False) as stream:
                tmp = Path(stream.name)
                os.chmod(tmp, 0o600)
                json.dump({"stage": stage, "step": step, "steps": len(STAGES),
                           "started": self.started, "updated": int(time.time())}, stream)
                stream.flush()
            os.replace(tmp, self.path)
        except OSError:
            # A read-only/full temp filesystem must not damage a valid MP4.
            pass
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass


def read_progress(path: Path, *, now: int | None = None) -> dict[str, object] | None:
    """Read a bounded, regular progress file; never follow symlinks.

    Job output directories are UUID scoped. Still reject unsafe, forged or
    partially-written sidecars before exposing any fields through HTTP.
    """
    if path.name != PROGRESS_NAME:
        return None
    try:
        if path.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            details = os.fstat(stream.fileno())
            if not stat.S_ISREG(details.st_mode) or not 0 < details.st_size <= MAX_PROGRESS_BYTES:
                return None
            data = stream.read(MAX_PROGRESS_BYTES + 1)
        value = json.loads(data)
    except (OSError, ValueError, TypeError, UnicodeError):
        return None
    if type(value) is not dict or set(value) != {"stage", "step", "steps", "started", "updated"}:
        return None
    stage = value["stage"]
    if type(stage) is not str or stage not in STAGES:
        return None
    if type(value["step"]) is not int or value["step"] != STAGES.index(stage) + 1:
        return None
    if type(value["steps"]) is not int or value["steps"] != len(STAGES):
        return None
    started, updated = value["started"], value["updated"]
    current = int(time.time()) if now is None else now
    if (type(started) is not int or type(updated) is not int or
            started < 0 or updated < started or updated > current + 10 or
            started > current + 10):
        return None
    return {"stage": stage, "step": value["step"], "steps": len(STAGES),
            "elapsed_seconds": max(0, current - started)}
