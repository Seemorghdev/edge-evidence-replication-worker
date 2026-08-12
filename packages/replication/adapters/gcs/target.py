"""Google Cloud Storage replication target adapter for SPEC-023."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import storage
from google.cloud.storage import exceptions as storage_exceptions

from packages.replication.contracts.model import ReplicaObject, ReplicationError
from packages.replication.contracts.target import DestinationState, PublishResult
from packages.replication.core.source import source_path

from .model import (
    ADAPTER_KIND,
    canonical_marker_bytes,
    marker_name,
    marker_sha256 as expected_marker_sha256,
    normalize_prefix,
    object_name,
)

_CHUNK = 1024 * 1024


def _target_unavailable(exc: Exception) -> ReplicationError:
    return ReplicationError("target_unavailable", code=5)


def _read_error(exc: Exception, *, missing: str, precondition: str) -> ReplicationError:
    if isinstance(exc, gexc.NotFound):
        return ReplicationError(missing, code=6 if missing.startswith("destination_") else 4)
    if isinstance(exc, gexc.PreconditionFailed):
        identity_or_collision = precondition in {
            "destination_collision",
            "target_identity_mismatch",
        }
        return ReplicationError(precondition, code=4 if identity_or_collision else 6)
    if isinstance(
        exc,
        (
            gexc.Forbidden,
            gexc.Unauthorized,
            gexc.TooManyRequests,
            gexc.ServiceUnavailable,
            gexc.GatewayTimeout,
            gexc.DeadlineExceeded,
        ),
    ):
        return _target_unavailable(exc)
    if isinstance(exc, (storage_exceptions.DataCorruption, storage_exceptions.InvalidResponse)):
        return ReplicationError("target_io_error", code=5)
    if isinstance(exc, gexc.GoogleAPICallError):
        return ReplicationError("target_io_error", code=5)
    raise exc


def _write_error(exc: Exception) -> ReplicationError:
    if isinstance(exc, (gexc.Forbidden, gexc.Unauthorized)):
        return ReplicationError("target_read_only", code=5)
    if isinstance(
        exc,
        (
            gexc.NotFound,
            gexc.TooManyRequests,
            gexc.ServiceUnavailable,
            gexc.GatewayTimeout,
            gexc.DeadlineExceeded,
        ),
    ):
        return _target_unavailable(exc)
    if isinstance(exc, (storage_exceptions.DataCorruption, storage_exceptions.InvalidResponse)):
        return ReplicationError("target_io_error", code=5)
    if isinstance(exc, gexc.GoogleAPICallError):
        return ReplicationError("target_io_error", code=5)
    raise exc


def _require_bucket(bucket: Any) -> None:
    try:
        bucket.reload()
    except Exception as exc:
        raise _target_unavailable(exc) from exc


def _download_hash(blob: Any, *, generation: int, missing: str, changed: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    try:
        with tempfile.SpooledTemporaryFile(max_size=8 * _CHUNK, mode="w+b") as handle:
            blob.download_to_file(
                handle,
                if_generation_match=generation,
                checksum="auto",
            )
            handle.seek(0)
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
    except Exception as exc:
        raise _read_error(exc, missing=missing, precondition=changed) from exc
    return total, digest.hexdigest()


@dataclass(frozen=True)
class _BoundGcsTarget:
    bucket: Any
    prefix: str
    target_id: str
    marker_sha256: str
    marker_generation: int
    adapter_kind: str = ADAPTER_KIND

    def _marker_blob(self) -> Any:
        return self.bucket.blob(marker_name(self.prefix))

    def validate_identity(self) -> None:
        _require_bucket(self.bucket)
        blob = self._marker_blob()
        try:
            blob.reload(if_generation_match=self.marker_generation)
        except gexc.NotFound as exc:
            raise ReplicationError("target_identity_mismatch", code=4) from exc
        except gexc.PreconditionFailed as exc:
            raise ReplicationError("target_identity_mismatch", code=4) from exc
        except Exception as exc:
            raise _read_error(
                exc,
                missing="target_identity_mismatch",
                precondition="target_identity_mismatch",
            ) from exc

    def cleanup_transient(self, obj: ReplicaObject) -> bool:
        del obj
        return False

    def _blob(self, obj: ReplicaObject) -> Any:
        return self.bucket.blob(object_name(self.prefix, obj))

    def inspect(self, obj: ReplicaObject) -> DestinationState:
        blob = self._blob(obj)
        try:
            blob.reload()
        except gexc.NotFound:
            return "absent"
        except Exception as exc:
            raise _read_error(
                exc,
                missing="destination_missing",
                precondition="destination_collision",
            ) from exc
        generation = int(blob.generation)
        if int(blob.size) != obj.expected_byte_size:
            raise ReplicationError("destination_collision", code=4)
        size, digest = _download_hash(
            blob,
            generation=generation,
            missing="destination_missing",
            changed="destination_collision",
        )
        if size == obj.expected_byte_size and digest == obj.expected_sha256:
            return "exact"
        raise ReplicationError("destination_collision", code=4)

    def publish_immutable(self, spool_root: Path, obj: ReplicaObject) -> PublishResult:
        source = source_path(spool_root, obj.relative_uri)
        blob = self._blob(obj)
        try:
            with source.open("rb") as handle:
                blob.upload_from_file(
                    handle,
                    rewind=True,
                    size=obj.expected_byte_size,
                    content_type="application/octet-stream",
                    if_generation_match=0,
                    checksum="auto",
                )
        except gexc.PreconditionFailed:
            if self.inspect(obj) == "exact":
                return "adopted"
            raise ReplicationError("destination_collision", code=4)
        except Exception as exc:
            raise _write_error(exc) from exc
        self.verify(obj)
        return "published"

    def verify(self, obj: ReplicaObject) -> None:
        blob = self._blob(obj)
        try:
            blob.reload()
        except gexc.NotFound as exc:
            raise ReplicationError("destination_missing", code=6) from exc
        except Exception as exc:
            raise _read_error(
                exc,
                missing="destination_missing",
                precondition="destination_digest_mismatch",
            ) from exc
        generation = int(blob.generation)
        if int(blob.size) != obj.expected_byte_size:
            raise ReplicationError("destination_size_mismatch", code=6)
        size, digest = _download_hash(
            blob,
            generation=generation,
            missing="destination_missing",
            changed="destination_digest_mismatch",
        )
        if size != obj.expected_byte_size:
            raise ReplicationError("destination_size_mismatch", code=6)
        if digest != obj.expected_sha256:
            raise ReplicationError("destination_digest_mismatch", code=6)


@dataclass(frozen=True)
class GcsTarget:
    """GCS target using external ADC when no client is injected."""

    bucket_name: str
    prefix: str = ""
    client: Any | None = field(default=None, compare=False, repr=False)
    adapter_kind: str = ADAPTER_KIND

    def __post_init__(self) -> None:
        if not isinstance(self.bucket_name, str) or not self.bucket_name:
            raise ReplicationError("target_config_invalid", code=2)
        object.__setattr__(self, "prefix", normalize_prefix(self.prefix))

    def _client(self) -> Any:
        return self.client if self.client is not None else storage.Client()

    def _bucket(self) -> Any:
        return self._client().bucket(self.bucket_name)

    def initialize(self, target_id: str) -> str:
        bucket = self._bucket()
        _require_bucket(bucket)
        canonical = canonical_marker_bytes(target_id)
        expected = expected_marker_sha256(target_id)
        blob = bucket.blob(marker_name(self.prefix))
        try:
            blob.upload_from_string(
                canonical,
                content_type="application/json",
                if_generation_match=0,
                checksum="auto",
            )
        except gexc.PreconditionFailed:
            pass
        except Exception as exc:
            raise _write_error(exc) from exc

        try:
            blob.reload()
            generation = int(blob.generation)
        except gexc.NotFound as exc:
            raise ReplicationError("target_identity_mismatch", code=4) from exc
        except Exception as exc:
            raise _read_error(
                exc,
                missing="target_identity_mismatch",
                precondition="target_identity_mismatch",
            ) from exc
        size, digest = _download_hash(
            blob,
            generation=generation,
            missing="target_identity_mismatch",
            changed="target_identity_mismatch",
        )
        if size != len(canonical) or digest != expected:
            raise ReplicationError("target_identity_mismatch", code=4)
        return expected

    def bind(self, target_id: str, marker_sha256: str) -> _BoundGcsTarget:
        bucket = self._bucket()
        _require_bucket(bucket)
        if marker_sha256 != expected_marker_sha256(target_id):
            raise ReplicationError("target_identity_mismatch", code=4)
        blob = bucket.blob(marker_name(self.prefix))
        try:
            blob.reload()
            generation = int(blob.generation)
        except gexc.NotFound as exc:
            raise ReplicationError("target_identity_mismatch", code=4) from exc
        except Exception as exc:
            raise _read_error(
                exc,
                missing="target_identity_mismatch",
                precondition="target_identity_mismatch",
            ) from exc
        size, digest = _download_hash(
            blob,
            generation=generation,
            missing="target_identity_mismatch",
            changed="target_identity_mismatch",
        )
        canonical = canonical_marker_bytes(target_id)
        if size != len(canonical) or digest != marker_sha256:
            raise ReplicationError("target_identity_mismatch", code=4)
        return _BoundGcsTarget(
            bucket=bucket,
            prefix=self.prefix,
            target_id=target_id,
            marker_sha256=marker_sha256,
            marker_generation=generation,
        )
