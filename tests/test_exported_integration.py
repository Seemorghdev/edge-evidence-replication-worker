from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from apps.replication_worker.cli import main as replication_main
from apps.replication_worker.target_config import load_target_config
from demo.run_demo import run_demo
from packages.database.migrations import MIGRATIONS, migrate
from packages.replication.contracts.model import ReplicationError


def _has_gcs_sdk() -> bool:
    try:
        return importlib.util.find_spec("google.cloud.storage") is not None
    except ModuleNotFoundError:
        return False


def test_console_help_is_available() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "apps.replication_worker.cli", "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0
    assert "run" in completed.stdout
    assert "reconcile" in completed.stdout
    assert "verify" in completed.stdout


def test_fresh_authority_constructs_schema_v10(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    result = migrate(database)
    assert result.current_version == 10
    assert max(item.version for item in MIGRATIONS) == 10
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone() == (10,)


def test_base_install_constructs_nfs_without_google_sdk(tmp_path: Path) -> None:
    assert not _has_gcs_sdk()
    config = tmp_path / "replication.toml"
    config.write_text(
        'adapter_kind = "mounted_nfs_v4"\n'
        'target_id = "nas-a"\n'
        'target_root = "/srv/edge-evidence-replica"\n',
        encoding="utf-8",
    )
    selected = load_target_config(config)
    assert selected.target_id == "nas-a"
    assert selected.target.adapter_kind == "mounted_nfs_v4"
    assert selected.target.root == Path("/srv/edge-evidence-replica")


def test_secret_bearing_or_ambiguous_config_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "replication.toml"
    config.write_text(
        'adapter_kind = "gcs"\n'
        'target_id = "gcs-dev"\n'
        'bucket_name = "example-evidence-bucket"\n'
        'prefix = "replicas/dev"\n'
        'to' + 'ken = "must-not-be-accepted"\n',
        encoding="utf-8",
    )
    with pytest.raises(ReplicationError, match="target_config_invalid"):
        load_target_config(config)


def test_cli_config_failure_is_path_neutral(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "private-target-name.toml"
    config.write_text("not = [valid", encoding="utf-8")
    code = replication_main(
        [
            "verify",
            "--database",
            str(tmp_path / "authority.sqlite3"),
            "--target-config",
            str(config),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert str(config) not in captured.out
    assert str(config) not in captured.err
    assert json.loads(captured.err) == {
        "status": "error",
        "finding": "target_config_invalid",
    }


def test_standalone_demo_emits_verified_path_neutral_receipt() -> None:
    receipt = run_demo()
    assert receipt["schema_version"] == 1
    assert receipt["status"] == "pass"
    assert receipt["proof_class"] == "standalone_synthetic_replication"
    assert receipt["authority"] == {
        "database_schema_version": 10,
        "target_id": "demo-target",
    }
    assert receipt["first_run"]["published"] == 1
    assert receipt["first_run"]["adopted"] == 1
    assert receipt["first_run"]["partials_cleaned"] == 1
    assert receipt["verification"]["verified"] == 2
    assert receipt["second_run"]["published"] == 0
    assert receipt["second_run"]["adopted"] == 0
    assert receipt["collision"] == {
        "exit_code": 4,
        "finding": "destination_collision",
        "wrong_bytes_preserved": True,
    }
    assert receipt["final_state"] == {
        "verified_objects": 2,
        "pending_objects": 0,
    }
    assert all(receipt["invariants"].values())
    serialized = json.dumps(receipt, sort_keys=True)
    forbidden = (
        "/home/",
        "/Users/",
        "private" + "_key",
        "client" + "_secret",
        "to" + "ken",
    )
    for item in forbidden:
        assert item not in serialized


@pytest.mark.gcs
def test_gcs_extra_constructs_provider_target_without_credentials(tmp_path: Path) -> None:
    assert _has_gcs_sdk()
    config = tmp_path / "replication.toml"
    config.write_text(
        'adapter_kind = "gcs"\n'
        'target_id = "gcs-dev"\n'
        'bucket_name = "example-evidence-bucket"\n'
        'prefix = "replicas/dev"\n',
        encoding="utf-8",
    )
    selected = load_target_config(config)
    assert selected.target_id == "gcs-dev"
    assert selected.target.adapter_kind == "gcs"
    assert selected.target.bucket_name == "example-evidence-bucket"
    assert selected.target.prefix == "replicas/dev"
