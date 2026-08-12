"""Semantic target interface owned by replication core, not by any provider."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from .model import ReplicaObject

DestinationState = Literal["absent", "exact"]
PublishResult = Literal["published", "adopted"]


@runtime_checkable
class BoundReplicationTarget(Protocol):
    """One target whose identity has been bound for a bounded worker invocation."""

    adapter_kind: str
    target_id: str
    marker_sha256: str

    def validate_identity(self) -> None:
        """Fail if the runtime target no longer matches the bound identity."""

    def cleanup_transient(self, obj: ReplicaObject) -> bool:
        """Remove only adapter-owned transient state for ``obj`` when present."""

    def inspect(self, obj: ReplicaObject) -> DestinationState:
        """Return absent/exact; conflicting destination bytes fail closed."""

    def publish_immutable(self, spool_root: Path, obj: ReplicaObject) -> PublishResult:
        """Create only if absent or adopt exact existing bytes; never overwrite."""

    def verify(self, obj: ReplicaObject) -> None:
        """Read back and verify expected object size/content identity."""


@runtime_checkable
class ReplicationTarget(Protocol):
    """Provider-specific factory implementing provider-neutral target semantics."""

    adapter_kind: str

    def initialize(self, target_id: str) -> str:
        """Initialize/validate target identity and return its canonical marker digest."""

    def bind(self, target_id: str, marker_sha256: str) -> BoundReplicationTarget:
        """Bind one runtime identity for a bounded convergence invocation."""
