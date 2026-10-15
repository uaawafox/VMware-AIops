"""Filesystem permission helpers for owner-only state files.

State written under ``~/.vmware-aiops/`` (TTL store, plans, image registry)
can carry VM names, operation plans, and infrastructure topology. Keep the
directory owner-only (0700) and the files owner-read/write (0600). All helpers
are best-effort: a failed chmod must never break the operation it guards.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def secure_mkdir(directory: Path) -> None:
    """Create ``directory`` (and parents) as 0700, enforcing mode past umask."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass


def secure_chmod_file(path: Path) -> None:
    """Restrict ``path`` to 0600 if it exists. Best-effort."""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except OSError:
        pass


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` in one step, created owner-only (0600).

    MCP calls run side by side in worker threads (HLD §16 D-13(2)), and the
    daemon is another process: a reader that opened the file mid-write saw
    truncated JSON, which the TTL loader reads as "no entries" — and the next
    save then wrote every other entry away. ``os.replace`` is atomic on one
    filesystem, so a reader sees the old file or the new one.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
