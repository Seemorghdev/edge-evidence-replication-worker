from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from apps.replication_worker.target_config import load_target_config
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
        'token = "must-not-be-accepted"\n',
        encoding="utf-8",
    )
    with pytest.raises(ReplicationError, match="target_config_invalid"):
        load_target_config(config)


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
