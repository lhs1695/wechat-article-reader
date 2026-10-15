"""Bounded process-local locks for publishing files to the same export path."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from threading import RLock

_PATH_LOCKS = tuple(RLock() for _ in range(64))


def export_path_lock(path: Path) -> RLock:
    normalized = str(path.resolve()).casefold().encode("utf-8")
    index = int.from_bytes(sha256(normalized).digest()[:2], "big") % len(_PATH_LOCKS)
    return _PATH_LOCKS[index]
