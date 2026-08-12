"""Provider-neutral replication identities and stable failure classes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

OBJECT_KINDS = frozenset(
    {
        "artifact_content",
        "artifact_manifest",
        "occurrence_assertion",
        "recording_original",
    }
)

_TARGET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ReplicationError(RuntimeError):
    """Stable, path-neutral replication failure."""

    def __init__(self, finding: str, *, code: int) -> None:
        super().__init__(finding)
        self.finding = finding
        self.code = code


@dataclass(frozen=True, order=True)
class ReplicaObject:
    target_id: str
    object_kind: str
    relative_uri: str
    expected_byte_size: int
    expected_sha256: str

    def __post_init__(self) -> None:
        validate_target_id(self.target_id)
        if self.object_kind not in OBJECT_KINDS:
            raise ReplicationError("replica_object_kind_invalid", code=2)
        canonical_file_uri(self.relative_uri)
        if type(self.expected_byte_size) is not int or self.expected_byte_size < 0:
            raise ReplicationError("replica_expected_size_invalid", code=2)
        validate_sha256(self.expected_sha256)


def validate_target_id(target_id: str) -> str:
    if not isinstance(target_id, str) or _TARGET_ID_RE.fullmatch(target_id) is None:
        raise ReplicationError("target_id_invalid", code=2)
    return target_id


def validate_sha256(value: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ReplicationError("sha256_invalid", code=2)
    return value


def canonical_file_uri(uri: str) -> PurePosixPath:
    if not isinstance(uri, str) or not uri.startswith("file:"):
        raise ReplicationError("source_path_unsafe", code=2)
    raw = uri[5:]
    if not raw or raw.startswith("/") or "\\" in raw:
        raise ReplicationError("source_path_unsafe", code=2)
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReplicationError("source_path_unsafe", code=2)
    path = PurePosixPath(*parts)
    if path.is_absolute() or path.as_posix() != raw:
        raise ReplicationError("source_path_unsafe", code=2)
    return path
