"""SPEC-023 migration v10: provider-neutral replication target authority."""

from __future__ import annotations

from .migrations import Migration


MIGRATION_V10 = Migration(
    version=10,
    name="provider_neutral_replication_targets",
    statements=(
        """
        CREATE TABLE replica_targets_v10 (
            target_id TEXT PRIMARY KEY
                CHECK (
                    length(target_id) BETWEEN 1 AND 64
                    AND substr(target_id, 1, 1) GLOB '[A-Za-z0-9]'
                    AND target_id NOT GLOB '*[^A-Za-z0-9._-]*'
                ),
            adapter_kind TEXT NOT NULL
                CHECK (
                    length(adapter_kind) BETWEEN 1 AND 64
                    AND substr(adapter_kind, 1, 1) GLOB '[a-z]'
                    AND adapter_kind NOT GLOB '*[^a-z0-9_]*'
                ),
            marker_sha256 TEXT NOT NULL
                CHECK (
                    length(marker_sha256) = 64
                    AND marker_sha256 NOT GLOB '*[^0-9a-f]*'
                ),
            created_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
        )
        """,
        """
        CREATE TABLE replica_objects_v10 (
            target_id TEXT NOT NULL
                REFERENCES replica_targets_v10(target_id),
            object_kind TEXT NOT NULL
                CHECK (object_kind IN (
                    'artifact_content',
                    'artifact_manifest',
                    'occurrence_assertion',
                    'recording_original'
                )),
            relative_uri TEXT NOT NULL
                CHECK (
                    length(relative_uri) >= 6
                    AND substr(relative_uri, 1, 5) = 'file:'
                    AND substr(relative_uri, 6, 1) <> '/'
                    AND relative_uri NOT LIKE '%//%'
                    AND relative_uri NOT LIKE 'file:../%'
                    AND relative_uri NOT LIKE 'file:./%'
                    AND relative_uri NOT LIKE '%/../%'
                    AND relative_uri NOT LIKE '%/./%'
                    AND relative_uri NOT LIKE '%/..'
                    AND relative_uri NOT LIKE '%/.'
                ),
            expected_byte_size INTEGER NOT NULL
                CHECK (expected_byte_size >= 0),
            expected_sha256 TEXT NOT NULL
                CHECK (
                    length(expected_sha256) = 64
                    AND expected_sha256 NOT GLOB '*[^0-9a-f]*'
                ),
            state TEXT NOT NULL
                CHECK (state IN ('PENDING', 'VERIFIED')),
            attempt_count INTEGER NOT NULL DEFAULT 0
                CHECK (attempt_count >= 0),
            last_error TEXT
                CHECK (last_error IS NULL OR length(last_error) <= 96),
            created_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            verified_at TEXT,
            PRIMARY KEY (
                target_id,
                object_kind,
                relative_uri,
                expected_byte_size,
                expected_sha256
            ),
            UNIQUE (target_id, object_kind, relative_uri),
            CHECK (
                (state = 'PENDING' AND verified_at IS NULL)
                OR
                (state = 'VERIFIED'
                    AND verified_at IS NOT NULL
                    AND last_error IS NULL)
            )
        )
        """,
        """
        INSERT INTO replica_targets_v10
            (target_id, adapter_kind, marker_sha256, created_at)
        SELECT target_id, adapter_kind, marker_sha256, created_at
        FROM replica_targets
        """,
        """
        INSERT INTO replica_objects_v10
            (target_id, object_kind, relative_uri, expected_byte_size,
             expected_sha256, state, attempt_count, last_error, created_at,
             verified_at)
        SELECT target_id, object_kind, relative_uri, expected_byte_size,
               expected_sha256, state, attempt_count, last_error, created_at,
               verified_at
        FROM replica_objects
        """,
        "DROP TABLE replica_objects",
        "DROP TABLE replica_targets",
        "ALTER TABLE replica_targets_v10 RENAME TO replica_targets",
        "ALTER TABLE replica_objects_v10 RENAME TO replica_objects",
        """
        CREATE INDEX idx_replica_objects_pending
        ON replica_objects(target_id, state, object_kind, relative_uri)
        """,
        """
        CREATE TRIGGER replica_targets_no_update
        BEFORE UPDATE ON replica_targets
        BEGIN
            SELECT RAISE(ABORT, 'replica_targets rows are immutable');
        END
        """,
        """
        CREATE TRIGGER replica_targets_no_delete
        BEFORE DELETE ON replica_targets
        BEGIN
            SELECT RAISE(ABORT, 'replica_targets rows cannot be deleted');
        END
        """,
        """
        CREATE TRIGGER replica_objects_no_delete
        BEFORE DELETE ON replica_objects
        BEGIN
            SELECT RAISE(ABORT, 'replica_objects rows cannot be deleted');
        END
        """,
        """
        CREATE TRIGGER replica_objects_guard_update
        BEFORE UPDATE ON replica_objects
        BEGIN
            SELECT CASE
                WHEN OLD.state = 'VERIFIED' THEN
                    RAISE(ABORT, 'VERIFIED replica_objects are immutable')
                WHEN NEW.target_id <> OLD.target_id
                    OR NEW.object_kind <> OLD.object_kind
                    OR NEW.relative_uri <> OLD.relative_uri
                    OR NEW.expected_byte_size <> OLD.expected_byte_size
                    OR NEW.expected_sha256 <> OLD.expected_sha256
                    OR NEW.created_at <> OLD.created_at THEN
                    RAISE(
                        ABORT,
                        'replica object identity and expected bytes are immutable'
                    )
                WHEN NEW.state NOT IN ('PENDING', 'VERIFIED') THEN
                    RAISE(ABORT, 'invalid replica state')
                WHEN NEW.attempt_count < OLD.attempt_count THEN
                    RAISE(ABORT, 'replica attempt_count cannot decrease')
            END;
        END
        """,
        "PRAGMA foreign_key_check",
    ),
)
