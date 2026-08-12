"""Provider-neutral local source inspection for replication core."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path

from packages.replication.contracts.model import ReplicationError, canonical_file_uri

_CHUNK = 1024 * 1024


def _validate_spool_root(root: Path) -> Path:
    root = Path(root)
    try:
        st = root.lstat()
    except OSError as exc:
        raise ReplicationError("source_path_unsafe", code=2) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ReplicationError("source_path_unsafe", code=2)
    return root


def _open_source(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ReplicationError("local_source_missing", code=6) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ReplicationError("source_path_unsafe", code=2) from exc
        raise ReplicationError("local_source_missing", code=6) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReplicationError("source_path_unsafe", code=2)
    except Exception:
        os.close(fd)
        raise
    return fd


def source_path(spool_root: Path, uri: str) -> Path:
    root = _validate_spool_root(spool_root)
    relative = canonical_file_uri(uri)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            st = current.lstat()
        except OSError as exc:
            raise ReplicationError("source_path_unsafe", code=2) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ReplicationError("source_path_unsafe", code=2)
    path = root.joinpath(*relative.parts)
    fd = _open_source(path)
    os.close(fd)
    return path


def local_size_and_hash(spool_root: Path, uri: str) -> tuple[int, str]:
    path = source_path(spool_root, uri)
    fd = _open_source(path)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    except OSError as exc:
        raise ReplicationError("local_source_corrupt", code=6) from exc
    finally:
        os.close(fd)
    return total, digest.hexdigest()
