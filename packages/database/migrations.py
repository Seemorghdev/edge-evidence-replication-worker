"""Small, ordered, idempotent SQLite migration runner."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationResult:
    database: Path
    applied_versions: tuple[int, ...]
    current_version: int

    @property
    def applied_count(self) -> int:
        return len(self.applied_versions)


MIGRATIONS = (
    Migration(version=1, name="repository_foundation"),
    Migration(
        version=2,
        name="ledger_and_outbox",
        statements=(
            # ── artifacts: one row per immutable content-addressed artifact ──────
            """
            CREATE TABLE artifacts (
                artifact_id      TEXT PRIMARY KEY
                                 CHECK (artifact_id = 'sha256:' || digest_value),
                source_id        TEXT NOT NULL
                                 CHECK (length(source_id) BETWEEN 1 AND 128),
                artifact_kind    TEXT NOT NULL
                                 CHECK (artifact_kind IN ('raw_media', 'derived_media', 'metadata')),
                media_type       TEXT NOT NULL
                                 CHECK (length(media_type) BETWEEN 1 AND 255),
                byte_size        INTEGER NOT NULL CHECK (byte_size >= 0),
                digest_algorithm TEXT NOT NULL CHECK (digest_algorithm = 'sha256'),
                digest_value     TEXT NOT NULL UNIQUE
                                 CHECK (length(digest_value) = 64),
                storage_uri      TEXT NOT NULL UNIQUE CHECK (length(storage_uri) >= 1),
                manifest_uri     TEXT NOT NULL UNIQUE CHECK (length(manifest_uri) >= 1),
                manifest_sha256  TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
                finalized_at     TEXT NOT NULL,
                recorded_at      TEXT NOT NULL DEFAULT (
                                     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                 ),
                -- composite target for the occurrence (artifact_id, source_id) FK
                UNIQUE (artifact_id, source_id)
            )
            """,
            # ── capture_occurrences: one row per operational occurrence ─────────
            """
            CREATE TABLE capture_occurrences (
                occurrence_id     TEXT PRIMARY KEY,
                source_id         TEXT NOT NULL,
                session_id        TEXT NOT NULL,
                sequence_number   INTEGER NOT NULL CHECK (sequence_number > 0),
                capture_started_at TEXT NOT NULL,
                capture_ended_at   TEXT NOT NULL,
                artifact_id       TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'COMPLETE'
                                  CHECK (status = 'COMPLETE'),
                completed_at      TEXT NOT NULL DEFAULT (
                                      strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                  ),
                UNIQUE (source_id, session_id, sequence_number),
                FOREIGN KEY (artifact_id, source_id)
                    REFERENCES artifacts (artifact_id, source_id)
            )
            """,
            # ── outbox_events: one row per logical completion event ─────────────
            #    Pending means published_at IS NULL (no status column).
            """
            CREATE TABLE outbox_events (
                event_id       TEXT PRIMARY KEY,
                event_type     TEXT NOT NULL CHECK (event_type = 'SegmentFinalized'),
                aggregate_type TEXT NOT NULL CHECK (aggregate_type = 'capture_occurrence'),
                aggregate_id   TEXT NOT NULL
                               REFERENCES capture_occurrences (occurrence_id),
                payload_json   TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
                created_at     TEXT NOT NULL DEFAULT (
                                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                               ),
                published_at   TEXT,
                attempt_count  INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                last_error     TEXT,
                UNIQUE (event_type, aggregate_id)
            )
            """,
            "CREATE INDEX idx_occurrence_artifact ON capture_occurrences(artifact_id)",
        ),
    ),
    Migration(
        version=3,
        name="crash_reconciliation",
        statements=(
            # ── expected_occurrences: durable PREPARED intent + terminal state ──
            #    A SEPARATE table; capture_occurrences stays COMPLETE-only.
            """
            CREATE TABLE expected_occurrences (
                occurrence_id       TEXT PRIMARY KEY,
                source_id           TEXT NOT NULL,
                session_id          TEXT NOT NULL,
                sequence_number     INTEGER NOT NULL CHECK (sequence_number > 0),
                expected_started_at TEXT NOT NULL,
                expected_ended_at   TEXT NOT NULL,
                state               TEXT NOT NULL
                                    CHECK (state IN ('PREPARED', 'COMPLETE', 'GAP')),
                artifact_id         TEXT,
                gap_reason          TEXT
                                    CHECK (gap_reason IS NULL OR gap_reason IN
                                        ('source_unavailable', 'process_interruption',
                                         'storage_pressure', 'unknown')),
                gap_detail          TEXT
                                    CHECK (gap_detail IS NULL OR length(gap_detail) <= 500),
                created_at          TEXT NOT NULL DEFAULT (
                                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                    ),
                terminal_at         TEXT,
                -- Freeze the exact per-state column tuple:
                CHECK (
                    (state = 'PREPARED' AND artifact_id IS NULL AND gap_reason IS NULL
                        AND gap_detail IS NULL AND terminal_at IS NULL)
                    OR (state = 'COMPLETE' AND artifact_id IS NOT NULL AND gap_reason IS NULL
                        AND gap_detail IS NULL AND terminal_at IS NOT NULL)
                    OR (state = 'GAP' AND artifact_id IS NULL AND gap_reason IS NOT NULL
                        AND terminal_at IS NOT NULL)
                ),
                UNIQUE (source_id, session_id, sequence_number),
                FOREIGN KEY (artifact_id, source_id)
                    REFERENCES artifacts (artifact_id, source_id)
            )
            """,
            # ── Backfill every capture_occurrences row as a COMPLETE expected row.
            #    created_at == terminal_at == the preserved completed_at (a
            #    documented historical approximation; no original intent time exists).
            """
            INSERT INTO expected_occurrences
                (occurrence_id, source_id, session_id, sequence_number,
                 expected_started_at, expected_ended_at, state, artifact_id,
                 gap_reason, gap_detail, created_at, terminal_at)
            SELECT occurrence_id, source_id, session_id, sequence_number,
                   capture_started_at, capture_ended_at, 'COMPLETE', artifact_id,
                   NULL, NULL, completed_at, completed_at
            FROM capture_occurrences
            """,
            # ── Rebuild outbox_events: preserve every existing row byte-for-byte,
            #    but change the constraints to support both terminal event pairs and
            #    point the FK at expected_occurrences with UNIQUE(aggregate_id).
            """
            CREATE TABLE outbox_events_v3 (
                event_id       TEXT PRIMARY KEY,
                event_type     TEXT NOT NULL
                               CHECK (event_type IN ('SegmentFinalized', 'GapRecorded')),
                aggregate_type TEXT NOT NULL
                               CHECK (aggregate_type IN ('capture_occurrence', 'expected_occurrence')),
                aggregate_id   TEXT NOT NULL
                               REFERENCES expected_occurrences (occurrence_id),
                payload_json   TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
                created_at     TEXT NOT NULL DEFAULT (
                                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                               ),
                published_at   TEXT,
                attempt_count  INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                last_error     TEXT,
                -- exact valid (event_type, aggregate_type) pairs only
                CHECK (
                    (event_type = 'SegmentFinalized' AND aggregate_type = 'capture_occurrence')
                    OR (event_type = 'GapRecorded' AND aggregate_type = 'expected_occurrence')
                ),
                UNIQUE (aggregate_id)
            )
            """,
            """
            INSERT INTO outbox_events_v3
                (event_id, event_type, aggregate_type, aggregate_id, payload_json,
                 payload_sha256, created_at, published_at, attempt_count, last_error)
            SELECT event_id, event_type, aggregate_type, aggregate_id, payload_json,
                   payload_sha256, created_at, published_at, attempt_count, last_error
            FROM outbox_events
            """,
            "DROP TABLE outbox_events",
            "ALTER TABLE outbox_events_v3 RENAME TO outbox_events",
            "CREATE INDEX idx_outbox_unpublished ON outbox_events(published_at)",

            # ── expected_occurrences guard triggers ─────────────────────────────
            # Only PREPARED→COMPLETE or PREPARED→GAP; identity/interval/created_at
            # immutable; terminal rows immutable; no deletes.
            """
            CREATE TRIGGER expected_occurrences_no_delete
            BEFORE DELETE ON expected_occurrences
            BEGIN
                SELECT RAISE(ABORT, 'expected_occurrences rows are append-only');
            END
            """,
            """
            CREATE TRIGGER expected_occurrences_guard_update
            BEFORE UPDATE ON expected_occurrences
            BEGIN
                SELECT CASE
                    WHEN OLD.state <> 'PREPARED' THEN
                        RAISE(ABORT, 'terminal expected_occurrences are immutable')
                    WHEN NEW.state NOT IN ('COMPLETE', 'GAP') THEN
                        RAISE(ABORT, 'only PREPARED->COMPLETE or PREPARED->GAP allowed')
                    WHEN NEW.occurrence_id <> OLD.occurrence_id
                        OR NEW.source_id <> OLD.source_id
                        OR NEW.session_id <> OLD.session_id
                        OR NEW.sequence_number <> OLD.sequence_number
                        OR NEW.expected_started_at <> OLD.expected_started_at
                        OR NEW.expected_ended_at <> OLD.expected_ended_at
                        OR NEW.created_at <> OLD.created_at THEN
                        RAISE(ABORT, 'identity, interval, and created_at are immutable')
                END;
            END
            """,
            # ── capture_occurrences terminal immutability ───────────────────────
            """
            CREATE TRIGGER capture_occurrences_no_update
            BEFORE UPDATE ON capture_occurrences
            BEGIN
                SELECT RAISE(ABORT, 'capture_occurrences terminal rows are immutable');
            END
            """,
            """
            CREATE TRIGGER capture_occurrences_no_delete
            BEFORE DELETE ON capture_occurrences
            BEGIN
                SELECT RAISE(ABORT, 'capture_occurrences terminal rows cannot be deleted');
            END
            """,
            # ── outbox terminal identity/payload immutability (publication meta ok)
            """
            CREATE TRIGGER outbox_events_guard_update
            BEFORE UPDATE ON outbox_events
            BEGIN
                SELECT CASE WHEN
                    NEW.event_id <> OLD.event_id
                    OR NEW.event_type <> OLD.event_type
                    OR NEW.aggregate_type <> OLD.aggregate_type
                    OR NEW.aggregate_id <> OLD.aggregate_id
                    OR NEW.payload_json <> OLD.payload_json
                    OR NEW.payload_sha256 <> OLD.payload_sha256
                    OR NEW.created_at <> OLD.created_at
                THEN RAISE(ABORT, 'outbox identity, payload, and created_at are immutable')
                END;
            END
            """,
            """
            CREATE TRIGGER outbox_events_no_delete
            BEFORE DELETE ON outbox_events
            BEGIN
                SELECT RAISE(ABORT, 'outbox_events rows cannot be deleted');
            END
            """,
            # ── terminal-event correctness: SegmentFinalized only for a COMPLETE
            #    expected row + matching capture_occurrences; GapRecorded only for a
            #    GAP expected row with NO capture_occurrences row.
            """
            CREATE TRIGGER outbox_events_terminal_correctness
            BEFORE INSERT ON outbox_events
            BEGIN
                SELECT CASE
                    WHEN NEW.event_type = 'SegmentFinalized' AND (
                        (SELECT state FROM expected_occurrences
                         WHERE occurrence_id = NEW.aggregate_id) IS NOT 'COMPLETE'
                        OR NOT EXISTS (SELECT 1 FROM capture_occurrences
                                       WHERE occurrence_id = NEW.aggregate_id)
                    ) THEN
                        RAISE(ABORT, 'SegmentFinalized requires COMPLETE expected + capture occurrence')
                    WHEN NEW.event_type = 'GapRecorded' AND (
                        (SELECT state FROM expected_occurrences
                         WHERE occurrence_id = NEW.aggregate_id) IS NOT 'GAP'
                        OR EXISTS (SELECT 1 FROM capture_occurrences
                                   WHERE occurrence_id = NEW.aggregate_id)
                    ) THEN
                        RAISE(ABORT, 'GapRecorded requires GAP expected + no capture occurrence')
                END;
            END
            """,
        ),
    ),
    Migration(
        version=4,
        name="derived_processing",
        statements=(
            # ── processing_jobs: one idempotent job per (input, processor, version,
            #    contract, canonical parameters). Only PREPARED→COMPLETE. COMPLETE is
            #    immutable. No delete. Completion requires a matching output artifact
            #    and lineage (enforced by the transition trigger below). ────────────
            """
            CREATE TABLE processing_jobs (
                job_id             TEXT PRIMARY KEY,
                input_artifact_id  TEXT NOT NULL REFERENCES artifacts(artifact_id),
                processor_name     TEXT NOT NULL
                                   CHECK (processor_name = 'artifact-fingerprint'),
                processor_version  TEXT NOT NULL CHECK (processor_version = '1'),
                output_contract    TEXT NOT NULL
                                   CHECK (output_contract = 'artifact-fingerprint-report.v1'),
                parameters_json    TEXT NOT NULL,
                parameters_sha256  TEXT NOT NULL
                                   CHECK (parameters_sha256 GLOB
                                       '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                       || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                       || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                       || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                       || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                       || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                       || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                       || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                state              TEXT NOT NULL CHECK (state IN ('PREPARED', 'COMPLETE')),
                output_artifact_id TEXT REFERENCES artifacts(artifact_id),
                created_at         TEXT NOT NULL DEFAULT (
                                       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                   ),
                completed_at       TEXT,
                -- Freeze the exact per-state column tuple: PREPARED has no output or
                -- completion; COMPLETE requires both.
                CHECK (
                    (state = 'PREPARED' AND output_artifact_id IS NULL
                        AND completed_at IS NULL)
                    OR (state = 'COMPLETE' AND output_artifact_id IS NOT NULL
                        AND completed_at IS NOT NULL)
                ),
                -- The idempotency key: at most one job per canonical identity tuple.
                UNIQUE (input_artifact_id, processor_name, processor_version,
                        output_contract, parameters_sha256)
            )
            """,
            # ── artifact_lineage: exactly one parent per derived child. Parent must
            #    be raw_media, child metadata. Child differs from parent. Immutable,
            #    no delete. ─────────────────────────────────────────────────────────
            """
            CREATE TABLE artifact_lineage (
                child_artifact_id  TEXT PRIMARY KEY REFERENCES artifacts(artifact_id),
                parent_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                relation_type      TEXT NOT NULL CHECK (relation_type = 'derived_from'),
                CHECK (child_artifact_id <> parent_artifact_id)
            )
            """,
            # Job lookup by input (idempotency probe) and by output (reverse lookup).
            "CREATE INDEX idx_processing_jobs_input ON processing_jobs(input_artifact_id)",
            "CREATE INDEX idx_processing_jobs_output ON processing_jobs(output_artifact_id)",
            # Lineage lookup by parent (all children of a raw artifact).
            "CREATE INDEX idx_artifact_lineage_parent ON artifact_lineage(parent_artifact_id)",

            # ── processing_jobs guard triggers ──────────────────────────────────
            # Only PREPARED→COMPLETE; identity + creation fields immutable; COMPLETE
            # rows immutable; no deletes.
            """
            CREATE TRIGGER processing_jobs_no_delete
            BEFORE DELETE ON processing_jobs
            BEGIN
                SELECT RAISE(ABORT, 'processing_jobs rows are append-only');
            END
            """,
            """
            CREATE TRIGGER processing_jobs_guard_update
            BEFORE UPDATE ON processing_jobs
            BEGIN
                SELECT CASE
                    WHEN OLD.state <> 'PREPARED' THEN
                        RAISE(ABORT, 'COMPLETE processing_jobs are immutable')
                    WHEN NEW.state <> 'COMPLETE' THEN
                        RAISE(ABORT, 'only PREPARED->COMPLETE is allowed')
                    WHEN NEW.job_id <> OLD.job_id
                        OR NEW.input_artifact_id <> OLD.input_artifact_id
                        OR NEW.processor_name <> OLD.processor_name
                        OR NEW.processor_version <> OLD.processor_version
                        OR NEW.output_contract <> OLD.output_contract
                        OR NEW.parameters_json <> OLD.parameters_json
                        OR NEW.parameters_sha256 <> OLD.parameters_sha256
                        OR NEW.created_at <> OLD.created_at THEN
                        RAISE(ABORT, 'job identity, parameters, and created_at are immutable')
                    -- Completion requires matching lineage: the output artifact must
                    -- already have a lineage row naming this input as its parent.
                    WHEN NOT EXISTS (
                        SELECT 1 FROM artifact_lineage
                        WHERE child_artifact_id = NEW.output_artifact_id
                          AND parent_artifact_id = NEW.input_artifact_id
                          AND relation_type = 'derived_from'
                    ) THEN
                        RAISE(ABORT, 'COMPLETE requires matching derived lineage')
                END;
            END
            """,
            # ── artifact_lineage guard triggers ─────────────────────────────────
            # Immutable, no delete, and the parent/child kinds are frozen.
            """
            CREATE TRIGGER artifact_lineage_no_update
            BEFORE UPDATE ON artifact_lineage
            BEGIN
                SELECT RAISE(ABORT, 'artifact_lineage rows are immutable');
            END
            """,
            """
            CREATE TRIGGER artifact_lineage_no_delete
            BEFORE DELETE ON artifact_lineage
            BEGIN
                SELECT RAISE(ABORT, 'artifact_lineage rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER artifact_lineage_kind_check
            BEFORE INSERT ON artifact_lineage
            BEGIN
                SELECT CASE
                    WHEN (SELECT artifact_kind FROM artifacts
                          WHERE artifact_id = NEW.parent_artifact_id) IS NOT 'raw_media' THEN
                        RAISE(ABORT, 'lineage parent must be raw_media')
                    WHEN (SELECT artifact_kind FROM artifacts
                          WHERE artifact_id = NEW.child_artifact_id) IS NOT 'metadata' THEN
                        RAISE(ABORT, 'lineage child must be metadata')
                END;
            END
            """,
            # ── artifacts immutability (v4 hardens the existing ledger) ──────────
            # SPEC-004/005 adopt-or-compare uses INSERT + read; artifacts rows are
            # never updated or deleted. v4 makes that explicit and enforced so a
            # derived-artifact insert cannot later be mutated. Adopt-or-compare still
            # works because it never issues UPDATE/DELETE on artifacts.
            """
            CREATE TRIGGER artifacts_no_update
            BEFORE UPDATE ON artifacts
            BEGIN
                SELECT RAISE(ABORT, 'artifacts rows are immutable');
            END
            """,
            """
            CREATE TRIGGER artifacts_no_delete
            BEFORE DELETE ON artifacts
            BEGIN
                SELECT RAISE(ABORT, 'artifacts rows cannot be deleted');
            END
            """,
        ),
    ),
    Migration(
        version=5,
        name="mobile_recording_import",
        statements=(
            # ── recording_imports: the preserved original recording authority. ──
            #    recording_id == 'sha256:' || digest_value; storage_uri is the frozen
            #    content-addressed .source path. PREPARED has all probe fields NULL;
            #    COMPLETE requires them all + completed_at. Identity/source/time/
            #    digest/path immutable; COMPLETE immutable; no delete.
            """
            CREATE TABLE recording_imports (
                recording_id           TEXT PRIMARY KEY
                                       CHECK (recording_id = 'sha256:' || digest_value),
                source_id              TEXT NOT NULL
                                       CHECK (length(source_id) BETWEEN 1 AND 128),
                source_descriptor_json TEXT NOT NULL,
                source_descriptor_sha256 TEXT NOT NULL CHECK (length(source_descriptor_sha256) = 64),
                byte_size              INTEGER NOT NULL CHECK (byte_size >= 0),
                digest_algorithm       TEXT NOT NULL CHECK (digest_algorithm = 'sha256'),
                digest_value           TEXT NOT NULL UNIQUE CHECK (length(digest_value) = 64),
                storage_uri            TEXT NOT NULL UNIQUE
                                       CHECK (storage_uri = 'file:recordings/sha256/'
                                              || substr(digest_value, 1, 2) || '/'
                                              || digest_value || '.source'),
                recording_started_at   TEXT NOT NULL,
                time_authority         TEXT NOT NULL CHECK (time_authority = 'operator_asserted_utc'),
                container_class        TEXT CHECK (container_class IS NULL OR
                                           container_class IN ('quicktime_mov', 'iso_bmff_mp4')),
                major_brand            TEXT,
                video_stream_index     INTEGER CHECK (video_stream_index IS NULL OR video_stream_index >= 0),
                video_codec            TEXT CHECK (video_codec IS NULL OR video_codec IN ('h264', 'hevc')),
                pixel_format           TEXT CHECK (pixel_format IS NULL OR pixel_format = 'yuv420p'),
                coded_width            INTEGER CHECK (coded_width IS NULL OR coded_width > 0),
                coded_height           INTEGER CHECK (coded_height IS NULL OR coded_height > 0),
                duration_ms            INTEGER CHECK (duration_ms IS NULL OR duration_ms > 0),
                display_rotation_ccw_degrees INTEGER
                                       CHECK (display_rotation_ccw_degrees IS NULL OR
                                              display_rotation_ccw_degrees IN (0, 90, 180, 270)),
                state                  TEXT NOT NULL CHECK (state IN ('PREPARED', 'COMPLETE')),
                created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                completed_at           TEXT,
                -- exact per-state column tuple: PREPARED has all probe fields + completion NULL.
                CHECK (
                    (state = 'PREPARED' AND container_class IS NULL AND major_brand IS NULL
                        AND video_stream_index IS NULL AND video_codec IS NULL
                        AND pixel_format IS NULL AND coded_width IS NULL AND coded_height IS NULL
                        AND duration_ms IS NULL AND display_rotation_ccw_degrees IS NULL
                        AND completed_at IS NULL)
                    OR (state = 'COMPLETE' AND container_class IS NOT NULL
                        AND major_brand IS NOT NULL
                        AND video_stream_index IS NOT NULL AND video_codec IS NOT NULL
                        AND pixel_format IS NOT NULL AND coded_width IS NOT NULL
                        AND coded_height IS NOT NULL AND duration_ms IS NOT NULL
                        AND display_rotation_ccw_degrees IS NOT NULL AND completed_at IS NOT NULL)
                )
            )
            """,
            # ── recording_import_jobs: one import per (source,session,sequence). ─
            """
            CREATE TABLE recording_import_jobs (
                job_id             TEXT PRIMARY KEY,
                recording_id       TEXT NOT NULL REFERENCES recording_imports(recording_id),
                source_id          TEXT NOT NULL,
                session_id         TEXT NOT NULL,
                sequence_number    INTEGER NOT NULL CHECK (sequence_number > 0),
                occurrence_id      TEXT NOT NULL UNIQUE,
                start_offset_ms    INTEGER NOT NULL CHECK (start_offset_ms >= 0),
                duration_ms        INTEGER NOT NULL CHECK (duration_ms = 10000),
                parameters_json    TEXT NOT NULL,
                parameters_sha256  TEXT NOT NULL CHECK (length(parameters_sha256) = 64),
                toolchain_fingerprint_sha256 TEXT NOT NULL CHECK (length(toolchain_fingerprint_sha256) = 64),
                state              TEXT NOT NULL CHECK (state IN ('PREPARED', 'COMPLETE')),
                output_artifact_id TEXT REFERENCES artifacts(artifact_id),
                created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                completed_at       TEXT,
                CHECK (
                    (state = 'PREPARED' AND output_artifact_id IS NULL AND completed_at IS NULL)
                    OR (state = 'COMPLETE' AND output_artifact_id IS NOT NULL AND completed_at IS NOT NULL)
                ),
                UNIQUE (source_id, session_id, sequence_number)
            )
            """,
            # ── recording_segment_provenance: immutable recording↔occurrence link. ─
            """
            CREATE TABLE recording_segment_provenance (
                occurrence_id      TEXT PRIMARY KEY REFERENCES capture_occurrences(occurrence_id),
                import_job_id      TEXT NOT NULL UNIQUE REFERENCES recording_import_jobs(job_id),
                recording_id       TEXT NOT NULL REFERENCES recording_imports(recording_id),
                source_offset_ms   INTEGER NOT NULL CHECK (source_offset_ms >= 0),
                source_duration_ms INTEGER NOT NULL CHECK (source_duration_ms = 10000),
                parameters_sha256  TEXT NOT NULL CHECK (length(parameters_sha256) = 64),
                created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            "CREATE INDEX idx_recording_import_jobs_recording ON recording_import_jobs(recording_id)",
            "CREATE INDEX idx_recording_import_jobs_output ON recording_import_jobs(output_artifact_id)",
            "CREATE INDEX idx_recording_provenance_job ON recording_segment_provenance(import_job_id)",
            "CREATE INDEX idx_recording_provenance_recording ON recording_segment_provenance(recording_id)",

            # ── recording_imports guards ────────────────────────────────────────
            """
            CREATE TRIGGER recording_imports_no_delete
            BEFORE DELETE ON recording_imports
            BEGIN
                SELECT RAISE(ABORT, 'recording_imports rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER recording_imports_guard_update
            BEFORE UPDATE ON recording_imports
            BEGIN
                SELECT CASE
                    WHEN OLD.state <> 'PREPARED' THEN
                        RAISE(ABORT, 'COMPLETE recording_imports are immutable')
                    WHEN NEW.state <> 'COMPLETE' THEN
                        RAISE(ABORT, 'only PREPARED->COMPLETE is allowed')
                    -- identity/source/time/digest/path/creation immutable across the transition
                    WHEN NEW.recording_id <> OLD.recording_id
                        OR NEW.source_id <> OLD.source_id
                        OR NEW.source_descriptor_json <> OLD.source_descriptor_json
                        OR NEW.source_descriptor_sha256 <> OLD.source_descriptor_sha256
                        OR NEW.byte_size <> OLD.byte_size
                        OR NEW.digest_algorithm <> OLD.digest_algorithm
                        OR NEW.digest_value <> OLD.digest_value
                        OR NEW.storage_uri <> OLD.storage_uri
                        OR NEW.recording_started_at <> OLD.recording_started_at
                        OR NEW.time_authority <> OLD.time_authority
                        OR NEW.created_at <> OLD.created_at THEN
                        RAISE(ABORT, 'recording identity/source/time/digest/path are immutable')
                END;
            END
            """,
            # ── recording_import_jobs guards ────────────────────────────────────
            """
            CREATE TRIGGER recording_import_jobs_no_delete
            BEFORE DELETE ON recording_import_jobs
            BEGIN
                SELECT RAISE(ABORT, 'recording_import_jobs rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER recording_import_jobs_guard_update
            BEFORE UPDATE ON recording_import_jobs
            BEGIN
                SELECT CASE
                    WHEN OLD.state <> 'PREPARED' THEN
                        RAISE(ABORT, 'COMPLETE recording_import_jobs are immutable')
                    WHEN NEW.state <> 'COMPLETE' THEN
                        RAISE(ABORT, 'only PREPARED->COMPLETE is allowed')
                    WHEN NEW.job_id <> OLD.job_id
                        OR NEW.recording_id <> OLD.recording_id
                        OR NEW.source_id <> OLD.source_id
                        OR NEW.session_id <> OLD.session_id
                        OR NEW.sequence_number <> OLD.sequence_number
                        OR NEW.occurrence_id <> OLD.occurrence_id
                        OR NEW.start_offset_ms <> OLD.start_offset_ms
                        OR NEW.duration_ms <> OLD.duration_ms
                        OR NEW.parameters_json <> OLD.parameters_json
                        OR NEW.parameters_sha256 <> OLD.parameters_sha256
                        OR NEW.toolchain_fingerprint_sha256 <> OLD.toolchain_fingerprint_sha256
                        OR NEW.created_at <> OLD.created_at THEN
                        RAISE(ABORT, 'import job identity and parameters are immutable')
                    -- completion requires the source recording COMPLETE with the
                    -- same source, a matching COMPLETE expected + capture occurrence
                    -- whose identity equals the job's, output == the capture artifact,
                    -- and a matching provenance row.
                    WHEN (SELECT state FROM recording_imports
                          WHERE recording_id = NEW.recording_id) IS NOT 'COMPLETE' THEN
                        RAISE(ABORT, 'COMPLETE import job requires a COMPLETE recording')
                    WHEN (SELECT source_id FROM recording_imports
                          WHERE recording_id = NEW.recording_id) IS NOT NEW.source_id THEN
                        RAISE(ABORT, 'import job source must equal its recording source')
                    WHEN (SELECT state FROM expected_occurrences
                          WHERE occurrence_id = NEW.occurrence_id) IS NOT 'COMPLETE' THEN
                        RAISE(ABORT, 'COMPLETE import job requires a COMPLETE expected occurrence')
                    WHEN NOT EXISTS (SELECT 1 FROM expected_occurrences
                          WHERE occurrence_id = NEW.occurrence_id
                            AND source_id = NEW.source_id
                            AND session_id = NEW.session_id
                            AND sequence_number = NEW.sequence_number) THEN
                        RAISE(ABORT, 'expected occurrence identity must equal the import job identity')
                    WHEN NOT EXISTS (SELECT 1 FROM capture_occurrences
                          WHERE occurrence_id = NEW.occurrence_id
                            AND source_id = NEW.source_id
                            AND session_id = NEW.session_id
                            AND sequence_number = NEW.sequence_number
                            AND artifact_id = NEW.output_artifact_id) THEN
                        RAISE(ABORT, 'COMPLETE import job output/identity must equal the capture occurrence')
                    WHEN NOT EXISTS (SELECT 1 FROM recording_segment_provenance
                          WHERE import_job_id = NEW.job_id
                            AND occurrence_id = NEW.occurrence_id
                            AND recording_id = NEW.recording_id) THEN
                        RAISE(ABORT, 'COMPLETE import job requires a matching provenance row')
                END;
            END
            """,
            # ── recording_segment_provenance guards ─────────────────────────────
            """
            CREATE TRIGGER recording_provenance_no_delete
            BEFORE DELETE ON recording_segment_provenance
            BEGIN
                SELECT RAISE(ABORT, 'recording_segment_provenance rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER recording_provenance_no_update
            BEFORE UPDATE ON recording_segment_provenance
            BEGIN
                SELECT RAISE(ABORT, 'recording_segment_provenance rows are immutable');
            END
            """,
            # insert-time consistency: provenance must equal its referenced import job
            # (recording, occurrence identity, source/session/sequence, offset,
            # duration, parameter hash).
            """
            CREATE TRIGGER recording_provenance_consistency
            BEFORE INSERT ON recording_segment_provenance
            BEGIN
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM recording_import_jobs j
                        WHERE j.job_id = NEW.import_job_id
                          AND j.recording_id = NEW.recording_id
                          AND j.occurrence_id = NEW.occurrence_id
                          AND j.start_offset_ms = NEW.source_offset_ms
                          AND j.duration_ms = NEW.source_duration_ms
                          AND j.parameters_sha256 = NEW.parameters_sha256
                    ) THEN
                        RAISE(ABORT, 'provenance must match its import job facts')
                END;
            END
            """,
        ),
    ),
    Migration(
        version=6,
        name="rtsp_capture",
        statements=(
            # ── rtsp_capture_attempts: one immutable informational row per PREPARED
            #    RTSP capture attempt. COMPLETE/GAP terminal authority stays SOLELY in
            #    expected_occurrences (SPEC-005 v3); this table never carries a state
            #    column and never stores clear connection/topology/camera data. The
            #    endpoint fingerprint is private correlation evidence (SHA-256 only),
            #    NOT the URL. Rows are append-only, immutable, non-deletable, and
            #    require a matching PREPARED expected occurrence + agreeing source. ──
            """
            CREATE TABLE rtsp_capture_attempts (
                occurrence_id                TEXT PRIMARY KEY
                                             REFERENCES expected_occurrences(occurrence_id),
                source_id                    TEXT NOT NULL
                                             CHECK (length(source_id) BETWEEN 1 AND 128),
                source_descriptor_json       TEXT NOT NULL,
                source_descriptor_sha256     TEXT NOT NULL
                                             CHECK (length(source_descriptor_sha256) = 64),
                endpoint_fingerprint_sha256  TEXT NOT NULL
                                             CHECK (length(endpoint_fingerprint_sha256) = 64),
                transport                    TEXT NOT NULL CHECK (transport = 'tcp'),
                duration_ms                  INTEGER NOT NULL CHECK (duration_ms = 10000),
                recipe_json                  TEXT NOT NULL,
                recipe_sha256                TEXT NOT NULL CHECK (length(recipe_sha256) = 64),
                toolchain_fingerprint_sha256 TEXT NOT NULL
                                             CHECK (length(toolchain_fingerprint_sha256) = 64),
                time_authority               TEXT NOT NULL
                                             CHECK (time_authority = 'local_system_observed_utc'),
                created_at                   TEXT NOT NULL DEFAULT (
                                                 strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                             ),
                -- Every SHA field is exactly 64 lowercase hex (defense beyond length).
                CHECK (source_descriptor_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                CHECK (endpoint_fingerprint_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                CHECK (recipe_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                CHECK (toolchain_fingerprint_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]')
            )
            """,
            "CREATE INDEX idx_rtsp_capture_attempts_source ON rtsp_capture_attempts(source_id)",

            # ── rtsp_capture_attempts guards ────────────────────────────────────
            # Append-only, immutable, non-deletable.
            """
            CREATE TRIGGER rtsp_capture_attempts_no_update
            BEFORE UPDATE ON rtsp_capture_attempts
            BEGIN
                SELECT RAISE(ABORT, 'rtsp_capture_attempts rows are immutable');
            END
            """,
            """
            CREATE TRIGGER rtsp_capture_attempts_no_delete
            BEFORE DELETE ON rtsp_capture_attempts
            BEGIN
                SELECT RAISE(ABORT, 'rtsp_capture_attempts rows cannot be deleted');
            END
            """,
            # Insert requires a matching PREPARED expected occurrence whose source
            # equals the attempt's source. (COMPLETE/GAP terminalization happens after
            # the attempt row is committed, so the state is checked as PREPARED here.)
            """
            CREATE TRIGGER rtsp_capture_attempts_requires_prepared
            BEFORE INSERT ON rtsp_capture_attempts
            BEGIN
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM expected_occurrences
                        WHERE occurrence_id = NEW.occurrence_id
                          AND source_id = NEW.source_id
                          AND state = 'PREPARED'
                    ) THEN
                        RAISE(ABORT,
                            'rtsp attempt requires a matching PREPARED expected occurrence + source')
                END;
            END
            """,
        ),
    ),
    Migration(
        version=7,
        name="multi_source_occurrence_assertions",
        statements=(
            # ── SPEC-011 migration v7 — deferred-FK single-transaction rebuild ──────
            # The driver already runs inside `PRAGMA foreign_keys = ON` + a single
            # `BEGIN IMMEDIATE` and COMMITs at the end; an unsatisfied deferred FK
            # therefore aborts the driver COMMIT automatically (this is the frozen
            # "require foreign_key_check returns no rows" guarantee). We ONLY add the
            # deferred toggle here; we never touch the migration driver, add an out-of-
            # transaction FK toggle, a schema-rebuild mode, or a migration callback.
            #
            # Goal: rebuild capture_occurrences and expected_occurrences WITHOUT the
            # old composite `(artifact_id, source_id) -> artifacts` dependency (keeping
            # `artifact_id -> artifacts.artifact_id`), rebuild the three proven direct
            # children (outbox_events, rtsp_capture_attempts,
            # recording_segment_provenance), add capture_occurrence_assertions, backfill
            # every existing COMPLETE occurrence as a legacy_digest_v1 assertion, and
            # recreate every affected index and trigger. All existing rows/values/
            # timestamps are copied byte-for-byte.
            "PRAGMA defer_foreign_keys = ON",

            # 1) Replacement parents (no composite artifacts FK; keep artifact_id FK).
            """
            CREATE TABLE capture_occurrences_v7 (
                occurrence_id     TEXT PRIMARY KEY,
                source_id         TEXT NOT NULL,
                session_id        TEXT NOT NULL,
                sequence_number   INTEGER NOT NULL CHECK (sequence_number > 0),
                capture_started_at TEXT NOT NULL,
                capture_ended_at   TEXT NOT NULL,
                artifact_id       TEXT NOT NULL REFERENCES artifacts (artifact_id),
                status            TEXT NOT NULL DEFAULT 'COMPLETE'
                                  CHECK (status = 'COMPLETE'),
                completed_at      TEXT NOT NULL DEFAULT (
                                      strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                  ),
                UNIQUE (source_id, session_id, sequence_number)
            )
            """,
            """
            CREATE TABLE expected_occurrences_v7 (
                occurrence_id       TEXT PRIMARY KEY,
                source_id           TEXT NOT NULL,
                session_id          TEXT NOT NULL,
                sequence_number     INTEGER NOT NULL CHECK (sequence_number > 0),
                expected_started_at TEXT NOT NULL,
                expected_ended_at   TEXT NOT NULL,
                state               TEXT NOT NULL
                                    CHECK (state IN ('PREPARED', 'COMPLETE', 'GAP')),
                artifact_id         TEXT REFERENCES artifacts (artifact_id),
                gap_reason          TEXT
                                    CHECK (gap_reason IS NULL OR gap_reason IN
                                        ('source_unavailable', 'process_interruption',
                                         'storage_pressure', 'unknown')),
                gap_detail          TEXT
                                    CHECK (gap_detail IS NULL OR length(gap_detail) <= 500),
                created_at          TEXT NOT NULL DEFAULT (
                                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                    ),
                terminal_at         TEXT,
                CHECK (
                    (state = 'PREPARED' AND artifact_id IS NULL AND gap_reason IS NULL
                        AND gap_detail IS NULL AND terminal_at IS NULL)
                    OR (state = 'COMPLETE' AND artifact_id IS NOT NULL AND gap_reason IS NULL
                        AND gap_detail IS NULL AND terminal_at IS NOT NULL)
                    OR (state = 'GAP' AND artifact_id IS NULL AND gap_reason IS NOT NULL
                        AND terminal_at IS NOT NULL)
                ),
                UNIQUE (source_id, session_id, sequence_number)
            )
            """,

            # 2) Replacement direct children pointing at the replacement parents.
            """
            CREATE TABLE outbox_events_v7 (
                event_id       TEXT PRIMARY KEY,
                event_type     TEXT NOT NULL
                               CHECK (event_type IN ('SegmentFinalized', 'GapRecorded')),
                aggregate_type TEXT NOT NULL
                               CHECK (aggregate_type IN ('capture_occurrence', 'expected_occurrence')),
                aggregate_id   TEXT NOT NULL
                               REFERENCES expected_occurrences_v7 (occurrence_id),
                payload_json   TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
                created_at     TEXT NOT NULL DEFAULT (
                                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                               ),
                published_at   TEXT,
                attempt_count  INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                last_error     TEXT,
                CHECK (
                    (event_type = 'SegmentFinalized' AND aggregate_type = 'capture_occurrence')
                    OR (event_type = 'GapRecorded' AND aggregate_type = 'expected_occurrence')
                ),
                UNIQUE (aggregate_id)
            )
            """,
            """
            CREATE TABLE rtsp_capture_attempts_v7 (
                occurrence_id                TEXT PRIMARY KEY
                                             REFERENCES expected_occurrences_v7(occurrence_id),
                source_id                    TEXT NOT NULL
                                             CHECK (length(source_id) BETWEEN 1 AND 128),
                source_descriptor_json       TEXT NOT NULL,
                source_descriptor_sha256     TEXT NOT NULL
                                             CHECK (length(source_descriptor_sha256) = 64),
                endpoint_fingerprint_sha256  TEXT NOT NULL
                                             CHECK (length(endpoint_fingerprint_sha256) = 64),
                transport                    TEXT NOT NULL CHECK (transport = 'tcp'),
                duration_ms                  INTEGER NOT NULL CHECK (duration_ms = 10000),
                recipe_json                  TEXT NOT NULL,
                recipe_sha256                TEXT NOT NULL CHECK (length(recipe_sha256) = 64),
                toolchain_fingerprint_sha256 TEXT NOT NULL
                                             CHECK (length(toolchain_fingerprint_sha256) = 64),
                time_authority               TEXT NOT NULL
                                             CHECK (time_authority = 'local_system_observed_utc'),
                created_at                   TEXT NOT NULL DEFAULT (
                                                 strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                             ),
                CHECK (source_descriptor_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                CHECK (endpoint_fingerprint_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                CHECK (recipe_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                CHECK (toolchain_fingerprint_sha256 GLOB
                    '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                    || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]')
            )
            """,
            """
            CREATE TABLE recording_segment_provenance_v7 (
                occurrence_id      TEXT PRIMARY KEY REFERENCES capture_occurrences_v7(occurrence_id),
                import_job_id      TEXT NOT NULL UNIQUE REFERENCES recording_import_jobs(job_id),
                recording_id       TEXT NOT NULL REFERENCES recording_imports(recording_id),
                source_offset_ms   INTEGER NOT NULL CHECK (source_offset_ms >= 0),
                source_duration_ms INTEGER NOT NULL CHECK (source_duration_ms = 10000),
                parameters_sha256  TEXT NOT NULL CHECK (length(parameters_sha256) = 64),
                created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,

            # 3) The new immutable occurrence-assertion table (composite FK to the
            #    rebuilt capture occurrence identity; artifact_id -> artifacts).
            """
            CREATE TABLE capture_occurrence_assertions (
                occurrence_id   TEXT PRIMARY KEY
                                REFERENCES capture_occurrences_v7(occurrence_id),
                source_id       TEXT NOT NULL,
                artifact_id     TEXT NOT NULL REFERENCES artifacts(artifact_id),
                manifest_layout TEXT NOT NULL
                                CHECK (manifest_layout IN ('legacy_digest_v1', 'occurrence_v1')),
                manifest_uri    TEXT NOT NULL UNIQUE CHECK (length(manifest_uri) >= 1),
                manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64 AND
                                manifest_sha256 GLOB
                                '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
                                || '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
                finalized_at    TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                -- Composite FK to the matching (occurrence_id, source_id, artifact_id)
                -- capture occurrence identity.
                FOREIGN KEY (occurrence_id, source_id, artifact_id)
                    REFERENCES capture_occurrences_v7 (occurrence_id, source_id, artifact_id)
            )
            """,
            # The composite FK above needs a matching UNIQUE/PK target on the parent.
            "CREATE UNIQUE INDEX ux_capture_occurrences_v7_identity "
            "ON capture_occurrences_v7 (occurrence_id, source_id, artifact_id)",

            # 4) Copy and validate all parent rows (exact values/timestamps).
            """
            INSERT INTO capture_occurrences_v7
                (occurrence_id, source_id, session_id, sequence_number,
                 capture_started_at, capture_ended_at, artifact_id, status, completed_at)
            SELECT occurrence_id, source_id, session_id, sequence_number,
                   capture_started_at, capture_ended_at, artifact_id, status, completed_at
            FROM capture_occurrences
            """,
            """
            INSERT INTO expected_occurrences_v7
                (occurrence_id, source_id, session_id, sequence_number,
                 expected_started_at, expected_ended_at, state, artifact_id,
                 gap_reason, gap_detail, created_at, terminal_at)
            SELECT occurrence_id, source_id, session_id, sequence_number,
                   expected_started_at, expected_ended_at, state, artifact_id,
                   gap_reason, gap_detail, created_at, terminal_at
            FROM expected_occurrences
            """,

            # 5) Copy and validate all child rows.
            """
            INSERT INTO outbox_events_v7
                (event_id, event_type, aggregate_type, aggregate_id, payload_json,
                 payload_sha256, created_at, published_at, attempt_count, last_error)
            SELECT event_id, event_type, aggregate_type, aggregate_id, payload_json,
                   payload_sha256, created_at, published_at, attempt_count, last_error
            FROM outbox_events
            """,
            """
            INSERT INTO rtsp_capture_attempts_v7
                (occurrence_id, source_id, source_descriptor_json, source_descriptor_sha256,
                 endpoint_fingerprint_sha256, transport, duration_ms, recipe_json,
                 recipe_sha256, toolchain_fingerprint_sha256, time_authority, created_at)
            SELECT occurrence_id, source_id, source_descriptor_json, source_descriptor_sha256,
                   endpoint_fingerprint_sha256, transport, duration_ms, recipe_json,
                   recipe_sha256, toolchain_fingerprint_sha256, time_authority, created_at
            FROM rtsp_capture_attempts
            """,
            """
            INSERT INTO recording_segment_provenance_v7
                (occurrence_id, import_job_id, recording_id, source_offset_ms,
                 source_duration_ms, parameters_sha256, created_at)
            SELECT occurrence_id, import_job_id, recording_id, source_offset_ms,
                   source_duration_ms, parameters_sha256, created_at
            FROM recording_segment_provenance
            """,

            # 6) Backfill legacy_digest_v1 assertions for every existing COMPLETE
            #    occurrence, pointing at the EXISTING digest-addressed manifest URI +
            #    SHA (no new file is created). finalized_at is the artifact's frozen
            #    finalized time; source/artifact come from the completed occurrence.
            """
            INSERT INTO capture_occurrence_assertions
                (occurrence_id, source_id, artifact_id, manifest_layout, manifest_uri,
                 manifest_sha256, finalized_at, created_at)
            SELECT co.occurrence_id, co.source_id, co.artifact_id, 'legacy_digest_v1',
                   a.manifest_uri, a.manifest_sha256, a.finalized_at, co.completed_at
            FROM capture_occurrences_v7 co
            JOIN artifacts a ON a.artifact_id = co.artifact_id
            """,

            # 7) Drop external triggers referencing the rebuilt parents (recreated in
            #    step 10). These are the triggers whose bodies read/guard the parents.
            "DROP TRIGGER IF EXISTS outbox_events_terminal_correctness",
            "DROP TRIGGER IF EXISTS recording_import_jobs_guard_update",

            # 8) Drop old child tables before old parents.
            "DROP TABLE outbox_events",
            "DROP TABLE rtsp_capture_attempts",
            "DROP TABLE recording_segment_provenance",

            # 9) Drop old parent tables.
            "DROP TABLE capture_occurrences",
            "DROP TABLE expected_occurrences",

            # 10) Rename all replacements to canonical names.
            "ALTER TABLE capture_occurrences_v7 RENAME TO capture_occurrences",
            "ALTER TABLE expected_occurrences_v7 RENAME TO expected_occurrences",
            "ALTER TABLE outbox_events_v7 RENAME TO outbox_events",
            "ALTER TABLE rtsp_capture_attempts_v7 RENAME TO rtsp_capture_attempts",
            "ALTER TABLE recording_segment_provenance_v7 RENAME TO recording_segment_provenance",

            # 11) Recreate every index (v2/v5/v6) plus the new assertion indexes.
            "CREATE INDEX idx_occurrence_artifact ON capture_occurrences(artifact_id)",
            "CREATE INDEX idx_outbox_unpublished ON outbox_events(published_at)",
            "CREATE INDEX idx_rtsp_capture_attempts_source ON rtsp_capture_attempts(source_id)",
            "CREATE INDEX idx_recording_provenance_job "
            "ON recording_segment_provenance(import_job_id)",
            "CREATE INDEX idx_recording_provenance_recording "
            "ON recording_segment_provenance(recording_id)",
            "CREATE INDEX idx_capture_assertions_artifact "
            "ON capture_occurrence_assertions(artifact_id)",
            "CREATE INDEX idx_capture_assertions_source "
            "ON capture_occurrence_assertions(source_id)",

            # 12) Recreate every trigger on the rebuilt parents/children (v3/v6),
            #     plus the new immutable-assertion guards.
            """
            CREATE TRIGGER expected_occurrences_no_delete
            BEFORE DELETE ON expected_occurrences
            BEGIN
                SELECT RAISE(ABORT, 'expected_occurrences rows are append-only');
            END
            """,
            """
            CREATE TRIGGER expected_occurrences_guard_update
            BEFORE UPDATE ON expected_occurrences
            BEGIN
                SELECT CASE
                    WHEN OLD.state <> 'PREPARED' THEN
                        RAISE(ABORT, 'terminal expected_occurrences are immutable')
                    WHEN NEW.state NOT IN ('COMPLETE', 'GAP') THEN
                        RAISE(ABORT, 'only PREPARED->COMPLETE or PREPARED->GAP allowed')
                    WHEN NEW.occurrence_id <> OLD.occurrence_id
                        OR NEW.source_id <> OLD.source_id
                        OR NEW.session_id <> OLD.session_id
                        OR NEW.sequence_number <> OLD.sequence_number
                        OR NEW.expected_started_at <> OLD.expected_started_at
                        OR NEW.expected_ended_at <> OLD.expected_ended_at
                        OR NEW.created_at <> OLD.created_at THEN
                        RAISE(ABORT, 'identity, interval, and created_at are immutable')
                END;
            END
            """,
            """
            CREATE TRIGGER capture_occurrences_no_update
            BEFORE UPDATE ON capture_occurrences
            BEGIN
                SELECT RAISE(ABORT, 'capture_occurrences terminal rows are immutable');
            END
            """,
            """
            CREATE TRIGGER capture_occurrences_no_delete
            BEFORE DELETE ON capture_occurrences
            BEGIN
                SELECT RAISE(ABORT, 'capture_occurrences terminal rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER outbox_events_guard_update
            BEFORE UPDATE ON outbox_events
            BEGIN
                SELECT CASE WHEN
                    NEW.event_id <> OLD.event_id
                    OR NEW.event_type <> OLD.event_type
                    OR NEW.aggregate_type <> OLD.aggregate_type
                    OR NEW.aggregate_id <> OLD.aggregate_id
                    OR NEW.payload_json <> OLD.payload_json
                    OR NEW.payload_sha256 <> OLD.payload_sha256
                    OR NEW.created_at <> OLD.created_at
                THEN RAISE(ABORT, 'outbox identity, payload, and created_at are immutable')
                END;
            END
            """,
            """
            CREATE TRIGGER outbox_events_no_delete
            BEFORE DELETE ON outbox_events
            BEGIN
                SELECT RAISE(ABORT, 'outbox_events rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER outbox_events_terminal_correctness
            BEFORE INSERT ON outbox_events
            BEGIN
                SELECT CASE
                    WHEN NEW.event_type = 'SegmentFinalized' AND (
                        (SELECT state FROM expected_occurrences
                         WHERE occurrence_id = NEW.aggregate_id) IS NOT 'COMPLETE'
                        OR NOT EXISTS (SELECT 1 FROM capture_occurrences
                                       WHERE occurrence_id = NEW.aggregate_id)
                    ) THEN
                        RAISE(ABORT, 'SegmentFinalized requires COMPLETE expected + capture occurrence')
                    WHEN NEW.event_type = 'GapRecorded' AND (
                        (SELECT state FROM expected_occurrences
                         WHERE occurrence_id = NEW.aggregate_id) IS NOT 'GAP'
                        OR EXISTS (SELECT 1 FROM capture_occurrences
                                   WHERE occurrence_id = NEW.aggregate_id)
                    ) THEN
                        RAISE(ABORT, 'GapRecorded requires GAP expected + no capture occurrence')
                END;
            END
            """,
            """
            CREATE TRIGGER rtsp_capture_attempts_no_update
            BEFORE UPDATE ON rtsp_capture_attempts
            BEGIN
                SELECT RAISE(ABORT, 'rtsp_capture_attempts rows are immutable');
            END
            """,
            """
            CREATE TRIGGER rtsp_capture_attempts_no_delete
            BEFORE DELETE ON rtsp_capture_attempts
            BEGIN
                SELECT RAISE(ABORT, 'rtsp_capture_attempts rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER rtsp_capture_attempts_requires_prepared
            BEFORE INSERT ON rtsp_capture_attempts
            BEGIN
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM expected_occurrences
                        WHERE occurrence_id = NEW.occurrence_id
                          AND source_id = NEW.source_id
                          AND state = 'PREPARED'
                    ) THEN
                        RAISE(ABORT,
                            'rtsp attempt requires a matching PREPARED expected occurrence + source')
                END;
            END
            """,
            """
            CREATE TRIGGER recording_provenance_no_delete
            BEFORE DELETE ON recording_segment_provenance
            BEGIN
                SELECT RAISE(ABORT, 'recording_segment_provenance rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER recording_provenance_no_update
            BEFORE UPDATE ON recording_segment_provenance
            BEGIN
                SELECT RAISE(ABORT, 'recording_segment_provenance rows are immutable');
            END
            """,
            """
            CREATE TRIGGER recording_provenance_consistency
            BEFORE INSERT ON recording_segment_provenance
            BEGIN
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM recording_import_jobs j
                        WHERE j.job_id = NEW.import_job_id
                          AND j.recording_id = NEW.recording_id
                          AND j.occurrence_id = NEW.occurrence_id
                          AND j.start_offset_ms = NEW.source_offset_ms
                          AND j.duration_ms = NEW.source_duration_ms
                          AND j.parameters_sha256 = NEW.parameters_sha256
                    ) THEN
                        RAISE(ABORT, 'provenance must match its import job facts')
                END;
            END
            """,
            # Recreate the external recording_import_jobs guard (its body references the
            # rebuilt capture_occurrences / expected_occurrences); byte-identical to v5.
            """
            CREATE TRIGGER recording_import_jobs_guard_update
            BEFORE UPDATE ON recording_import_jobs
            BEGIN
                SELECT CASE
                    WHEN OLD.state <> 'PREPARED' THEN
                        RAISE(ABORT, 'COMPLETE recording_import_jobs are immutable')
                    WHEN NEW.state <> 'COMPLETE' THEN
                        RAISE(ABORT, 'only PREPARED->COMPLETE is allowed')
                    WHEN NEW.job_id <> OLD.job_id
                        OR NEW.recording_id <> OLD.recording_id
                        OR NEW.source_id <> OLD.source_id
                        OR NEW.session_id <> OLD.session_id
                        OR NEW.sequence_number <> OLD.sequence_number
                        OR NEW.occurrence_id <> OLD.occurrence_id
                        OR NEW.start_offset_ms <> OLD.start_offset_ms
                        OR NEW.duration_ms <> OLD.duration_ms
                        OR NEW.parameters_json <> OLD.parameters_json
                        OR NEW.parameters_sha256 <> OLD.parameters_sha256
                        OR NEW.toolchain_fingerprint_sha256 <> OLD.toolchain_fingerprint_sha256
                        OR NEW.created_at <> OLD.created_at THEN
                        RAISE(ABORT, 'import job identity and parameters are immutable')
                    WHEN (SELECT state FROM recording_imports
                          WHERE recording_id = NEW.recording_id) IS NOT 'COMPLETE' THEN
                        RAISE(ABORT, 'COMPLETE import job requires a COMPLETE recording')
                    WHEN (SELECT source_id FROM recording_imports
                          WHERE recording_id = NEW.recording_id) IS NOT NEW.source_id THEN
                        RAISE(ABORT, 'import job source must equal its recording source')
                    WHEN (SELECT state FROM expected_occurrences
                          WHERE occurrence_id = NEW.occurrence_id) IS NOT 'COMPLETE' THEN
                        RAISE(ABORT, 'COMPLETE import job requires a COMPLETE expected occurrence')
                    WHEN NOT EXISTS (SELECT 1 FROM expected_occurrences
                          WHERE occurrence_id = NEW.occurrence_id
                            AND source_id = NEW.source_id
                            AND session_id = NEW.session_id
                            AND sequence_number = NEW.sequence_number) THEN
                        RAISE(ABORT, 'expected occurrence identity must equal the import job identity')
                    WHEN NOT EXISTS (SELECT 1 FROM capture_occurrences
                          WHERE occurrence_id = NEW.occurrence_id
                            AND source_id = NEW.source_id
                            AND session_id = NEW.session_id
                            AND sequence_number = NEW.sequence_number
                            AND artifact_id = NEW.output_artifact_id) THEN
                        RAISE(ABORT, 'COMPLETE import job output/identity must equal the capture occurrence')
                    WHEN NOT EXISTS (SELECT 1 FROM recording_segment_provenance
                          WHERE import_job_id = NEW.job_id
                            AND occurrence_id = NEW.occurrence_id
                            AND recording_id = NEW.recording_id) THEN
                        RAISE(ABORT, 'COMPLETE import job requires a matching provenance row')
                END;
            END
            """,
            # New immutable-assertion guards.
            """
            CREATE TRIGGER capture_occurrence_assertions_no_update
            BEFORE UPDATE ON capture_occurrence_assertions
            BEGIN
                SELECT RAISE(ABORT, 'capture_occurrence_assertions rows are immutable');
            END
            """,
            """
            CREATE TRIGGER capture_occurrence_assertions_no_delete
            BEFORE DELETE ON capture_occurrence_assertions
            BEGIN
                SELECT RAISE(ABORT, 'capture_occurrence_assertions rows cannot be deleted');
            END
            """,
            # An assertion may only bind a COMPLETE capture occurrence whose identity
            # (occurrence, source, artifact) matches; PREPARED/GAP authority has none.
            """
            CREATE TRIGGER capture_occurrence_assertions_requires_complete
            BEFORE INSERT ON capture_occurrence_assertions
            BEGIN
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM capture_occurrences
                        WHERE occurrence_id = NEW.occurrence_id
                          AND source_id = NEW.source_id
                          AND artifact_id = NEW.artifact_id
                    ) THEN
                        RAISE(ABORT,
                            'assertion requires a matching COMPLETE capture occurrence identity')
                    WHEN (SELECT state FROM expected_occurrences
                          WHERE occurrence_id = NEW.occurrence_id) IS NOT 'COMPLETE' THEN
                        RAISE(ABORT, 'assertion requires a COMPLETE expected occurrence')
                END;
            END
            """,
        ),
    ),

    Migration(
        version=8,
        name="enforce_occurrence_assertion_cycle",
        statements=(
            "PRAGMA defer_foreign_keys = ON",
            """
            CREATE TABLE capture_occurrences_v8 (
                occurrence_id      TEXT PRIMARY KEY,
                source_id          TEXT NOT NULL,
                session_id         TEXT NOT NULL,
                sequence_number    INTEGER NOT NULL CHECK (sequence_number > 0),
                capture_started_at TEXT NOT NULL,
                capture_ended_at   TEXT NOT NULL,
                artifact_id        TEXT NOT NULL REFERENCES artifacts(artifact_id),
                status             TEXT NOT NULL DEFAULT 'COMPLETE'
                                   CHECK (status = 'COMPLETE'),
                completed_at       TEXT NOT NULL DEFAULT (
                                       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                   ),
                UNIQUE (source_id, session_id, sequence_number),
                UNIQUE (occurrence_id, source_id, artifact_id),
                FOREIGN KEY (occurrence_id, source_id, artifact_id)
                    REFERENCES capture_occurrence_assertions_v8
                        (occurrence_id, source_id, artifact_id)
                    DEFERRABLE INITIALLY DEFERRED
            )
            """,
            """
            CREATE TABLE capture_occurrence_assertions_v8 (
                occurrence_id   TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL,
                artifact_id     TEXT NOT NULL REFERENCES artifacts(artifact_id),
                manifest_layout TEXT NOT NULL
                                CHECK (manifest_layout IN
                                    ('legacy_digest_v1', 'occurrence_v1')),
                manifest_uri    TEXT NOT NULL UNIQUE CHECK (length(manifest_uri) >= 1),
                manifest_sha256 TEXT NOT NULL CHECK (
                    length(manifest_sha256) = 64
                    AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
                ),
                finalized_at    TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (
                                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                ),
                UNIQUE (occurrence_id, source_id, artifact_id),
                FOREIGN KEY (occurrence_id, source_id, artifact_id)
                    REFERENCES capture_occurrences_v8
                        (occurrence_id, source_id, artifact_id)
                    DEFERRABLE INITIALLY DEFERRED
            )
            """,
            """
            CREATE TABLE recording_segment_provenance_v8 (
                occurrence_id      TEXT PRIMARY KEY
                                   REFERENCES capture_occurrences_v8(occurrence_id),
                import_job_id      TEXT NOT NULL UNIQUE
                                   REFERENCES recording_import_jobs(job_id),
                recording_id       TEXT NOT NULL
                                   REFERENCES recording_imports(recording_id),
                source_offset_ms   INTEGER NOT NULL CHECK (source_offset_ms >= 0),
                source_duration_ms INTEGER NOT NULL CHECK (source_duration_ms = 10000),
                parameters_sha256  TEXT NOT NULL CHECK (length(parameters_sha256) = 64),
                created_at         TEXT NOT NULL DEFAULT (
                                       strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                                   )
            )
            """,
            """
            INSERT INTO capture_occurrences_v8
                (occurrence_id, source_id, session_id, sequence_number,
                 capture_started_at, capture_ended_at, artifact_id, status,
                 completed_at)
            SELECT occurrence_id, source_id, session_id, sequence_number,
                   capture_started_at, capture_ended_at, artifact_id, status,
                   completed_at
            FROM capture_occurrences
            """,
            """
            INSERT INTO capture_occurrence_assertions_v8
                (occurrence_id, source_id, artifact_id, manifest_layout,
                 manifest_uri, manifest_sha256, finalized_at, created_at)
            SELECT occurrence_id, source_id, artifact_id, manifest_layout,
                   manifest_uri, manifest_sha256, finalized_at, created_at
            FROM capture_occurrence_assertions
            """,
            """
            INSERT INTO recording_segment_provenance_v8
                (occurrence_id, import_job_id, recording_id, source_offset_ms,
                 source_duration_ms, parameters_sha256, created_at)
            SELECT occurrence_id, import_job_id, recording_id, source_offset_ms,
                   source_duration_ms, parameters_sha256, created_at
            FROM recording_segment_provenance
            """,
            """
            CREATE TABLE __migration_v8_authority_guard (
                ok INTEGER NOT NULL CHECK (ok = 1)
            )
            """,
            """
            INSERT INTO __migration_v8_authority_guard(ok)
            SELECT CASE WHEN EXISTS (
                SELECT 1
                FROM capture_occurrence_assertions_v8 a
                LEFT JOIN expected_occurrences e
                  ON e.occurrence_id = a.occurrence_id
                WHERE e.state IS NOT 'COMPLETE'
            ) THEN 0 ELSE 1 END
            """,
            "DROP TABLE __migration_v8_authority_guard",
            "DROP TRIGGER IF EXISTS outbox_events_terminal_correctness",
            "DROP TRIGGER IF EXISTS recording_import_jobs_guard_update",
            "DROP TABLE recording_segment_provenance",
            "DROP TABLE capture_occurrence_assertions",
            "DROP TABLE capture_occurrences",
            "ALTER TABLE capture_occurrences_v8 RENAME TO capture_occurrences",
            "ALTER TABLE capture_occurrence_assertions_v8 "
            "RENAME TO capture_occurrence_assertions",
            "ALTER TABLE recording_segment_provenance_v8 "
            "RENAME TO recording_segment_provenance",
            "CREATE INDEX idx_occurrence_artifact "
            "ON capture_occurrences(artifact_id)",
            "CREATE INDEX idx_capture_assertions_artifact "
            "ON capture_occurrence_assertions(artifact_id)",
            "CREATE INDEX idx_capture_assertions_source "
            "ON capture_occurrence_assertions(source_id)",
            "CREATE INDEX idx_recording_provenance_job "
            "ON recording_segment_provenance(import_job_id)",
            "CREATE INDEX idx_recording_provenance_recording "
            "ON recording_segment_provenance(recording_id)",
            """
            CREATE TRIGGER capture_occurrences_no_update
            BEFORE UPDATE ON capture_occurrences
            BEGIN
                SELECT RAISE(ABORT,
                    'capture_occurrences terminal rows are immutable');
            END
            """,
            """
            CREATE TRIGGER capture_occurrences_no_delete
            BEFORE DELETE ON capture_occurrences
            BEGIN
                SELECT RAISE(ABORT,
                    'capture_occurrences terminal rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER capture_occurrence_assertions_no_update
            BEFORE UPDATE ON capture_occurrence_assertions
            BEGIN
                SELECT RAISE(ABORT,
                    'capture_occurrence_assertions rows are immutable');
            END
            """,
            """
            CREATE TRIGGER capture_occurrence_assertions_no_delete
            BEFORE DELETE ON capture_occurrence_assertions
            BEGIN
                SELECT RAISE(ABORT,
                    'capture_occurrence_assertions rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER capture_occurrence_assertions_requires_complete
            BEFORE INSERT ON capture_occurrence_assertions
            BEGIN
                SELECT CASE
                    WHEN (SELECT state FROM expected_occurrences
                          WHERE occurrence_id = NEW.occurrence_id)
                         IS NOT 'COMPLETE'
                    THEN RAISE(ABORT,
                        'assertion requires a COMPLETE expected occurrence')
                END;
            END
            """,
            """
            CREATE TRIGGER outbox_events_terminal_correctness
            BEFORE INSERT ON outbox_events
            BEGIN
                SELECT CASE
                    WHEN NEW.event_type = 'SegmentFinalized' AND (
                        (SELECT state FROM expected_occurrences
                         WHERE occurrence_id = NEW.aggregate_id) IS NOT 'COMPLETE'
                        OR NOT EXISTS (
                            SELECT 1 FROM capture_occurrences
                            WHERE occurrence_id = NEW.aggregate_id
                        )
                    ) THEN RAISE(ABORT,
                        'SegmentFinalized requires COMPLETE expected + capture occurrence')
                    WHEN NEW.event_type = 'GapRecorded' AND (
                        (SELECT state FROM expected_occurrences
                         WHERE occurrence_id = NEW.aggregate_id) IS NOT 'GAP'
                        OR EXISTS (
                            SELECT 1 FROM capture_occurrences
                            WHERE occurrence_id = NEW.aggregate_id
                        )
                    ) THEN RAISE(ABORT,
                        'GapRecorded requires GAP expected + no capture occurrence')
                END;
            END
            """,
            """
            CREATE TRIGGER recording_provenance_no_delete
            BEFORE DELETE ON recording_segment_provenance
            BEGIN
                SELECT RAISE(ABORT,
                    'recording_segment_provenance rows cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER recording_provenance_no_update
            BEFORE UPDATE ON recording_segment_provenance
            BEGIN
                SELECT RAISE(ABORT,
                    'recording_segment_provenance rows are immutable');
            END
            """,
            """
            CREATE TRIGGER recording_provenance_consistency
            BEFORE INSERT ON recording_segment_provenance
            BEGIN
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM recording_import_jobs j
                        WHERE j.job_id = NEW.import_job_id
                          AND j.recording_id = NEW.recording_id
                          AND j.occurrence_id = NEW.occurrence_id
                          AND j.start_offset_ms = NEW.source_offset_ms
                          AND j.duration_ms = NEW.source_duration_ms
                          AND j.parameters_sha256 = NEW.parameters_sha256
                    ) THEN RAISE(ABORT,
                        'provenance must match its import job facts')
                END;
            END
            """,
            """
            CREATE TRIGGER recording_import_jobs_guard_update
            BEFORE UPDATE ON recording_import_jobs
            BEGIN
                SELECT CASE
                    WHEN OLD.state <> 'PREPARED' THEN
                        RAISE(ABORT, 'COMPLETE recording_import_jobs are immutable')
                    WHEN NEW.state <> 'COMPLETE' THEN
                        RAISE(ABORT, 'only PREPARED->COMPLETE is allowed')
                    WHEN NEW.job_id <> OLD.job_id
                        OR NEW.recording_id <> OLD.recording_id
                        OR NEW.source_id <> OLD.source_id
                        OR NEW.session_id <> OLD.session_id
                        OR NEW.sequence_number <> OLD.sequence_number
                        OR NEW.occurrence_id <> OLD.occurrence_id
                        OR NEW.start_offset_ms <> OLD.start_offset_ms
                        OR NEW.duration_ms <> OLD.duration_ms
                        OR NEW.parameters_json <> OLD.parameters_json
                        OR NEW.parameters_sha256 <> OLD.parameters_sha256
                        OR NEW.toolchain_fingerprint_sha256
                           <> OLD.toolchain_fingerprint_sha256
                        OR NEW.created_at <> OLD.created_at
                    THEN RAISE(ABORT,
                        'import job identity and parameters are immutable')
                    WHEN (SELECT state FROM recording_imports
                          WHERE recording_id = NEW.recording_id)
                         IS NOT 'COMPLETE'
                    THEN RAISE(ABORT,
                        'COMPLETE import job requires a COMPLETE recording')
                    WHEN (SELECT source_id FROM recording_imports
                          WHERE recording_id = NEW.recording_id)
                         IS NOT NEW.source_id
                    THEN RAISE(ABORT,
                        'import job source must equal its recording source')
                    WHEN (SELECT state FROM expected_occurrences
                          WHERE occurrence_id = NEW.occurrence_id)
                         IS NOT 'COMPLETE'
                    THEN RAISE(ABORT,
                        'COMPLETE import job requires a COMPLETE expected occurrence')
                    WHEN NOT EXISTS (
                        SELECT 1 FROM expected_occurrences
                        WHERE occurrence_id = NEW.occurrence_id
                          AND source_id = NEW.source_id
                          AND session_id = NEW.session_id
                          AND sequence_number = NEW.sequence_number
                    ) THEN RAISE(ABORT,
                        'expected occurrence identity must equal the import job identity')
                    WHEN NOT EXISTS (
                        SELECT 1 FROM capture_occurrences
                        WHERE occurrence_id = NEW.occurrence_id
                          AND source_id = NEW.source_id
                          AND session_id = NEW.session_id
                          AND sequence_number = NEW.sequence_number
                          AND artifact_id = NEW.output_artifact_id
                    ) THEN RAISE(ABORT,
                        'COMPLETE import job output/identity must equal the capture occurrence')
                    WHEN NOT EXISTS (
                        SELECT 1 FROM recording_segment_provenance
                        WHERE import_job_id = NEW.job_id
                          AND occurrence_id = NEW.occurrence_id
                          AND recording_id = NEW.recording_id
                    ) THEN RAISE(ABORT,
                        'COMPLETE import job requires a matching provenance row')
                END;
            END
            """,
            "PRAGMA foreign_key_check",
        ),
    ),
    Migration(
        version=9,
        name="verified_replication",
        statements=(
            """
            CREATE TABLE replica_targets (
                target_id TEXT PRIMARY KEY
                    CHECK (
                        length(target_id) BETWEEN 1 AND 64
                        AND substr(target_id, 1, 1) GLOB '[A-Za-z0-9]'
                        AND target_id NOT GLOB '*[^A-Za-z0-9._-]*'
                    ),
                adapter_kind TEXT NOT NULL
                    CHECK (adapter_kind = 'mounted_nfs_v4'),
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
            CREATE TABLE replica_objects (
                target_id TEXT NOT NULL
                    REFERENCES replica_targets(target_id),
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
    ),

)

from .replication_target_migration_v10 import MIGRATION_V10
MIGRATIONS = MIGRATIONS + (MIGRATION_V10,)

_CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
)
"""


class MigrationError(Exception):
    """A migration-integrity failure (name drift or database ahead of code)."""


def migrate(database: Path) -> MigrationResult:
    database = Path(database).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    applied: list[int] = []

    known = {migration.version: migration.name for migration in MIGRATIONS}
    code_max = max((migration.version for migration in MIGRATIONS), default=0)

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_CREATE_TRACKING_TABLE)

        recorded = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            )
        }
        # Reject a stored version whose name differs from the code (drift), and a
        # database version newer than the running code (database ahead of code).
        for version, name in recorded.items():
            if version not in known:
                connection.rollback()
                raise MigrationError(
                    f"database version {version} is newer than this code "
                    f"(known max {code_max})"
                )
            if known[version] != name:
                connection.rollback()
                raise MigrationError(
                    f"migration {version} name drift: database {name!r} != code "
                    f"{known[version]!r}"
                )

        existing = set(recorded)
        for migration in MIGRATIONS:
            if migration.version in existing:
                continue
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
            applied.append(migration.version)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return MigrationResult(
        database=database,
        applied_versions=tuple(applied),
        current_version=code_max,
    )
