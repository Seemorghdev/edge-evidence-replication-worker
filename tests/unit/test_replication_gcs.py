"""SPEC-023 GCS adapter contract tests without real provider credentials."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from google.api_core import exceptions as gexc

from packages.replication.adapters.gcs.model import marker_name, object_name
from packages.replication.adapters.gcs.target import GcsTarget
from packages.replication.contracts.model import ReplicaObject, ReplicationError


@dataclass
class _Stored:
    data: bytes
    generation: int


class _FakeBlob:
    def __init__(self, bucket: "_FakeBucket", name: str) -> None:
        self.bucket = bucket
        self.name = name
        self.generation: int | None = None
        self.size: int | None = None

    def _stored(self) -> _Stored:
        if self.name not in self.bucket.objects:
            raise gexc.NotFound("missing")
        return self.bucket.objects[self.name]

    def reload(self, if_generation_match: int | None = None) -> None:
        stored = self._stored()
        if if_generation_match is not None and stored.generation != if_generation_match:
            raise gexc.PreconditionFailed("generation changed")
        self.generation = stored.generation
        self.size = len(stored.data)

    def upload_from_string(self, data: bytes, **kwargs) -> None:
        self._create(bytes(data), kwargs.get("if_generation_match"))

    def upload_from_file(self, handle, **kwargs) -> None:
        if kwargs.get("rewind"):
            handle.seek(0)
        data = handle.read()
        expected_size = kwargs.get("size")
        if expected_size is not None and len(data) != expected_size:
            raise AssertionError("fake upload size mismatch")
        self._create(data, kwargs.get("if_generation_match"))

    def _create(self, data: bytes, if_generation_match: int | None) -> None:
        if if_generation_match == 0 and self.name in self.bucket.objects:
            raise gexc.PreconditionFailed("already exists")
        generation = self.bucket.next_generation
        self.bucket.next_generation += 1
        self.bucket.objects[self.name] = _Stored(data=data, generation=generation)
        self.generation = generation
        self.size = len(data)

    def download_to_file(self, handle, **kwargs) -> None:
        stored = self._stored()
        expected = kwargs.get("if_generation_match")
        if expected is not None and stored.generation != expected:
            raise gexc.PreconditionFailed("generation changed")
        handle.write(stored.data)


class _FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, _Stored] = {}
        self.next_generation = 1
        self.available = True

    def reload(self) -> None:
        if not self.available:
            raise gexc.NotFound("bucket missing")

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)


class _FakeClient:
    def __init__(self, bucket: _FakeBucket) -> None:
        self._bucket = bucket

    def bucket(self, name: str) -> _FakeBucket:
        assert name == "spec023-test-bucket"
        return self._bucket


def _object(data: bytes = b"replica-bytes") -> ReplicaObject:
    return ReplicaObject(
        target_id="gcs-a",
        object_kind="artifact_content",
        relative_uri="file:complete/aa/object.bin",
        expected_byte_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )


def _target(bucket: _FakeBucket, *, prefix: str = "replicas/dev") -> GcsTarget:
    return GcsTarget(
        bucket_name="spec023-test-bucket",
        prefix=prefix,
        client=_FakeClient(bucket),
    )


def test_gcs_marker_init_bind_and_exact_retry() -> None:
    bucket = _FakeBucket()
    target = _target(bucket)
    digest = target.initialize("gcs-a")
    assert target.initialize("gcs-a") == digest
    bound = target.bind("gcs-a", digest)
    bound.validate_identity()
    assert marker_name("replicas/dev") in bucket.objects


def test_gcs_publish_is_create_only_then_adopts_exact(tmp_path: Path) -> None:
    bucket = _FakeBucket()
    target = _target(bucket)
    digest = target.initialize("gcs-a")
    bound = target.bind("gcs-a", digest)
    obj = _object()
    source = tmp_path / "complete" / "aa" / "object.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"replica-bytes")

    assert bound.publish_immutable(tmp_path, obj) == "published"
    assert bound.inspect(obj) == "exact"
    bound.verify(obj)
    first = bucket.objects[object_name("replicas/dev", obj)]

    assert bound.publish_immutable(tmp_path, obj) == "adopted"
    second = bucket.objects[object_name("replicas/dev", obj)]
    assert second == first


def test_gcs_collision_never_overwrites(tmp_path: Path) -> None:
    bucket = _FakeBucket()
    target = _target(bucket)
    digest = target.initialize("gcs-a")
    bound = target.bind("gcs-a", digest)
    obj = _object()
    name = object_name("replicas/dev", obj)
    bucket.objects[name] = _Stored(data=b"wrong", generation=50)
    source = tmp_path / "complete" / "aa" / "object.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"replica-bytes")

    with pytest.raises(ReplicationError) as error:
        bound.publish_immutable(tmp_path, obj)
    assert (error.value.code, error.value.finding) == (4, "destination_collision")
    assert bucket.objects[name].data == b"wrong"


def test_gcs_marker_replacement_invalidates_bound_identity() -> None:
    bucket = _FakeBucket()
    target = _target(bucket)
    digest = target.initialize("gcs-a")
    bound = target.bind("gcs-a", digest)
    name = marker_name("replicas/dev")
    original = bucket.objects[name]
    bucket.objects[name] = _Stored(data=original.data, generation=original.generation + 100)

    with pytest.raises(ReplicationError) as error:
        bound.validate_identity()
    assert (error.value.code, error.value.finding) == (4, "target_identity_mismatch")


def test_gcs_bucket_unavailable_is_path_neutral() -> None:
    bucket = _FakeBucket()
    target = _target(bucket)
    digest = target.initialize("gcs-a")
    bound = target.bind("gcs-a", digest)
    bucket.available = False

    with pytest.raises(ReplicationError) as error:
        bound.validate_identity()
    assert (error.value.code, error.value.finding) == (5, "target_unavailable")
    assert "spec023-test-bucket" not in str(error.value)


@pytest.mark.parametrize("prefix", ("/absolute", "trailing/", "a//b", "a/../b", "a/./b"))
def test_gcs_prefix_must_be_canonical(prefix: str) -> None:
    with pytest.raises(ReplicationError) as error:
        GcsTarget(bucket_name="spec023-test-bucket", prefix=prefix, client=_FakeClient(_FakeBucket()))
    assert (error.value.code, error.value.finding) == (2, "target_config_invalid")


def test_gcs_adapter_source_contains_no_credential_material() -> None:
    text = Path("packages/replication/adapters/gcs/target.py").read_text(encoding="utf-8").lower()
    for forbidden in (
        "google_application_credentials",
        "service_account_file",
        "service_account_info",
        "client_secret",
        "private_key",
    ):
        assert forbidden not in text
    assert "storage.client()" in text
