from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_export_provenance_is_publication_authorized_and_canonical() -> None:
    provenance = json.loads(
        (ROOT / "EXPORT_PROVENANCE.json").read_text(encoding="utf-8")
    )
    assert provenance["schema_version"] == 1
    assert provenance["component"] == "replication-worker"
    assert provenance["canonical_repository"] == "Seemorghdev/edge-evidence-platform"
    assert provenance["entry_point"] == "apps.replication_worker.cli:main"
    assert provenance["public_license"] == "MIT"
    assert provenance["publication_authorized"] is True
    assert len(provenance["canonical_commit"]) == 40

    projected = {
        item["output_path"] for item in provenance["projected_files"]
    }
    assert "packages/replication/model.py" in projected
    assert not any(path.startswith(("deploy/", "infra/", "acceptance/")) for path in projected)
    assert not any("azure" in path.lower() for path in projected)


def test_public_package_keeps_gcs_optional() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "edge-evidence-replication-worker"
    assert config["project"]["dependencies"] == []
    assert config["project"]["optional-dependencies"]["gcs"] == [
        "google-cloud-storage==3.13.0"
    ]
    assert config["project"]["scripts"]["replication-worker"] == (
        "apps.replication_worker.cli:main"
    )


def test_dependency_boundary_has_no_private_platform_roots() -> None:
    text = (ROOT / "DEPENDENCY_BOUNDARY.md").read_text(encoding="utf-8")
    for forbidden in ("`deploy/", "`infra/", "`acceptance/evidence/", "azure"):
        assert forbidden not in text.lower()
