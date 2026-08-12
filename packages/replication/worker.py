"""Compatibility facade for the proven mounted-NFSv4 replication worker.

SPEC-023 moves convergence into ``packages.replication.core.worker``. These legacy
function signatures remain stable for the existing CLI/tests/systemd surface and
construct only the proven NFS adapter. Provider-specific convergence does not live
here.
"""

from __future__ import annotations

from pathlib import Path

from .adapters.nfs.target import NfsTarget
from .core.worker import RunSummary
from .core.worker import init_target as _init_target
from .core.worker import reconcile as _reconcile
from .core.worker import run as _run
from .core.worker import verify as _verify


def init_target(database: Path, target_root: Path, target_id: str) -> RunSummary:
    return _init_target(database, NfsTarget(target_root), target_id)


def reconcile(database: Path, spool_root: Path, target_root: Path, target_id: str) -> RunSummary:
    return _reconcile(database, spool_root, NfsTarget(target_root), target_id)


def run(database: Path, spool_root: Path, target_root: Path, target_id: str) -> RunSummary:
    return _run(database, spool_root, NfsTarget(target_root), target_id)


def verify(database: Path, target_root: Path, target_id: str) -> RunSummary:
    return _verify(database, NfsTarget(target_root), target_id)
