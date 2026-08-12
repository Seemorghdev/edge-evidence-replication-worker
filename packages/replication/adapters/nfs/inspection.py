"""Read-only/non-creating target inspection used by reconcile and verify."""

from __future__ import annotations

import stat
from pathlib import Path

from .filesystem import _target_error, fsync_directory, hash_regular, validate_root
from .model import ReplicaObject, ReplicationError, canonical_file_uri, owned_partial_name


def _existing_final(target_root: Path, obj: ReplicaObject) -> Path | None:
    root = validate_root(target_root, target=True)
    relative = canonical_file_uri(obj.relative_uri)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _target_error(exc) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ReplicationError("destination_path_unsafe", code=2)
    return root.joinpath(*relative.parts)


def cleanup_owned_partial_existing(target_root: Path, obj: ReplicaObject) -> bool:
    final = _existing_final(target_root, obj)
    if final is None:
        return False
    partial = final.parent / owned_partial_name(obj)
    try:
        st = partial.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _target_error(exc) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ReplicationError("partial_cleanup_failed", code=5)
    try:
        partial.unlink()
        fsync_directory(final.parent)
    except OSError as exc:
        raise ReplicationError("partial_cleanup_failed", code=5) from exc
    return True


def reconcile_destination_state(target_root: Path, obj: ReplicaObject) -> str:
    final = _existing_final(target_root, obj)
    if final is None:
        return "absent"
    try:
        st = final.lstat()
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise _target_error(exc) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ReplicationError("destination_path_unsafe", code=2)
    size, digest = hash_regular(
        final,
        target=True,
        missing="destination_missing",
        unsafe="destination_path_unsafe",
        corrupt="destination_digest_mismatch",
    )
    if size == obj.expected_byte_size and digest == obj.expected_sha256:
        return "exact"
    raise ReplicationError("destination_collision", code=4)


def verify_existing_replica(target_root: Path, obj: ReplicaObject) -> None:
    final = _existing_final(target_root, obj)
    if final is None:
        raise ReplicationError("destination_missing", code=6)
    try:
        st = final.lstat()
    except FileNotFoundError as exc:
        raise ReplicationError("destination_missing", code=6) from exc
    except OSError as exc:
        raise _target_error(exc) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ReplicationError("destination_path_unsafe", code=2)
    size, digest = hash_regular(
        final,
        target=True,
        missing="destination_missing",
        unsafe="destination_path_unsafe",
        corrupt="destination_digest_mismatch",
    )
    if size != obj.expected_byte_size:
        raise ReplicationError("destination_size_mismatch", code=6)
    if digest != obj.expected_sha256:
        raise ReplicationError("destination_digest_mismatch", code=6)
