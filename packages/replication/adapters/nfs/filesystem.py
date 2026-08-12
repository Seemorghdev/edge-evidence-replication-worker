"""Mounted-filesystem safety and publication primitives for SPEC-012."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path, PurePosixPath

from .model import (
    TARGET_MARKER_NAME,
    TARGET_MARKER_PARTIAL,
    ReplicaObject,
    ReplicationError,
    canonical_file_uri,
    canonical_marker_bytes,
    owned_partial_name,
)

_CHUNK = 1024 * 1024


def _target_error(exc: OSError) -> ReplicationError:
    if exc.errno == errno.ENOSPC:
        return ReplicationError("target_full", code=5)
    if exc.errno in {errno.EROFS, errno.EACCES}:
        return ReplicationError("target_read_only", code=5)
    if exc.errno in {
        errno.ENOENT,
        errno.EIO,
        getattr(errno, "ESTALE", -1),
        getattr(errno, "ENOTCONN", -1),
        getattr(errno, "ETIMEDOUT", -1),
        getattr(errno, "EHOSTDOWN", -1),
    }:
        return ReplicationError("target_unavailable", code=5)
    return ReplicationError("target_io_error", code=5)


def _unescape_mount_field(value: str) -> str:
    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def mounted_fstype(root: Path) -> str | None:
    """Return the longest-prefix Linux mount type without exposing its source."""

    try:
        resolved = str(Path(root).resolve(strict=True))
        lines = Path("/proc/self/mountinfo").read_text(
            encoding="utf-8",
            errors="strict",
        ).splitlines()
    except (OSError, UnicodeError):
        return None

    best: tuple[int, str] | None = None
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if len(fields) <= separator + 1 or len(fields) < 5:
            continue
        mountpoint = _unescape_mount_field(fields[4])
        if resolved == mountpoint or resolved.startswith(mountpoint.rstrip("/") + "/"):
            candidate = (len(mountpoint), fields[separator + 1])
            if best is None or candidate[0] > best[0]:
                best = candidate
    return None if best is None else best[1]


def require_nfs_v4(root: Path, *, runtime: bool) -> None:
    if mounted_fstype(root) != "nfs4":
        raise ReplicationError(
            "target_identity_mismatch" if runtime else "target_adapter_mismatch",
            code=4 if runtime else 2,
        )


def validate_root(root: Path, *, target: bool) -> Path:
    root = Path(root)
    try:
        st = root.lstat()
    except OSError as exc:
        if target:
            raise _target_error(exc) from exc
        raise ReplicationError("source_path_unsafe", code=2) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ReplicationError(
            "destination_path_unsafe" if target else "source_path_unsafe",
            code=2,
        )
    return root


def root_identity(root: Path) -> tuple[int, int]:
    root = validate_root(root, target=True)
    try:
        st = os.stat(root, follow_symlinks=False)
    except OSError as exc:
        raise _target_error(exc) from exc
    return int(st.st_dev), int(st.st_ino)


def _open_regular(path: Path, *, target: bool, missing: str, unsafe: str) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        if target and missing == "target_unavailable":
            raise _target_error(exc) from exc
        raise ReplicationError(missing, code=6) from exc
    except OSError as exc:
        if target:
            raise _target_error(exc) from exc
        if exc.errno == errno.ELOOP:
            raise ReplicationError(unsafe, code=2) from exc
        raise ReplicationError(missing, code=6) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReplicationError(unsafe, code=2)
    except Exception:
        os.close(fd)
        raise
    return fd


def hash_regular(
    path: Path,
    *,
    target: bool,
    missing: str,
    unsafe: str,
    corrupt: str,
) -> tuple[int, str]:
    fd = _open_regular(
        path,
        target=target,
        missing=missing,
        unsafe=unsafe,
    )
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
        if target:
            raise _target_error(exc) from exc
        raise ReplicationError(corrupt, code=6) from exc
    finally:
        os.close(fd)
    return total, digest.hexdigest()


def _existing_parent_walk(
    root: Path,
    relative: PurePosixPath,
    *,
    target: bool,
) -> Path:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            st = current.lstat()
        except OSError as exc:
            if target:
                raise _target_error(exc) from exc
            raise ReplicationError("source_path_unsafe", code=2) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ReplicationError(
                "destination_path_unsafe" if target else "source_path_unsafe",
                code=2,
            )
    return root.joinpath(*relative.parts)


def source_path(spool_root: Path, uri: str) -> Path:
    root = validate_root(spool_root, target=False)
    relative = canonical_file_uri(uri)
    path = _existing_parent_walk(root, relative, target=False)
    fd = _open_regular(
        path,
        target=False,
        missing="local_source_missing",
        unsafe="source_path_unsafe",
    )
    os.close(fd)
    return path


def local_size_and_hash(spool_root: Path, uri: str) -> tuple[int, str]:
    path = source_path(spool_root, uri)
    return hash_regular(
        path,
        target=False,
        missing="local_source_missing",
        unsafe="source_path_unsafe",
        corrupt="local_source_corrupt",
    )


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise _target_error(exc) from exc


def ensure_destination_parent(target_root: Path, uri: str) -> Path:
    root = validate_root(target_root, target=True)
    relative = canonical_file_uri(uri)
    current = root
    for part in relative.parts[:-1]:
        candidate = current / part
        try:
            st = candidate.lstat()
        except FileNotFoundError:
            try:
                os.mkdir(candidate)
            except FileExistsError:
                pass
            except OSError as exc:
                raise _target_error(exc) from exc
            try:
                st = candidate.lstat()
            except OSError as exc:
                raise _target_error(exc) from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise ReplicationError("destination_path_unsafe", code=2)
            fsync_directory(current)
        except OSError as exc:
            raise _target_error(exc) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ReplicationError("destination_path_unsafe", code=2)
        current = candidate
    return root.joinpath(*relative.parts)


def _read_exact_file(path: Path, *, target: bool, missing: str, unsafe: str) -> bytes:
    fd = _open_regular(path, target=target, missing=missing, unsafe=unsafe)
    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as exc:
        if target:
            raise _target_error(exc) from exc
        raise ReplicationError("local_source_corrupt", code=6) from exc
    finally:
        os.close(fd)
    return b"".join(chunks)


def marker_path(target_root: Path) -> Path:
    return Path(target_root) / TARGET_MARKER_NAME


def marker_exists(target_root: Path) -> bool:
    root = validate_root(target_root, target=True)
    path = marker_path(root)
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _target_error(exc) from exc
    return True


def initialize_marker(target_root: Path, target_id: str) -> str:
    root = validate_root(target_root, target=True)
    require_nfs_v4(root, runtime=False)
    canonical = canonical_marker_bytes(target_id)
    digest = hashlib.sha256(canonical).hexdigest()
    final = root / TARGET_MARKER_NAME
    partial = root / TARGET_MARKER_PARTIAL

    if marker_exists(root):
        existing = _read_exact_file(
            final,
            target=True,
            missing="target_identity_mismatch",
            unsafe="target_identity_mismatch",
        )
        if existing != canonical:
            raise ReplicationError("target_identity_mismatch", code=4)
        return digest

    try:
        st = partial.lstat()
    except FileNotFoundError:
        st = None
    except OSError as exc:
        raise _target_error(exc) from exc
    if st is not None:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ReplicationError("target_identity_mismatch", code=4)
        try:
            partial.unlink()
            fsync_directory(root)
        except OSError as exc:
            raise _target_error(exc) from exc

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(partial, flags, 0o600)
        try:
            offset = 0
            while offset < len(canonical):
                offset += os.write(fd, canonical[offset:])
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(partial, final, follow_symlinks=False)
        except FileExistsError:
            pass
        except OSError as exc:
            unsupported = {
                errno.EXDEV,
                errno.ENOTSUP,
                errno.EPERM,
                getattr(errno, "EOPNOTSUPP", -1),
            }
            if exc.errno in unsupported:
                raise ReplicationError(
                    "exclusive_publication_unsupported",
                    code=5,
                ) from exc
            raise _target_error(exc) from exc
        partial.unlink(missing_ok=True)
        fsync_directory(root)
    except ReplicationError:
        raise
    except OSError as exc:
        raise _target_error(exc) from exc

    existing = _read_exact_file(
        final,
        target=True,
        missing="target_identity_mismatch",
        unsafe="target_identity_mismatch",
    )
    if existing != canonical:
        raise ReplicationError("target_identity_mismatch", code=4)
    return digest


def validate_target_identity(
    target_root: Path,
    target_id: str,
    marker_sha256: str,
    *,
    expected_runtime_identity: tuple[int, int] | None = None,
) -> tuple[int, int]:
    root = validate_root(target_root, target=True)
    require_nfs_v4(root, runtime=True)
    runtime = root_identity(root)
    if expected_runtime_identity is not None and runtime != expected_runtime_identity:
        raise ReplicationError("target_identity_mismatch", code=4)
    canonical = canonical_marker_bytes(target_id)
    marker = root / TARGET_MARKER_NAME
    try:
        data = _read_exact_file(
            marker,
            target=True,
            missing="target_identity_mismatch",
            unsafe="target_identity_mismatch",
        )
    except ReplicationError as exc:
        if exc.finding in {
            "target_unavailable",
            "target_read_only",
            "target_io_error",
        }:
            if expected_runtime_identity is not None:
                now = root_identity(root)
                if now != expected_runtime_identity:
                    raise ReplicationError("target_identity_mismatch", code=4) from exc
            raise
        raise ReplicationError("target_identity_mismatch", code=4) from exc
    if data != canonical or hashlib.sha256(data).hexdigest() != marker_sha256:
        raise ReplicationError("target_identity_mismatch", code=4)
    return runtime


def final_state(target_root: Path, obj: ReplicaObject) -> str:
    final = ensure_destination_parent(target_root, obj.relative_uri)
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


def verify_replica_object(target_root: Path, obj: ReplicaObject) -> None:
    root = validate_root(target_root, target=True)
    relative = canonical_file_uri(obj.relative_uri)
    try:
        final = _existing_parent_walk(root, relative, target=True)
    except ReplicationError as exc:
        if exc.finding == "target_unavailable":
            raise
        raise ReplicationError("destination_path_unsafe", code=2) from exc
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


def cleanup_owned_partial(target_root: Path, obj: ReplicaObject) -> bool:
    final = ensure_destination_parent(target_root, obj.relative_uri)
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


def publish_object(
    spool_root: Path,
    target_root: Path,
    obj: ReplicaObject,
    *,
    target_id: str,
    marker_sha256: str,
    runtime_identity: tuple[int, int],
) -> str:
    validate_target_identity(
        target_root,
        target_id,
        marker_sha256,
        expected_runtime_identity=runtime_identity,
    )
    source = source_path(spool_root, obj.relative_uri)
    final = ensure_destination_parent(target_root, obj.relative_uri)

    if final_state(target_root, obj) == "exact":
        validate_target_identity(
            target_root,
            target_id,
            marker_sha256,
            expected_runtime_identity=runtime_identity,
        )
        return "adopted"

    partial = final.parent / owned_partial_name(obj)
    try:
        partial.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise _target_error(exc) from exc
    else:
        raise ReplicationError("partial_cleanup_failed", code=5)

    read_flags = os.O_RDONLY
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        read_flags |= os.O_NOFOLLOW
        write_flags |= os.O_NOFOLLOW

    digest = hashlib.sha256()
    total = 0
    try:
        source_fd = os.open(source, read_flags)
        try:
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise ReplicationError("source_path_unsafe", code=2)
            partial_fd = os.open(partial, write_flags, 0o600)
            try:
                while True:
                    chunk = os.read(source_fd, _CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    total += len(chunk)
                    offset = 0
                    while offset < len(chunk):
                        offset += os.write(partial_fd, chunk[offset:])
                os.fsync(partial_fd)
            finally:
                os.close(partial_fd)
        finally:
            os.close(source_fd)
    except ReplicationError:
        raise
    except OSError as exc:
        raise _target_error(exc) from exc

    if total != obj.expected_byte_size or digest.hexdigest() != obj.expected_sha256:
        try:
            partial.unlink()
            fsync_directory(final.parent)
        except OSError:
            pass
        raise ReplicationError("local_source_corrupt", code=6)

    validate_target_identity(
        target_root,
        target_id,
        marker_sha256,
        expected_runtime_identity=runtime_identity,
    )

    try:
        os.link(partial, final, follow_symlinks=False)
    except FileExistsError:
        try:
            partial.unlink()
            fsync_directory(final.parent)
        except OSError as exc:
            raise ReplicationError("partial_cleanup_failed", code=5) from exc
        if final_state(target_root, obj) == "exact":
            validate_target_identity(
                target_root,
                target_id,
                marker_sha256,
                expected_runtime_identity=runtime_identity,
            )
            return "adopted"
        raise ReplicationError("destination_collision", code=4)
    except OSError as exc:
        unsupported = {
            errno.EXDEV,
            errno.ENOTSUP,
            errno.EPERM,
            getattr(errno, "EOPNOTSUPP", -1),
        }
        if exc.errno in unsupported:
            raise ReplicationError(
                "exclusive_publication_unsupported",
                code=5,
            ) from exc
        raise _target_error(exc) from exc

    try:
        partial.unlink()
        fsync_directory(final.parent)
    except OSError as exc:
        raise _target_error(exc) from exc

    verify_replica_object(target_root, obj)
    validate_target_identity(
        target_root,
        target_id,
        marker_sha256,
        expected_runtime_identity=runtime_identity,
    )
    return "published"
