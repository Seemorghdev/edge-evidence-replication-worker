"""Mounted-NFSv4 target identity/transient naming for SPEC-023."""

from __future__ import annotations

import hashlib
import json

from packages.replication.contracts.model import (
    ReplicaObject,
    ReplicationError,
    canonical_file_uri,
    validate_target_id,
)

ADAPTER_KIND = "mounted_nfs_v4"
TARGET_MARKER_NAME = ".edge-evidence-replica-target.v1.json"
TARGET_MARKER_PARTIAL = ".edge-evidence-replica-target.v1.partial"


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


def owned_partial_name(obj: ReplicaObject) -> str:
    identity = "\x1f".join(
        (
            obj.target_id,
            obj.object_kind,
            obj.relative_uri,
            str(obj.expected_byte_size),
            obj.expected_sha256,
        )
    ).encode("utf-8")
    return (
        ".edge-evidence-replica-partial."
        + hashlib.sha256(identity).hexdigest()
        + ".tmp"
    )


__all__ = [
    "ADAPTER_KIND",
    "TARGET_MARKER_NAME",
    "TARGET_MARKER_PARTIAL",
    "ReplicaObject",
    "ReplicationError",
    "canonical_file_uri",
    "canonical_marker_bytes",
    "marker_sha256",
    "owned_partial_name",
]
