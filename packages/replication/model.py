"""Compatibility imports for replication identity helpers.

Neutral identities live in ``contracts.model``. Mounted-NFS marker/transient names
remain available here only for the existing SPEC-012 import surface during the
SPEC-023 migration.
"""

from .contracts.model import (
    OBJECT_KINDS,
    ReplicaObject,
    ReplicationError,
    canonical_file_uri,
    validate_sha256,
    validate_target_id,
)
from .adapters.nfs.model import (
    ADAPTER_KIND,
    TARGET_MARKER_NAME,
    TARGET_MARKER_PARTIAL,
    canonical_marker_bytes,
    marker_sha256,
    owned_partial_name,
)

__all__ = [
    "ADAPTER_KIND",
    "OBJECT_KINDS",
    "ReplicaObject",
    "ReplicationError",
    "TARGET_MARKER_NAME",
    "TARGET_MARKER_PARTIAL",
    "canonical_file_uri",
    "canonical_marker_bytes",
    "marker_sha256",
    "owned_partial_name",
    "validate_sha256",
    "validate_target_id",
]
