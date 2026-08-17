"""Unit proofs for the SPEC-023 semantic target boundary."""

from __future__ import annotations

from pathlib import Path

from packages.replication.adapters.nfs.target import NfsTarget
from packages.replication.contracts.target import BoundReplicationTarget, ReplicationTarget
from packages.replication.model import ReplicaObject


def _obj() -> ReplicaObject:
    return ReplicaObject(
        target_id="backup",
        object_kind="artifact_content",
        relative_uri="file:sha256/aa/object.bin",
        expected_byte_size=3,
        expected_sha256="0" * 64,
    )


def test_nfs_target_satisfies_neutral_factory_contract() -> None:
    target = NfsTarget(Path("/replica"))
    assert isinstance(target, ReplicationTarget)
    assert target.adapter_kind == "mounted_nfs_v4"


def test_bound_nfs_target_forwards_semantics_without_exposing_runtime_identity(monkeypatch) -> None:
    from packages.replication.adapters.nfs import target as module

    calls: list[str] = []
    monkeypatch.setattr(
        module.filesystem,
        "validate_target_identity",
        lambda *args, **kwargs: (11, 22),
    )
    monkeypatch.setattr(
        module.inspection,
        "cleanup_owned_partial_existing",
        lambda *args, **kwargs: calls.append("cleanup") or True,
    )
    monkeypatch.setattr(
        module.inspection,
        "reconcile_destination_state",
        lambda *args, **kwargs: calls.append("inspect") or "exact",
    )
    monkeypatch.setattr(
        module.filesystem,
        "publish_object",
        lambda *args, **kwargs: calls.append("publish") or "adopted",
    )
    monkeypatch.setattr(
        module.inspection,
        "verify_existing_replica",
        lambda *args, **kwargs: calls.append("verify"),
    )

    bound = NfsTarget(Path("/replica")).bind("backup", "1" * 64)
    assert isinstance(bound, BoundReplicationTarget)
    assert bound.cleanup_transient(_obj()) is True
    assert bound.inspect(_obj()) == "exact"
    assert bound.publish_immutable(Path("/spool"), _obj()) == "adopted"
    bound.verify(_obj())
    assert calls == ["cleanup", "inspect", "publish", "verify"]
