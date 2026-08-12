"""GCS target identity and object-name mapping for SPEC-023."""

from __future__ import annotations

import hashlib
import json

from packages.replication.contracts.model import (
    ReplicaObject,
    ReplicationError,
    canonical_file_uri,
    validate_target_id,
)

ADAPTER_KIND = "gcs"
TARGET_MARKER_NAME = ".edge-evidence-replica-target.v1.json"


def normalize_prefix(prefix: str) -> str:
    if not isinstance(prefix, str):
        raise ReplicationError("target_config_invalid", code=2)
    if prefix == "":
        return ""
    if prefix.startswith("/") or prefix.endswith("/") or "\\" in prefix:
        raise ReplicationError("target_config_invalid", code=2)
    parts = prefix.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReplicationError("target_config_invalid", code=2)
    return prefix


def canonical_marker_bytes(target_id: str) -> bytes:
    validate_target_id(target_id)
    return (
        json.dumps(
            {"schema": "replica-target.v1", "target_id": target_id},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")


def marker_sha256(target_id: str) -> str:
    return hashlib.sha256(canonical_marker_bytes(target_id)).hexdigest()


def marker_name(prefix: str) -> str:
    prefix = normalize_prefix(prefix)
    return TARGET_MARKER_NAME if not prefix else f"{prefix}/{TARGET_MARKER_NAME}"


def object_name(prefix: str, obj: ReplicaObject) -> str:
    prefix = normalize_prefix(prefix)
    relative = canonical_file_uri(obj.relative_uri).as_posix()
    return relative if not prefix else f"{prefix}/{relative}"
