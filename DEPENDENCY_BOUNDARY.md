# Dependency boundary

Component: `replication-worker`
Canonical repository: `Seemorghdev/edge-evidence-platform`
Canonical commit: `8e6af991ff5139413a5c9e43c66255a321dc4660`
Entry point: `apps.replication_worker.cli:main`

## Runtime canonical closure

- `apps/__init__.py`
- `apps/_cli.py`
- `apps/replication_worker/__init__.py`
- `apps/replication_worker/cli.py`
- `apps/replication_worker/target_config.py`
- `packages/__init__.py`
- `packages/database/__init__.py`
- `packages/database/migrations.py`
- `packages/database/replication_target_migration_v10.py`
- `packages/replication/__init__.py`
- `packages/replication/adapters/__init__.py`
- `packages/replication/adapters/gcs/__init__.py`
- `packages/replication/adapters/gcs/model.py`
- `packages/replication/adapters/gcs/target.py`
- `packages/replication/adapters/nfs/__init__.py`
- `packages/replication/adapters/nfs/filesystem.py`
- `packages/replication/adapters/nfs/inspection.py`
- `packages/replication/adapters/nfs/model.py`
- `packages/replication/adapters/nfs/target.py`
- `packages/replication/contracts/__init__.py`
- `packages/replication/contracts/model.py`
- `packages/replication/contracts/target.py`
- `packages/replication/core/__init__.py`
- `packages/replication/core/authority.py`
- `packages/replication/core/source.py`
- `packages/replication/core/worker.py`
- `packages/replication/worker.py`

## Standalone support canonical files

- `packages/replication/model.py`

## External prerequisites

- None

The standalone repository is a generated projection. Its source boundary is
reviewed in the canonical monorepo; direct source edits here are not authoritative.
