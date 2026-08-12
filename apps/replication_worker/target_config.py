"""Non-secret target composition for the replication-worker application boundary."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.replication.adapters.nfs.target import NfsTarget
from packages.replication.contracts.model import ReplicationError, validate_target_id


_NFS_KEYS = frozenset({"adapter_kind", "target_id", "target_root"})
_GCS_KEYS = frozenset({"adapter_kind", "target_id", "bucket_name", "prefix"})


@dataclass(frozen=True)
class TargetSelection:
    target_id: str
    target: Any


def _invalid() -> ReplicationError:
    return ReplicationError("target_config_invalid", code=2)


def _read(path: Path) -> dict[str, object]:
    try:
        with Path(path).open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _invalid() from exc
    if not isinstance(raw, dict):
        raise _invalid()
    return raw


def _require_exact_strings(
    raw: dict[str, object],
    expected: frozenset[str],
) -> dict[str, str]:
    if set(raw) != expected:
        raise _invalid()
    if any(type(raw[key]) is not str for key in expected):
        raise _invalid()
    return {key: str(raw[key]) for key in expected}


def _gcs_target(bucket_name: str, prefix: str) -> Any:
    try:
        from packages.replication.adapters.gcs.target import GcsTarget
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "google" or missing.startswith("google."):
            raise ReplicationError("target_adapter_unavailable", code=2) from exc
        raise
    return GcsTarget(bucket_name, prefix)


def load_target_config(path: Path) -> TargetSelection:
    raw = _read(path)
    adapter_kind = raw.get("adapter_kind")
    if adapter_kind == "mounted_nfs_v4":
        config = _require_exact_strings(raw, _NFS_KEYS)
        target_id = validate_target_id(config["target_id"])
        if not config["target_root"]:
            raise _invalid()
        return TargetSelection(
            target_id=target_id,
            target=NfsTarget(Path(config["target_root"])),
        )

    if adapter_kind == "gcs":
        config = _require_exact_strings(raw, _GCS_KEYS)
        target_id = validate_target_id(config["target_id"])
        if not config["bucket_name"]:
            raise _invalid()
        return TargetSelection(
            target_id=target_id,
            target=_gcs_target(config["bucket_name"], config["prefix"]),
        )

    raise _invalid()
