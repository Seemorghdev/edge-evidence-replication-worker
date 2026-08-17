"""Standalone local acceptance harness for the exported replication worker."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
from pathlib import Path

from apps.replication_worker import cli as replication_cli
from packages.database.migrations import migrate
from packages.replication import worker as replication_worker
from packages.replication.adapters.nfs import filesystem as nfs_filesystem
from packages.replication.adapters.nfs.model import owned_partial_name
from packages.replication.core import authority as replication_authority

_TARGET_ID = "demo-target"
_STARTED = "2026-08-17T10:00:00Z"
_COMPLETED = "2026-08-17T10:00:10Z"


def _relative_path(root: Path, uri: str) -> Path:
    if not uri.startswith("file:"):
        raise SystemExit("replication demo URI was not a relative file URI")
    relative = Path(uri[5:])
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise SystemExit("replication demo URI was not canonical")
    return root.joinpath(*relative.parts)


def _seed_recording(
    database: Path,
    spool: Path,
    *,
    source_id: str,
    data: bytes,
) -> tuple[str, bytes]:
    digest = hashlib.sha256(data).hexdigest()
    recording_id = f"sha256:{digest}"
    storage_uri = f"file:recordings/sha256/{digest[:2]}/{digest}.source"
    source = _relative_path(spool, storage_uri)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)

    descriptor = json.dumps(
        {"clock_domain": "unknown", "source_kind": "synthetic_local"},
        sort_keys=True,
        separators=(",", ":"),
    )
    descriptor_sha256 = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO recording_imports ("
            "recording_id, source_id, source_descriptor_json, "
            "source_descriptor_sha256, byte_size, digest_algorithm, digest_value, "
            "storage_uri, recording_started_at, time_authority, state"
            ") VALUES (?, ?, ?, ?, ?, 'sha256', ?, ?, ?, "
            "'operator_asserted_utc', 'PREPARED')",
            (
                recording_id,
                source_id,
                descriptor,
                descriptor_sha256,
                len(data),
                digest,
                storage_uri,
                _STARTED,
            ),
        )
        connection.execute(
            "UPDATE recording_imports SET "
            "state='COMPLETE', container_class='iso_bmff_mp4', major_brand='isom', "
            "video_stream_index=0, video_codec='h264', pixel_format='yuv420p', "
            "coded_width=1280, coded_height=720, duration_ms=10000, "
            "display_rotation_ccw_degrees=0, completed_at=? "
            "WHERE recording_id=?",
            (_COMPLETED, recording_id),
        )
        connection.commit()
    return storage_uri, data


def _invoke(arguments: list[str]) -> tuple[int, dict[str, object]]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = replication_cli.main(arguments)

    output = stderr.getvalue() if code else stdout.getvalue()
    other = stdout.getvalue() if code else stderr.getvalue()
    if other.strip():
        raise SystemExit("replication-worker emitted output on the unexpected stream")
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise SystemExit("replication-worker did not emit exactly one JSON object")
    payload = json.loads(lines[0])
    if not isinstance(payload, dict):
        raise SystemExit("replication-worker output was not a JSON object")
    return code, payload


def _run_success_case(root: Path) -> dict[str, object]:
    database = root / "authority.sqlite3"
    spool = root / "spool"
    target = root / "target"
    spool.mkdir()
    target.mkdir()
    migration = migrate(database)
    if migration.current_version != 10:
        raise SystemExit("replication export did not create schema-v10 authority")

    adopted_uri, adopted_bytes = _seed_recording(
        database,
        spool,
        source_id="demo-adopt",
        data=b"already-present-replica\n",
    )
    published_uri, published_bytes = _seed_recording(
        database,
        spool,
        source_id="demo-publish",
        data=b"new-immutable-replica\n",
    )

    replication_worker.init_target(database, target, _TARGET_ID)
    objects = replication_authority.discover(database, spool, _TARGET_ID)
    with replication_authority.connect(database) as connection:
        inserted = replication_authority.register_discovered(
            connection,
            _TARGET_ID,
            objects,
        )
    if inserted != 2:
        raise SystemExit("replication demo did not register exactly two objects")

    by_uri = {item.relative_uri: item for item in objects}
    adopted_object = by_uri[adopted_uri]
    published_object = by_uri[published_uri]

    adopted_final = nfs_filesystem.ensure_destination_parent(target, adopted_uri)
    adopted_final.write_bytes(adopted_bytes)
    published_final = nfs_filesystem.ensure_destination_parent(target, published_uri)
    partial = published_final.parent / owned_partial_name(published_object)
    partial.write_bytes(b"owned-transient-partial")

    code, first = _invoke(
        [
            "run",
            "--database",
            str(database),
            "--spool-root",
            str(spool),
            "--target-root",
            str(target),
            "--target-id",
            _TARGET_ID,
        ]
    )
    expected_first = {
        "status": "pass",
        "discovered": 2,
        "inserted": 0,
        "published": 1,
        "adopted": 1,
        "verified": 2,
        "partials_cleaned": 1,
    }
    if code != 0 or first != expected_first:
        raise SystemExit(f"unexpected first replication summary: {first}")

    verify_code, verification = _invoke(
        [
            "verify",
            "--database",
            str(database),
            "--target-root",
            str(target),
            "--target-id",
            _TARGET_ID,
        ]
    )
    if verify_code != 0 or verification != {
        "status": "pass",
        "discovered": 0,
        "inserted": 0,
        "published": 0,
        "adopted": 0,
        "verified": 2,
        "partials_cleaned": 0,
    }:
        raise SystemExit(f"unexpected replication verification summary: {verification}")

    second_code, second = _invoke(
        [
            "run",
            "--database",
            str(database),
            "--spool-root",
            str(spool),
            "--target-root",
            str(target),
            "--target-id",
            _TARGET_ID,
        ]
    )
    expected_second = {
        "status": "pass",
        "discovered": 2,
        "inserted": 0,
        "published": 0,
        "adopted": 0,
        "verified": 0,
        "partials_cleaned": 0,
    }
    if second_code != 0 or second != expected_second:
        raise SystemExit(f"unexpected exact-rerun summary: {second}")

    with sqlite3.connect(database) as connection:
        pending, verified = connection.execute(
            "SELECT "
            "sum(CASE WHEN state='PENDING' THEN 1 ELSE 0 END), "
            "sum(CASE WHEN state='VERIFIED' THEN 1 ELSE 0 END) "
            "FROM replica_objects WHERE target_id=?",
            (_TARGET_ID,),
        ).fetchone()

    adopted_digest_ok = hashlib.sha256(adopted_final.read_bytes()).hexdigest() == (
        adopted_object.expected_sha256
    )
    published_digest_ok = hashlib.sha256(published_final.read_bytes()).hexdigest() == (
        published_object.expected_sha256
    )

    return {
        "database_schema_version": migration.current_version,
        "registered": inserted,
        "first_run": first,
        "verification": verification,
        "second_run": second,
        "verified_objects": int(verified or 0),
        "pending_objects": int(pending or 0),
        "adopted_digest_verified": adopted_digest_ok,
        "published_digest_verified": published_digest_ok,
        "partial_removed": not partial.exists(),
    }


def _run_collision_case(root: Path) -> dict[str, object]:
    database = root / "collision.sqlite3"
    spool = root / "collision-spool"
    target = root / "collision-target"
    spool.mkdir()
    target.mkdir()
    migrate(database)
    uri, _expected = _seed_recording(
        database,
        spool,
        source_id="demo-collision",
        data=b"expected-replica-bytes\n",
    )
    replication_worker.init_target(database, target, _TARGET_ID)
    final = nfs_filesystem.ensure_destination_parent(target, uri)
    wrong = b"pre-existing-wrong-bytes\n"
    final.write_bytes(wrong)

    code, payload = _invoke(
        [
            "run",
            "--database",
            str(database),
            "--spool-root",
            str(spool),
            "--target-root",
            str(target),
            "--target-id",
            _TARGET_ID,
        ]
    )
    if code != 4 or payload != {
        "status": "error",
        "finding": "destination_collision",
    }:
        raise SystemExit(f"collision did not fail closed: {code}, {payload}")
    return {
        "exit_code": code,
        "finding": payload["finding"],
        "wrong_bytes_preserved": final.read_bytes() == wrong,
    }


def run_demo() -> dict[str, object]:
    original_mount_probe = nfs_filesystem.mounted_fstype
    nfs_filesystem.mounted_fstype = lambda _root: "nfs4"
    try:
        with tempfile.TemporaryDirectory(prefix="replication-worker-demo-") as temp:
            root = Path(temp)
            success = _run_success_case(root)
            collision = _run_collision_case(root)
    finally:
        nfs_filesystem.mounted_fstype = original_mount_probe

    invariants = {
        "immutable_create_verified": success["published_digest_verified"] is True,
        "exact_adoption_verified": success["adopted_digest_verified"] is True,
        "transient_cleanup_verified": success["partial_removed"] is True,
        "independent_readback_verified": success["verification"]["verified"] == 2,
        "exact_rerun_is_noop": (
            success["second_run"]["published"] == 0
            and success["second_run"]["adopted"] == 0
            and success["second_run"]["inserted"] == 0
        ),
        "collision_fails_closed": (
            collision["exit_code"] == 4
            and collision["finding"] == "destination_collision"
            and collision["wrong_bytes_preserved"] is True
        ),
        "zero_unresolved_work": success["pending_objects"] == 0,
        "temporary_local_state_only": True,
        "synthetic_nfs_contract_only": True,
    }
    if not all(invariants.values()):
        raise SystemExit(f"replication demo invariant failure: {invariants}")

    return {
        "schema_version": 1,
        "proof_class": "standalone_synthetic_replication",
        "status": "pass",
        "authority": {
            "database_schema_version": success["database_schema_version"],
            "target_id": _TARGET_ID,
        },
        "first_run": success["first_run"],
        "verification": success["verification"],
        "second_run": success["second_run"],
        "collision": collision,
        "final_state": {
            "verified_objects": success["verified_objects"],
            "pending_objects": success["pending_objects"],
        },
        "invariants": invariants,
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), sort_keys=True, separators=(",", ":")))
