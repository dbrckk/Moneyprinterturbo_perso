"""Publish a video and its editable sidecars as a rollback-safe file set.

Individual replacements are atomic within the output filesystem. A group of
separate files cannot be crash-atomic without changing the public output layout;
this protects existing files from ordinary copy and rename failures instead.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from collections.abc import Iterable, Mapping


def publish_bundle(artifacts: Mapping[Path, Path], *,
                   remove: Iterable[Path] = ()) -> None:
    """Stage and verify every file before replacing any existing output.

    \`artifacts\` maps published destination paths to completed working files.
    Stale artifacts (old QA/storyboard or removed ASS captions) can be removed
    in the same guarded operation. On an ordinary I/O error all former outputs
    are restored and newly created outputs are removed.
    """
    if not artifacts:
        raise ValueError("Nothing to publish")
    items = [(Path(dst), Path(src)) for dst, src in artifacts.items()]
    destinations = [dst for dst, _ in items]
    obsolete = [Path(dst) for dst in remove]
    all_paths = destinations + obsolete
    if len(set(all_paths)) != len(all_paths):
        raise ValueError("Output and obsolete files must be unique")
    directory = destinations[0].parent.resolve()
    if any(dst.parent.resolve() != directory for dst in all_paths):
        raise ValueError("All output files must have the same destination directory")
    if any(not source.is_file() or source.stat().st_size == 0 for _, source in items):
        raise ValueError("Missing or empty staged output file")
    if any(source.resolve() in {dst.resolve() for dst in all_paths}
           for _, source in items):
        raise ValueError("Staged sources must differ from published destinations")

    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory, prefix=".mpt-publish-") as scratch:
        root = Path(scratch)
        staged_dir = root / "ready"
        saved_dir = root / "previous"
        staged_dir.mkdir()
        saved_dir.mkdir()
        # Finish all writes before touching a single published destination.
        ready: dict[Path, Path] = {}
        for index, (dst, src) in enumerate(items):
            staged = staged_dir / str(index)
            shutil.copyfile(src, staged)
            ready[dst] = staged
        backups: dict[Path, Path] = {}
        for index, dst in enumerate(all_paths):
            if dst.exists():
                backup = saved_dir / str(index)
                shutil.copy2(dst, backup)
                backups[dst] = backup

        changed: list[Path] = []
        try:
            # Publish the MP4 last, after captions, credits and metadata, to
            # minimize the interval in which a new video has old sidecars.
            for dst, _ in items:
                if dst.suffix == ".mp4":
                    continue
                os.replace(ready[dst], dst)
                changed.append(dst)
            for dst in obsolete:
                if dst.exists():
                    dst.unlink()
                    changed.append(dst)
            for dst, _ in items:
                if dst.suffix == ".mp4":
                    os.replace(ready[dst], dst)
                    changed.append(dst)
        except OSError:
            for dst in reversed(changed):
                previous = backups.get(dst)
                if previous is not None:
                    os.replace(previous, dst)
                else:
                    dst.unlink(missing_ok=True)
            raise
