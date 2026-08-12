"""Provider facade for the proven mounted-NFSv4 replication target."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packages.replication.contracts.model import ReplicaObject
from packages.replication.contracts.target import DestinationState, PublishResult

from . import filesystem, inspection
from .model import ADAPTER_KIND


@dataclass(frozen=True)
class _BoundNfsTarget:
    root: Path
    target_id: str
    marker_sha256: str
    runtime_identity: tuple[int, int]
    adapter_kind: str = ADAPTER_KIND

    def validate_identity(self) -> None:
        filesystem.validate_target_identity(
            self.root,
            self.target_id,
            self.marker_sha256,
            expected_runtime_identity=self.runtime_identity,
        )

    def cleanup_transient(self, obj: ReplicaObject) -> bool:
        return inspection.cleanup_owned_partial_existing(self.root, obj)

    def inspect(self, obj: ReplicaObject) -> DestinationState:
        return inspection.reconcile_destination_state(self.root, obj)  # type: ignore[return-value]

    def publish_immutable(self, spool_root: Path, obj: ReplicaObject) -> PublishResult:
        return filesystem.publish_object(
            spool_root,
            self.root,
            obj,
            target_id=self.target_id,
            marker_sha256=self.marker_sha256,
            runtime_identity=self.runtime_identity,
        )  # type: ignore[return-value]

    def verify(self, obj: ReplicaObject) -> None:
        inspection.verify_existing_replica(self.root, obj)


@dataclass(frozen=True)
class NfsTarget:
    """Construct/bind the existing mounted-NFSv4 target behind neutral semantics."""

    root: Path
    adapter_kind: str = ADAPTER_KIND

    def initialize(self, target_id: str) -> str:
        return filesystem.initialize_marker(self.root, target_id)

    def bind(self, target_id: str, marker_sha256: str) -> _BoundNfsTarget:
        runtime_identity = filesystem.validate_target_identity(
            self.root,
            target_id,
            marker_sha256,
        )
        return _BoundNfsTarget(
            root=self.root,
            target_id=target_id,
            marker_sha256=marker_sha256,
            runtime_identity=runtime_identity,
        )
