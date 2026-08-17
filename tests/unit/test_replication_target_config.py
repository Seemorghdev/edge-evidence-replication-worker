"""SPEC-024 non-secret replication target-composition contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from apps.replication_worker.cli import main as worker_main
from apps.replication_worker.target_config import load_target_config
from packages.replication.contracts.model import ReplicationError

ROOT = Path(__file__).resolve().parents[2]


def test_nfs_config_constructs_exact_target(tmp_path: Path) -> None:
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


def test_canonical_dev_gcs_config_constructs_exact_provider_target() -> None:
    selected = load_target_config(
        ROOT / "deploy/environments/dev/replication.toml"
    )
    assert selected.target_id == "gcs-dev"
    assert selected.target.adapter_kind == "gcs"
    assert selected.target.bucket_name == "seemorgh-edge-4df3bb02-replicas-dev"
    assert selected.target.prefix == "replicas/dev"


@pytest.mark.parametrize(
    "text",
    [
        'adapter_kind = "gcs"\ntarget_id = "gcs-dev"\nbucket_name = "b"\n',
        'adapter_kind = "gcs"\ntarget_id = "gcs-dev"\nbucket_name = "b"\nprefix = ""\ntoken = "x"\n',
        'adapter_kind = "mounted_nfs_v4"\ntarget_id = "nas-a"\ntarget_root = "/srv/x"\ncredential = "x"\n',
        'adapter_kind = "azure"\ntarget_id = "x"\n',
        'adapter_kind = "gcs"\ntarget_id = 7\nbucket_name = "b"\nprefix = ""\n',
        '[target]\nadapter_kind = "gcs"\ntarget_id = "gcs-dev"\nbucket_name = "b"\nprefix = ""\n',
    ],
)
def test_unknown_missing_nested_or_secret_bearing_config_fails_closed(
    tmp_path: Path,
    text: str,
) -> None:
    config = tmp_path / "replication.toml"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ReplicationError) as exc:
        load_target_config(config)
    assert exc.value.finding == "target_config_invalid"
    assert exc.value.code == 2


def test_malformed_config_cli_error_is_path_neutral(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "private-config-name.toml"
    config.write_text("not = [valid", encoding="utf-8")
    code = worker_main([
        "verify",
        "--database",
        str(tmp_path / "db.sqlite3"),
        "--target-config",
        str(config),
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert str(config) not in captured.out
    assert str(config) not in captured.err
    assert json.loads(captured.err) == {
        "finding": "target_config_invalid",
        "status": "error",
    }


def test_base_import_and_nfs_config_do_not_load_google_sdk(tmp_path: Path) -> None:
    config = tmp_path / "replication.toml"
    config.write_text(
        'adapter_kind = "mounted_nfs_v4"\n'
        'target_id = "nas-a"\n'
        'target_root = "/srv/edge-evidence-replica"\n',
        encoding="utf-8",
    )
    script = r"""
import importlib.abc
import sys
from pathlib import Path

class BlockGoogle(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "google" or fullname.startswith("google."):
            raise ModuleNotFoundError("blocked optional Google SDK", name=fullname)
        return None

sys.meta_path.insert(0, BlockGoogle())
from apps.replication_worker.target_config import load_target_config
selected = load_target_config(Path(sys.argv[1]))
assert selected.target_id == "nas-a"
assert selected.target.adapter_kind == "mounted_nfs_v4"
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(config)],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_gcs_config_reports_optional_sdk_absence_before_provider_io(
    tmp_path: Path,
) -> None:
    config = tmp_path / "replication.toml"
    config.write_text(
        'adapter_kind = "gcs"\n'
        'target_id = "gcs-dev"\n'
        'bucket_name = "example-evidence-bucket"\n'
        'prefix = "replicas/dev"\n',
        encoding="utf-8",
    )
    script = r"""
import importlib.abc
import sys
from pathlib import Path

class BlockGoogle(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "google" or fullname.startswith("google."):
            raise ModuleNotFoundError("blocked optional Google SDK", name=fullname)
        return None

sys.meta_path.insert(0, BlockGoogle())
from apps.replication_worker.target_config import load_target_config
from packages.replication.contracts.model import ReplicationError

try:
    load_target_config(Path(sys.argv[1]))
except ReplicationError as exc:
    assert exc.finding == "target_adapter_unavailable"
    assert exc.code == 2
else:
    raise AssertionError("GCS config unexpectedly loaded without its optional SDK")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(config)],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
