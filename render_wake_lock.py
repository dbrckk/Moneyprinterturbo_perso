"""Explicit, bounded Termux wake-lock ownership during one local render.

Termux's wake lock is shared by the Termux application. This helper is never
enabled automatically: the creator explicitly opts in per rendered job.
"""
from __future__ import annotations

import shutil
import subprocess


class RenderWakeLock:
    """Best-effort paired acquire/release; failures never block a video."""

    def __init__(self, requested: bool):
        self.requested = requested is True
        self.active = False
        self._unlock: str | None = None

    def acquire(self) -> bool:
        if not self.requested or self.active:
            return self.active
        acquire = shutil.which("termux-wake-lock")
        unlock = shutil.which("termux-wake-unlock")
        if not acquire or not unlock:
            return False
        try:
            subprocess.run([acquire], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=4, check=True)
        except (OSError, subprocess.SubprocessError):
            return False
        self._unlock = unlock
        self.active = True
        return True

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        unlock = self._unlock
        self._unlock = None
        if not unlock:
            return
        try:
            subprocess.run([unlock], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=4, check=True)
        except (OSError, subprocess.SubprocessError):
            # A failed release cannot undo a completed/cancelled export.
            pass
