"""Provider-neutral SQLite replication authority for SPEC-023."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from packages.database.migrations import MIGRATIONS
from packages.replication.contracts.model import (
    ReplicaObject,
    ReplicationError,
    canonical_file_uri,
)

from .source import local_size_and_hash

CURRENT_REPLICATION_MIGRATION = (10, "provider_neutral_replication_targets")


def connect(database: Path, *, readonly: bool = False) -> sqlite3.Connection:
    database = Path(database).expanduser().resolve()
    try:
        if readonly:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise ReplicationError("database_integrity_failure", code=7) from exc


def require_current(connection: sqlite3.Connection) -> None:
    """Require exact canonical migration history through replication v10."""

    canonical = tuple((migration.version, migration.name) for migration in MIGRATIONS)
    try:
        recorded = tuple(
            (int(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            )
        )
    except sqlite3.Error as exc:
        raise ReplicationError("migration_mismatch", code=7) from exc
    if recorded != canonical or CURRENT_REPLICATION_MIGRATION not in recorded:
        raise ReplicationError("migration_mismatch", code=7)
    if not recorded or recorded[-1] != CURRENT_REPLICATION_MIGRATION:
        raise ReplicationError("migration_mismatch", code=7)


def target_row(connection: sqlite3.Connection, target_id: str) -> tuple[str, str] | None:
    try:
        row = connection.execute(
            "SELECT adapter_kind, marker_sha256 FROM replica_targets WHERE target_id=?",
            (target_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ReplicationError("database_integrity_failure", code=7) from exc
    if row is None:
        return None
    return str(row[0]), str(row[1])


def insert_target(
    connection: sqlite3.Connection,
    target_id: str,
    adapter_kind: str,
    marker_sha256: str,
) -> None:
    require_current(connection)
    try:
        connection.execute(
            "INSERT INTO replica_targets(target_id, adapter_kind, marker_sha256) VALUES (?, ?, ?)",
            (target_id, adapter_kind, marker_sha256),
        )
    except sqlite3.Error as exc:
        raise ReplicationError("database_integrity_failure", code=7) from exc


def _manifest_object(
    *,
    target_id: str,
    object_kind: str,
    uri: str,
    expected_sha256: str,
    spool_root: Path,
) -> ReplicaObject:
    canonical_file_uri(uri)
    size, digest = local_size_and_hash(spool_root, uri)
    if digest != expected_sha256:
        raise ReplicationError("local_source_corrupt", code=6)
    return ReplicaObject(
        target_id=target_id,
        object_kind=object_kind,
        relative_uri=uri,
        expected_byte_size=size,
        expected_sha256=expected_sha256,
    )


def discover(database: Path, spool_root: Path, target_id: str) -> tuple[ReplicaObject, ...]:
    """Discover finalized immutable files from one coherent SQLite snapshot."""

    connection = connect(database, readonly=True)
    objects: list[ReplicaObject] = []
    try:
        require_current(connection)
        connection.execute("BEGIN")
        for row in connection.execute(
            "SELECT storage_uri, byte_size, digest_value, manifest_uri, manifest_sha256 FROM artifacts ORDER BY artifact_id"
        ):
            storage_uri, byte_size, digest_value, manifest_uri, manifest_sha256 = row
            objects.append(
                ReplicaObject(
                    target_id=target_id,
                    object_kind="artifact_content",
                    relative_uri=str(storage_uri),
                    expected_byte_size=int(byte_size),
                    expected_sha256=str(digest_value),
                )
            )
            objects.append(
                _manifest_object(
                    target_id=target_id,
                    object_kind="artifact_manifest",
                    uri=str(manifest_uri),
                    expected_sha256=str(manifest_sha256),
                    spool_root=spool_root,
                )
            )
        for manifest_uri, manifest_sha256 in connection.execute(
            "SELECT manifest_uri, manifest_sha256 FROM capture_occurrence_assertions ORDER BY occurrence_id"
        ):
            objects.append(
                _manifest_object(
                    target_id=target_id,
                    object_kind="occurrence_assertion",
                    uri=str(manifest_uri),
                    expected_sha256=str(manifest_sha256),
                    spool_root=spool_root,
                )
            )
        for storage_uri, byte_size, digest_value in connection.execute(
            "SELECT storage_uri, byte_size, digest_value FROM recording_imports WHERE state='COMPLETE' ORDER BY recording_id"
        ):
            objects.append(
                ReplicaObject(
                    target_id=target_id,
                    object_kind="recording_original",
                    relative_uri=str(storage_uri),
                    expected_byte_size=int(byte_size),
                    expected_sha256=str(digest_value),
                )
            )
        connection.rollback()
    except ReplicationError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.rollback()
        raise ReplicationError("database_integrity_failure", code=7) from exc
    finally:
        connection.close()
    return tuple(sorted(set(objects)))


def register_discovered(
    connection: sqlite3.Connection,
    target_id: str,
    objects: tuple[ReplicaObject, ...],
) -> int:
    require_current(connection)
    inserted = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        for obj in objects:
            row = connection.execute(
                "SELECT expected_byte_size, expected_sha256 FROM replica_objects WHERE target_id=? AND object_kind=? AND relative_uri=?",
                (target_id, obj.object_kind, obj.relative_uri),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO replica_objects(target_id, object_kind, relative_uri, expected_byte_size, expected_sha256, state) VALUES (?, ?, ?, ?, ?, 'PENDING')",
                    (
                        target_id,
                        obj.object_kind,
                        obj.relative_uri,
                        obj.expected_byte_size,
                        obj.expected_sha256,
                    ),
                )
                inserted += 1
            elif (int(row[0]), str(row[1])) != (
                obj.expected_byte_size,
                obj.expected_sha256,
            ):
                raise ReplicationError("replica_authority_conflict", code=7)
        connection.commit()
    except ReplicationError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise ReplicationError("database_integrity_failure", code=7) from exc
    return inserted


def _objects_for_state(
    connection: sqlite3.Connection,
    target_id: str,
    state: str,
) -> tuple[ReplicaObject, ...]:
    require_current(connection)
    try:
        rows = connection.execute(
            "SELECT object_kind, relative_uri, expected_byte_size, expected_sha256 FROM replica_objects WHERE target_id=? AND state=? ORDER BY object_kind, relative_uri",
            (target_id, state),
        ).fetchall()
    except sqlite3.Error as exc:
        raise ReplicationError("database_integrity_failure", code=7) from exc
    return tuple(
        ReplicaObject(
            target_id=target_id,
            object_kind=str(kind),
            relative_uri=str(uri),
            expected_byte_size=int(size),
            expected_sha256=str(digest),
        )
        for kind, uri, size, digest in rows
    )


def pending_objects(connection: sqlite3.Connection, target_id: str) -> tuple[ReplicaObject, ...]:
    return _objects_for_state(connection, target_id, "PENDING")


def verified_objects(connection: sqlite3.Connection, target_id: str) -> tuple[ReplicaObject, ...]:
    return _objects_for_state(connection, target_id, "VERIFIED")


def record_attempt(connection: sqlite3.Connection, obj: ReplicaObject) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        count = connection.execute(
            "UPDATE replica_objects SET attempt_count=attempt_count+1, last_error=NULL WHERE target_id=? AND object_kind=? AND relative_uri=? AND expected_byte_size=? AND expected_sha256=? AND state='PENDING'",
            (
                obj.target_id,
                obj.object_kind,
                obj.relative_uri,
                obj.expected_byte_size,
                obj.expected_sha256,
            ),
        ).rowcount
        if count != 1:
            raise ReplicationError("replica_authority_conflict", code=7)
        connection.commit()
    except ReplicationError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise ReplicationError("database_integrity_failure", code=7) from exc


def record_failure(connection: sqlite3.Connection, obj: ReplicaObject, finding: str) -> None:
    stable = str(finding)[:96]
    try:
        connection.execute("BEGIN IMMEDIATE")
        count = connection.execute(
            "UPDATE replica_objects SET last_error=? WHERE target_id=? AND object_kind=? AND relative_uri=? AND expected_byte_size=? AND expected_sha256=? AND state='PENDING'",
            (
                stable,
                obj.target_id,
                obj.object_kind,
                obj.relative_uri,
                obj.expected_byte_size,
                obj.expected_sha256,
            ),
        ).rowcount
        if count != 1:
            raise ReplicationError("replica_authority_conflict", code=7)
        connection.commit()
    except ReplicationError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise ReplicationError("database_integrity_failure", code=7) from exc


def mark_verified(connection: sqlite3.Connection, obj: ReplicaObject) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        count = connection.execute(
            "UPDATE replica_objects SET state='VERIFIED', last_error=NULL, verified_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE target_id=? AND object_kind=? AND relative_uri=? AND expected_byte_size=? AND expected_sha256=? AND state='PENDING'",
            (
                obj.target_id,
                obj.object_kind,
                obj.relative_uri,
                obj.expected_byte_size,
                obj.expected_sha256,
            ),
        ).rowcount
        if count != 1:
            raise ReplicationError("replica_authority_conflict", code=7)
        connection.commit()
    except ReplicationError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise ReplicationError("database_integrity_failure", code=7) from exc
