"""Provider-neutral bounded replication convergence for SPEC-023."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packages.replication.contracts.model import ReplicationError, validate_target_id
from packages.replication.contracts.target import ReplicationTarget

from . import authority
from .source import local_size_and_hash


@dataclass(frozen=True)
class RunSummary:
    discovered: int = 0
    inserted: int = 0
    published: int = 0
    adopted: int = 0
    verified: int = 0
    partials_cleaned: int = 0

    def public(self) -> dict[str, int | str]:
        return {
            "status": "pass",
            "discovered": self.discovered,
            "inserted": self.inserted,
            "published": self.published,
            "adopted": self.adopted,
            "verified": self.verified,
            "partials_cleaned": self.partials_cleaned,
        }


def _load_target(connection, target_id: str, target: ReplicationTarget) -> str:
    authority.require_current(connection)
    row = authority.target_row(connection, target_id)
    if row is None:
        raise ReplicationError("target_identity_mismatch", code=4)
    adapter_kind, marker_sha256 = row
    if adapter_kind != target.adapter_kind:
        raise ReplicationError("target_identity_mismatch", code=4)
    return marker_sha256


def init_target(database: Path, target: ReplicationTarget, target_id: str) -> RunSummary:
    validate_target_id(target_id)
    connection = authority.connect(database)
    try:
        authority.require_current(connection)
        existing = authority.target_row(connection, target_id)
        if existing is not None:
            adapter_kind, marker_sha256 = existing
            if adapter_kind != target.adapter_kind:
                raise ReplicationError("target_identity_mismatch", code=4)
            target.bind(target_id, marker_sha256)
            return RunSummary()

        marker_sha256 = target.initialize(target_id)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = authority.target_row(connection, target_id)
            if row is None:
                authority.insert_target(
                    connection,
                    target_id,
                    target.adapter_kind,
                    marker_sha256,
                )
            elif row != (target.adapter_kind, marker_sha256):
                raise ReplicationError("target_identity_mismatch", code=4)
            connection.commit()
        except ReplicationError:
            connection.rollback()
            raise
        except Exception as exc:
            connection.rollback()
            raise ReplicationError("database_integrity_failure", code=7) from exc
        target.bind(target_id, marker_sha256)
        return RunSummary()
    finally:
        connection.close()


def reconcile(
    database: Path,
    spool_root: Path,
    target: ReplicationTarget,
    target_id: str,
) -> RunSummary:
    """Converge only already-known PENDING work through neutral target semantics."""

    del spool_root
    validate_target_id(target_id)
    connection = authority.connect(database)
    cleaned = 0
    adopted = 0
    try:
        marker_sha256 = _load_target(connection, target_id, target)
        bound = target.bind(target_id, marker_sha256)
        for obj in authority.pending_objects(connection, target_id):
            bound.validate_identity()
            if bound.cleanup_transient(obj):
                cleaned += 1
            state = bound.inspect(obj)
            if state == "exact":
                bound.validate_identity()
                authority.mark_verified(connection, obj)
                adopted += 1
        return RunSummary(adopted=adopted, verified=adopted, partials_cleaned=cleaned)
    finally:
        connection.close()


def run(
    database: Path,
    spool_root: Path,
    target: ReplicationTarget,
    target_id: str,
) -> RunSummary:
    validate_target_id(target_id)
    reconciled = reconcile(database, spool_root, target, target_id)
    connection = authority.connect(database)
    published = 0
    adopted = 0
    try:
        marker_sha256 = _load_target(connection, target_id, target)
        bound = target.bind(target_id, marker_sha256)
        objects = authority.discover(database, spool_root, target_id)
        inserted = authority.register_discovered(connection, target_id, objects)
        for obj in authority.pending_objects(connection, target_id):
            bound.validate_identity()
            authority.record_attempt(connection, obj)
            try:
                source_size, source_digest = local_size_and_hash(spool_root, obj.relative_uri)
                if source_size != obj.expected_byte_size or source_digest != obj.expected_sha256:
                    raise ReplicationError("local_source_corrupt", code=6)
                result = bound.publish_immutable(spool_root, obj)
            except ReplicationError as exc:
                authority.record_failure(connection, obj, exc.finding)
                raise
            authority.mark_verified(connection, obj)
            if result == "published":
                published += 1
            else:
                adopted += 1
        return RunSummary(
            discovered=len(objects),
            inserted=inserted,
            published=published,
            adopted=adopted + reconciled.adopted,
            verified=published + adopted + reconciled.verified,
            partials_cleaned=reconciled.partials_cleaned,
        )
    finally:
        connection.close()


def verify(database: Path, target: ReplicationTarget, target_id: str) -> RunSummary:
    validate_target_id(target_id)
    connection = authority.connect(database, readonly=True)
    try:
        marker_sha256 = _load_target(connection, target_id, target)
        bound = target.bind(target_id, marker_sha256)
        objects = authority.verified_objects(connection, target_id)
        for obj in objects:
            bound.validate_identity()
            bound.verify(obj)
        bound.validate_identity()
        return RunSummary(verified=len(objects))
    finally:
        connection.close()
