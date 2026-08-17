from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "EXPORT_PROVENANCE.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_digest(files: dict[str, tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        mode, data = files[path]
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(mode.encode())
        digest.update(b"\0")
        digest.update(_sha(data).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _provenance() -> dict[str, object]:
    return json.loads(PROVENANCE.read_text(encoding="utf-8"))


def test_provenance_covers_exact_generated_tree_and_hashes() -> None:
    provenance = _provenance()
    assert provenance["schema_version"] == 1
    assert len(provenance["canonical_commit"]) == 40
    modes: dict[str, str] = {}
    expected = {"EXPORT_PROVENANCE.json"}
    for item in provenance["projected_files"] + provenance["generated_files"]:
        path = item["output_path"]
        expected.add(path)
        modes[path] = item["mode"]

    actual: set[str] = set()
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(
            part in {".git", "__pycache__", ".pytest_cache", "build", "dist"}
            or part.endswith(".egg-info")
            for part in relative.parts
        ):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        actual.add(relative.as_posix())
    assert actual == expected

    for item in provenance["generated_files"]:
        data = (ROOT / item["output_path"]).read_bytes()
        assert _sha(data) == item["sha256"]

    files = {
        path: (modes.get(path, "100644"), (ROOT / path).read_bytes())
        for path in expected
        if path != "EXPORT_PROVENANCE.json"
    }
    assert _tree_digest(files) == provenance["content_tree_sha256"]


def test_projection_closes_runtime_tests_and_demo() -> None:
    provenance = _provenance()
    assert provenance["component"] == "replication-worker"
    assert provenance["canonical_repository"] == "Seemorghdev/edge-evidence-platform"
    assert provenance["entry_point"] == "apps.replication_worker.cli:main"
    assert provenance["public_license"] == "MIT"
    assert provenance["publication_authorized"] is True

    projected = {
        item["output_path"] for item in provenance["projected_files"]
    }
    generated = {
        item["output_path"] for item in provenance["generated_files"]
    }
    for required in (
        "apps/replication_worker/cli.py",
        "packages/database/migrations.py",
        "packages/database/replication_target_migration_v10.py",
        "packages/replication/core/worker.py",
        "packages/replication/adapters/nfs/target.py",
        "packages/replication/adapters/gcs/target.py",
        "packages/replication/model.py",
        "tests/unit/test_replication_gcs.py",
        "tests/unit/test_replication_target_config.py",
        "tests/unit/test_replication_target_contract.py",
    ):
        assert required in projected
    for required in (
        "README.md",
        "demo/run_demo.py",
        "tests/test_export_metadata.py",
        "tests/test_exported_integration.py",
    ):
        assert required in generated
    assert not any(
        path.startswith(("deploy/", "infra/", "acceptance/"))
        for path in projected
    )
    assert not any("azure" in path.lower() for path in projected)


def test_public_package_keeps_gcs_optional_and_exact() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "edge-evidence-replication-worker"
    assert config["project"]["dependencies"] == []
    assert config["project"]["optional-dependencies"]["gcs"] == [
        "google-cloud-storage==3.13.0"
    ]
    assert config["project"]["optional-dependencies"]["dev"] == [
        "pytest>=8,<9"
    ]
    assert config["project"]["scripts"]["replication-worker"] == (
        "apps.replication_worker.cli:main"
    )


def test_dependency_boundary_has_no_private_platform_roots() -> None:
    text = (ROOT / "DEPENDENCY_BOUNDARY.md").read_text(encoding="utf-8")
    forbidden = (
        "`deploy/",
        "`infra/",
        "`acceptance/evidence/",
        "service" + "_account_file",
        "client" + "_secret",
        "private" + "_key",
        "azure",
    )
    for item in forbidden:
        assert item not in text.lower()
