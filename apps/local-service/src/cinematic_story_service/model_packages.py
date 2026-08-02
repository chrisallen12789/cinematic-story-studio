from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Literal
from uuid import uuid4

from .util import canonical_json, ensure_private_directory, resolve_beneath, sha256_text

MODEL_PACKAGE_SCHEMA_VERSION = "1.0.0"
MAX_MODEL_PACKAGE_ENTRIES = 32
MAX_MANAGED_MODEL_DIRECTORY_ENTRIES = MAX_MODEL_PACKAGE_ENTRIES * 2
MAX_MODEL_PACKAGE_PATH_CHARACTERS = 240
MAX_MODEL_PACKAGE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MODEL_PACKAGE_COMPRESSION_RATIO = 200
MAX_MODEL_PACKAGE_STATE_BYTES = 8 * 1024
_REPARSE_POINT_ATTRIBUTE = 0x400
_MAX_MODEL_ARCHIVE_ENTRIES = MAX_MODEL_PACKAGE_ENTRIES * 2
_ZIP_END_OF_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x05\x06"
_ZIP_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_ZIP_END_OF_CENTRAL_DIRECTORY_BYTES = 22
_ZIP_CENTRAL_DIRECTORY_HEADER_BYTES = 46
_ZIP_MAX_COMMENT_BYTES = 0xFFFF
_MAX_MODEL_ARCHIVE_CENTRAL_DIRECTORY_BYTES = _MAX_MODEL_ARCHIVE_ENTRIES * (
    _ZIP_CENTRAL_DIRECTORY_HEADER_BYTES
    + (MAX_MODEL_PACKAGE_PATH_CHARACTERS * 4)
    + (_ZIP_MAX_COMMENT_BYTES * 2)
)


def _normalize_member_path(raw: str) -> str:
    if (
        not raw
        or len(raw) > MAX_MODEL_PACKAGE_PATH_CHARACTERS
        or "\\" in raw
        or raw.startswith("/")
        or "\x00" in raw
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise ValueError("The model package member path was unsafe.")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or len(path.parts) > 8
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
        or path.as_posix() != raw
    ):
        raise ValueError("The model package member path was unsafe.")
    return path.as_posix()


@dataclass(slots=True)
class ModelPackageError(Exception):
    code: str
    message: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


def _read_exact_archive_bytes(source: IO[bytes], byte_count: int) -> bytes:
    value = bytearray()
    while len(value) < byte_count:
        chunk = source.read(byte_count - len(value))
        if not chunk:
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "The model archive central directory was truncated.",
            )
        value.extend(chunk)
    return bytes(value)


def _preflight_zip_central_directory(source: IO[bytes], archive_size: int) -> int:
    """Bound ZIP metadata before ``zipfile`` materializes its member inventory."""

    if archive_size < _ZIP_END_OF_CENTRAL_DIRECTORY_BYTES:
        raise ModelPackageError(
            "MODEL_ARCHIVE_INVALID",
            "The model archive end-of-central-directory record was missing.",
        )
    tail_size = min(
        archive_size,
        _ZIP_END_OF_CENTRAL_DIRECTORY_BYTES + _ZIP_MAX_COMMENT_BYTES,
    )
    tail_offset = archive_size - tail_size
    source.seek(tail_offset, os.SEEK_SET)
    tail = _read_exact_archive_bytes(source, tail_size)
    eocd_relative_offset: int | None = None
    for candidate in range(
        tail_size - _ZIP_END_OF_CENTRAL_DIRECTORY_BYTES,
        -1,
        -1,
    ):
        if tail[candidate : candidate + 4] != _ZIP_END_OF_CENTRAL_DIRECTORY_SIGNATURE:
            continue
        comment_length = int.from_bytes(tail[candidate + 20 : candidate + 22], "little")
        if candidate + _ZIP_END_OF_CENTRAL_DIRECTORY_BYTES + comment_length == tail_size:
            eocd_relative_offset = candidate
            break
    if eocd_relative_offset is None:
        raise ModelPackageError(
            "MODEL_ARCHIVE_INVALID",
            "The model archive end-of-central-directory record was invalid.",
        )

    eocd = tail[eocd_relative_offset : eocd_relative_offset + _ZIP_END_OF_CENTRAL_DIRECTORY_BYTES]
    disk_number = int.from_bytes(eocd[4:6], "little")
    central_directory_disk = int.from_bytes(eocd[6:8], "little")
    entries_on_disk = int.from_bytes(eocd[8:10], "little")
    total_entries = int.from_bytes(eocd[10:12], "little")
    central_directory_size = int.from_bytes(eocd[12:16], "little")
    central_directory_offset = int.from_bytes(eocd[16:20], "little")
    if (
        entries_on_disk == 0xFFFF
        or total_entries == 0xFFFF
        or central_directory_size == 0xFFFFFFFF
        or central_directory_offset == 0xFFFFFFFF
    ):
        raise ModelPackageError(
            "MODEL_ARCHIVE_INVALID",
            "ZIP64 model archives are not accepted.",
        )
    if disk_number != 0 or central_directory_disk != 0 or entries_on_disk != total_entries:
        raise ModelPackageError(
            "MODEL_ARCHIVE_INVALID",
            "Multi-disk model archives are not accepted.",
        )
    if total_entries > _MAX_MODEL_ARCHIVE_ENTRIES:
        raise ModelPackageError(
            "MODEL_ARCHIVE_ENTRY_LIMIT",
            "The model archive contained too many entries.",
        )
    if central_directory_size > _MAX_MODEL_ARCHIVE_CENTRAL_DIRECTORY_BYTES:
        raise ModelPackageError(
            "MODEL_ARCHIVE_INVALID",
            "The model archive central directory exceeded its fixed byte bound.",
        )

    eocd_absolute_offset = tail_offset + eocd_relative_offset
    if central_directory_offset + central_directory_size != eocd_absolute_offset:
        raise ModelPackageError(
            "MODEL_ARCHIVE_INVALID",
            "The model archive central directory bounds were invalid.",
        )

    source.seek(central_directory_offset, os.SEEK_SET)
    consumed = 0
    actual_entries = 0
    while consumed < central_directory_size:
        if central_directory_size - consumed < _ZIP_CENTRAL_DIRECTORY_HEADER_BYTES:
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "The model archive central directory was truncated.",
            )
        header = _read_exact_archive_bytes(
            source,
            _ZIP_CENTRAL_DIRECTORY_HEADER_BYTES,
        )
        if header[:4] != _ZIP_CENTRAL_DIRECTORY_SIGNATURE:
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "The model archive central directory contained an invalid record.",
            )
        actual_entries += 1
        if actual_entries > _MAX_MODEL_ARCHIVE_ENTRIES:
            raise ModelPackageError(
                "MODEL_ARCHIVE_ENTRY_LIMIT",
                "The model archive contained too many entries.",
            )

        compressed_size = int.from_bytes(header[20:24], "little")
        uncompressed_size = int.from_bytes(header[24:28], "little")
        file_name_length = int.from_bytes(header[28:30], "little")
        extra_field_length = int.from_bytes(header[30:32], "little")
        file_comment_length = int.from_bytes(header[32:34], "little")
        disk_start = int.from_bytes(header[34:36], "little")
        local_header_offset = int.from_bytes(header[42:46], "little")
        if (
            compressed_size == 0xFFFFFFFF
            or uncompressed_size == 0xFFFFFFFF
            or local_header_offset == 0xFFFFFFFF
            or disk_start == 0xFFFF
        ):
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "ZIP64 model archive entries are not accepted.",
            )
        if disk_start != 0:
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "Multi-disk model archive entries are not accepted.",
            )
        if not 0 < file_name_length <= MAX_MODEL_PACKAGE_PATH_CHARACTERS * 4:
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "A model archive member name exceeded its fixed byte bound.",
            )
        variable_length = file_name_length + extra_field_length + file_comment_length
        entry_size = _ZIP_CENTRAL_DIRECTORY_HEADER_BYTES + variable_length
        if entry_size > central_directory_size - consumed:
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "The model archive central directory record exceeded its bounds.",
            )
        source.seek(variable_length, os.SEEK_CUR)
        consumed += entry_size

    if actual_entries != total_entries:
        raise ModelPackageError(
            "MODEL_ARCHIVE_INVALID",
            "The model archive central directory entry count was inconsistent.",
        )
    return actual_entries


@dataclass(frozen=True, slots=True)
class ModelPackageArtifact:
    path: str
    size_bytes: int
    sha256: str
    source_url: str
    role: Literal["model", "voice", "configuration", "tokenizer"]

    def __post_init__(self) -> None:
        normalized = _normalize_member_path(self.path)
        if normalized != self.path:
            raise ValueError("Model artifact paths must be normalized POSIX paths.")
        if not 0 < self.size_bytes <= MAX_MODEL_PACKAGE_TOTAL_BYTES:
            raise ValueError("Model artifact size is outside its fixed bound.")
        if len(self.sha256) != 64 or any(value not in "0123456789abcdef" for value in self.sha256):
            raise ValueError("Model artifact SHA-256 must be lowercase hexadecimal.")
        expected_suffix = f"/{self.path}"
        if not self.source_url.startswith(
            "https://huggingface.co/"
        ) or not self.source_url.endswith(expected_suffix):
            raise ValueError("Model artifact URLs must be revision-pinned Hugging Face URLs.")


@dataclass(frozen=True, slots=True)
class ModelPackageProvenance:
    conversion_repository: str
    conversion_revision: str
    official_upstream_repository: str
    official_upstream_model_sha256: str
    maintainer_reference_repository: str
    maintainer_reference_revision: str
    maintainer_reference_path: str

    def __post_init__(self) -> None:
        if self.conversion_repository != (
            "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX"
        ):
            raise ValueError("The conversion provenance repository was not allow-listed.")
        if self.official_upstream_repository != "https://huggingface.co/hexgrad/Kokoro-82M":
            raise ValueError("The official upstream provenance repository was not allow-listed.")
        if self.maintainer_reference_repository != "https://github.com/hexgrad/kokoro":
            raise ValueError("The maintainer provenance repository was not allow-listed.")
        if self.maintainer_reference_path != "kokoro.js/README.md":
            raise ValueError("The maintainer provenance path was not allow-listed.")
        for revision in (self.conversion_revision, self.maintainer_reference_revision):
            if len(revision) != 40 or any(value not in "0123456789abcdef" for value in revision):
                raise ValueError("Model provenance revisions must be immutable Git commit SHAs.")
        if len(self.official_upstream_model_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.official_upstream_model_sha256
        ):
            raise ValueError("The official upstream model SHA-256 was invalid.")

    def to_dict(self) -> dict[str, str]:
        return {
            "conversionRepository": self.conversion_repository,
            "conversionRevision": self.conversion_revision,
            "maintainerReferencePath": self.maintainer_reference_path,
            "maintainerReferenceRepository": self.maintainer_reference_repository,
            "maintainerReferenceRevision": self.maintainer_reference_revision,
            "officialUpstreamModelSha256": self.official_upstream_model_sha256,
            "officialUpstreamRepository": self.official_upstream_repository,
        }


@dataclass(frozen=True, slots=True)
class ModelPackageManifest:
    package_id: str
    package_version: str
    provider_id: str
    provider_version: str
    model_id: str
    model_version: str
    runtime_id: str
    runtime_version: str
    platform: Literal["windows"]
    architecture: Literal["x64"]
    source_repository: str
    source_revision: str
    source_classification: Literal["maintainer_referenced_conversion"]
    official_source_reference: str
    license_id: str
    commercial_use_classification: Literal["restricted"]
    attribution_requirements: tuple[str, ...]
    required_runtime_dependencies: tuple[str, ...]
    compatibility_constraints: tuple[str, ...]
    revocation_state: Literal["active", "deprecated", "revoked"]
    provenance: ModelPackageProvenance
    voice_id: str
    voice_rights_state: Literal["unknown", "restricted"]
    usage_classification: Literal["restricted"]
    redistribution_approved: bool
    artifacts: tuple[ModelPackageArtifact, ...]
    schema_version: str = MODEL_PACKAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value in (
            self.package_id,
            self.package_version,
            self.provider_id,
            self.provider_version,
            self.model_id,
            self.model_version,
            self.runtime_id,
            self.runtime_version,
            self.voice_id,
        ):
            if not value or len(value) > 160 or any(character.isspace() for character in value):
                raise ValueError("Model package identifiers must be bounded and whitespace-free.")
        if len(self.source_revision) != 40 or any(
            value not in "0123456789abcdef" for value in self.source_revision
        ):
            raise ValueError("The source revision must be an immutable Git commit SHA.")
        if self.source_repository != "onnx-community/Kokoro-82M-v1.0-ONNX":
            raise ValueError("The model package repository is not allow-listed.")
        if self.provider_id != "kokoro-local-onnx" or self.provider_version != "1.0.0":
            raise ValueError("The model package provider identity is not allow-listed.")
        if self.runtime_id != "onnxruntime-cpu" or self.runtime_version != "1.28.0":
            raise ValueError("The model package runtime identity is not allow-listed.")
        if self.platform != "windows" or self.architecture != "x64":
            raise ValueError("The model package platform is not allow-listed.")
        if self.source_classification != "maintainer_referenced_conversion":
            raise ValueError("The model package source classification is not allow-listed.")
        if self.official_source_reference != "https://huggingface.co/hexgrad/Kokoro-82M":
            raise ValueError("The official model source reference is not allow-listed.")
        if self.license_id != "Apache-2.0":
            raise ValueError("The model package license declaration is not allow-listed.")
        if self.commercial_use_classification != "restricted":
            raise ValueError("The model package commercial-use classification must be restricted.")
        for values, label in (
            (self.attribution_requirements, "attribution requirements"),
            (self.required_runtime_dependencies, "runtime dependencies"),
            (self.compatibility_constraints, "compatibility constraints"),
        ):
            if not values or len(values) > 64 or len(values) != len(set(values)):
                raise ValueError(f"Model package {label} must be nonempty, bounded, and unique.")
            if any(not value or len(value) > 512 for value in values):
                raise ValueError(f"Model package {label} contained an invalid value.")
        if self.revocation_state != "active":
            raise ValueError("Only an active allow-listed model manifest can be installed.")
        if self.provenance.conversion_revision != self.source_revision:
            raise ValueError("The conversion provenance did not match the package revision.")
        if self.provenance.official_upstream_repository != self.official_source_reference:
            raise ValueError("The official upstream provenance did not match the manifest.")
        if self.redistribution_approved:
            raise ValueError("Kokoro voice redistribution is not approved by this manifest.")
        if not 1 <= len(self.artifacts) <= MAX_MODEL_PACKAGE_ENTRIES:
            raise ValueError("The model package inventory is outside its fixed bounds.")
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)) or len(paths) != len({path.casefold() for path in paths}):
            raise ValueError("The model package inventory contains a path collision.")
        if sum(artifact.size_bytes for artifact in self.artifacts) > MAX_MODEL_PACKAGE_TOTAL_BYTES:
            raise ValueError("The model package inventory exceeded its fixed byte bound.")
        expected_prefix = (
            f"https://huggingface.co/{self.source_repository}/resolve/{self.source_revision}/"
        )
        if any(not artifact.source_url.startswith(expected_prefix) for artifact in self.artifacts):
            raise ValueError("Every model artifact URL must pin the manifest revision.")

    @property
    def total_size_bytes(self) -> int:
        return sum(artifact.size_bytes for artifact in self.artifacts)

    @property
    def fingerprint(self) -> str:
        return sha256_text(canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "artifacts": [
                {
                    "path": artifact.path,
                    "role": artifact.role,
                    "sha256": artifact.sha256,
                    "sizeBytes": artifact.size_bytes,
                    "sourceUrl": artifact.source_url,
                }
                for artifact in self.artifacts
            ],
            "architecture": self.architecture,
            "attributionRequirements": list(self.attribution_requirements),
            "commercialUseClassification": self.commercial_use_classification,
            "compatibilityConstraints": list(self.compatibility_constraints),
            "licenseId": self.license_id,
            "modelId": self.model_id,
            "modelVersion": self.model_version,
            "officialSourceReference": self.official_source_reference,
            "packageId": self.package_id,
            "packageVersion": self.package_version,
            "platform": self.platform,
            "provenance": self.provenance.to_dict(),
            "providerId": self.provider_id,
            "providerVersion": self.provider_version,
            "redistributionApproved": self.redistribution_approved,
            "requiredRuntimeDependencies": list(self.required_runtime_dependencies),
            "revocationState": self.revocation_state,
            "runtimeId": self.runtime_id,
            "runtimeVersion": self.runtime_version,
            "schemaVersion": self.schema_version,
            "sourceClassification": self.source_classification,
            "sourceRepository": self.source_repository,
            "sourceRevision": self.source_revision,
            "totalExpandedBytes": self.total_size_bytes,
            "usageClassification": self.usage_classification,
            "voiceId": self.voice_id,
            "voiceRightsState": self.voice_rights_state,
        }


_KOKORO_REPOSITORY = "onnx-community/Kokoro-82M-v1.0-ONNX"
_KOKORO_REVISION = "1939ad2a8e416c0acfeecc08a694d14ef25f2231"
_KOKORO_SOURCE_PREFIX = f"https://huggingface.co/{_KOKORO_REPOSITORY}/resolve/{_KOKORO_REVISION}/"

KOKORO_LOCAL_ONNX_MANIFEST = ModelPackageManifest(
    package_id="kokoro-82m-v1.0-onnx-q8-af-heart",
    package_version=f"1.0.0+{_KOKORO_REVISION}",
    provider_id="kokoro-local-onnx",
    provider_version="1.0.0",
    model_id="onnx-community/Kokoro-82M-v1.0-ONNX",
    model_version="1.0",
    runtime_id="onnxruntime-cpu",
    runtime_version="1.28.0",
    platform="windows",
    architecture="x64",
    source_repository=_KOKORO_REPOSITORY,
    source_revision=_KOKORO_REVISION,
    source_classification="maintainer_referenced_conversion",
    official_source_reference="https://huggingface.co/hexgrad/Kokoro-82M",
    license_id="Apache-2.0",
    commercial_use_classification="restricted",
    attribution_requirements=(
        "Retain the Apache-2.0 license text for model distributions.",
        "Preserve applicable third-party attribution and notices after human review.",
    ),
    required_runtime_dependencies=(
        "kokorog2p==0.6.7",
        "numpy==2.5.1",
        "onnxruntime==1.28.0",
    ),
    compatibility_constraints=(
        "platform:windows",
        "architecture:x64",
        "python:>=3.12",
        "execution-provider:CPUExecutionProvider",
        "language:en-US",
        "sample-rate-hz:24000",
        "voice-tensor:little-endian-float32[510,256]",
    ),
    revocation_state="active",
    provenance=ModelPackageProvenance(
        conversion_repository=("https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX"),
        conversion_revision=_KOKORO_REVISION,
        official_upstream_repository="https://huggingface.co/hexgrad/Kokoro-82M",
        official_upstream_model_sha256=(
            "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4"
        ),
        maintainer_reference_repository="https://github.com/hexgrad/kokoro",
        maintainer_reference_revision="dfb907a02bba8152ca444717ca5d78747ccb4bec",
        maintainer_reference_path="kokoro.js/README.md",
    ),
    voice_id="af_heart",
    voice_rights_state="unknown",
    usage_classification="restricted",
    redistribution_approved=False,
    artifacts=(
        ModelPackageArtifact(
            path="config.json",
            size_bytes=44,
            sha256="df34b4f930b23447cd4dc410fabfb42eb3f24e803e6c3f97d618fb359380a36f",
            source_url=f"{_KOKORO_SOURCE_PREFIX}config.json",
            role="configuration",
        ),
        ModelPackageArtifact(
            path="onnx/model_quantized.onnx",
            size_bytes=92_361_116,
            sha256="fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478",
            source_url=f"{_KOKORO_SOURCE_PREFIX}onnx/model_quantized.onnx",
            role="model",
        ),
        ModelPackageArtifact(
            path="tokenizer.json",
            size_bytes=3_497,
            sha256="77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34",
            source_url=f"{_KOKORO_SOURCE_PREFIX}tokenizer.json",
            role="tokenizer",
        ),
        ModelPackageArtifact(
            path="tokenizer_config.json",
            size_bytes=113,
            sha256="be1cb066d6ef6b074b3f15e6a6dd21ac88ff3cdaedf325f0aaed686c70f75d20",
            source_url=f"{_KOKORO_SOURCE_PREFIX}tokenizer_config.json",
            role="tokenizer",
        ),
        ModelPackageArtifact(
            path="voices/af_heart.bin",
            size_bytes=522_240,
            sha256="d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b",
            source_url=f"{_KOKORO_SOURCE_PREFIX}voices/af_heart.bin",
            role="voice",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ModelPackageVerification:
    package_id: str
    package_version: str
    package_path: Path
    manifest_fingerprint: str
    valid: bool
    error_codes: tuple[str, ...]
    verified_files: tuple[str, ...]
    total_size_bytes: int


@dataclass(frozen=True, slots=True)
class StagedModelPackageRemoval:
    """Opaque ownership proof for one same-volume package removal."""

    manifest_fingerprint: str
    original_path: Path
    tombstone_path: Path


class ModelPackageManager:
    """Install immutable, allow-listed model bytes from an already local source."""

    def __init__(
        self,
        root: Path,
        *,
        allowed_manifests: tuple[ModelPackageManifest, ...] = (KOKORO_LOCAL_ONNX_MANIFEST,),
    ) -> None:
        if not allowed_manifests:
            raise ValueError("At least one allow-listed model manifest is required.")
        self._lock = threading.RLock()
        self._allowed = {
            (manifest.package_id, manifest.package_version): manifest
            for manifest in allowed_manifests
        }
        if len(self._allowed) != len(allowed_manifests):
            raise ValueError("Allow-listed model package identities must be unique.")
        requested = root.absolute()
        if requested.exists() and _is_reparse(requested):
            raise ModelPackageError(
                "MODEL_STORAGE_REPARSE_POINT",
                "The model storage root must not be a link or reparse point.",
            )
        self.root = ensure_private_directory(requested).resolve(strict=True)
        if _is_reparse(self.root):
            raise ModelPackageError(
                "MODEL_STORAGE_REPARSE_POINT",
                "The model storage root must not be a link or reparse point.",
            )
        self.packages_root = ensure_private_directory(self.root / "packages")
        self.state_root = ensure_private_directory(self.root / "state")
        _assert_directory_not_reparse(self.packages_root)
        _assert_directory_not_reparse(self.state_root)

    def manifest(
        self,
        package_id: str,
        package_version: str,
    ) -> ModelPackageManifest:
        try:
            return self._allowed[(package_id, package_version)]
        except KeyError as exc:
            raise ModelPackageError(
                "MODEL_PACKAGE_NOT_ALLOWLISTED",
                "The requested model package identity is not allow-listed.",
            ) from exc

    def package_path(self, manifest: ModelPackageManifest) -> Path:
        allowed = self.manifest(manifest.package_id, manifest.package_version)
        if allowed.fingerprint != manifest.fingerprint:
            raise ModelPackageError(
                "MODEL_MANIFEST_MISMATCH",
                "The model package manifest did not match its allow-listed bytes.",
            )
        return resolve_beneath(
            self.packages_root,
            Path(manifest.package_id) / manifest.package_version,
        )

    def install(
        self,
        source: Path,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> ModelPackageVerification:
        with self._lock:
            final_path = self.package_path(manifest)
            if final_path.exists():
                existing = self.verify(manifest)
                if existing.valid:
                    return existing
                raise ModelPackageError(
                    "MODEL_PACKAGE_ALREADY_EXISTS_INVALID",
                    "An invalid package already occupies the managed identity.",
                )
            staging = resolve_beneath(
                self.packages_root,
                f".staging-{manifest.package_id}-{uuid4().hex}",
            )
            ensure_private_directory(staging)
            try:
                self._populate_staging(source, staging, manifest)
                verification = self._verify_path(staging, manifest)
                if not verification.valid:
                    raise ModelPackageError(
                        "MODEL_PACKAGE_VERIFICATION_FAILED",
                        "The staged model package failed exact verification.",
                    )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                _assert_directory_not_reparse(final_path.parent)
                os.replace(staging, final_path)
                published = self.verify(manifest)
                if not published.valid:
                    raise ModelPackageError(
                        "MODEL_PACKAGE_PUBLICATION_FAILED",
                        "The published model package failed exact verification.",
                    )
                return published
            finally:
                if staging.exists():
                    _safe_remove_tree(staging, managed_root=self.packages_root)

    def install_from_archive(
        self,
        archive: Path,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> ModelPackageVerification:
        if archive.suffix.casefold() != ".zip":
            raise ModelPackageError(
                "MODEL_ARCHIVE_FORMAT_UNSUPPORTED",
                "Only ZIP model packages are accepted.",
            )
        return self.install(archive, manifest)

    def verify(
        self,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> ModelPackageVerification:
        with self._lock:
            return self._verify_path(self.package_path(manifest), manifest)

    def activate(
        self,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> Path:
        with self._lock:
            verification = self.verify(manifest)
            if not verification.valid:
                raise ModelPackageError(
                    "MODEL_PACKAGE_NOT_VERIFIED",
                    "Only an exactly verified model package can be activated.",
                )
            state = {
                "manifestFingerprint": manifest.fingerprint,
                "packageId": manifest.package_id,
                "packageVersion": manifest.package_version,
                "schemaVersion": MODEL_PACKAGE_SCHEMA_VERSION,
            }
            _atomic_write_json(self.state_root / "active.json", state)
            return verification.package_path

    def active_manifest(self) -> ModelPackageManifest | None:
        with self._lock:
            path = self.state_root / "active.json"
            if not path.exists():
                return None
            _assert_regular_file(path)
            if path.stat().st_size > MAX_MODEL_PACKAGE_STATE_BYTES:
                raise ModelPackageError(
                    "MODEL_ACTIVE_STATE_INVALID",
                    "The active model state exceeded its fixed byte bound.",
                )
            try:
                decoded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ModelPackageError(
                    "MODEL_ACTIVE_STATE_INVALID",
                    "The active model state was invalid.",
                ) from exc
            if not isinstance(decoded, dict) or set(decoded) != {
                "manifestFingerprint",
                "packageId",
                "packageVersion",
                "schemaVersion",
            }:
                raise ModelPackageError(
                    "MODEL_ACTIVE_STATE_INVALID",
                    "The active model state was invalid.",
                )
            package_id = decoded.get("packageId")
            package_version = decoded.get("packageVersion")
            if not isinstance(package_id, str) or not isinstance(package_version, str):
                raise ModelPackageError(
                    "MODEL_ACTIVE_STATE_INVALID",
                    "The active model state was invalid.",
                )
            manifest = self.manifest(package_id, package_version)
            if (
                decoded.get("schemaVersion") != MODEL_PACKAGE_SCHEMA_VERSION
                or decoded.get("manifestFingerprint") != manifest.fingerprint
                or not self.verify(manifest).valid
            ):
                raise ModelPackageError(
                    "MODEL_ACTIVE_STATE_INVALID",
                    "The active model state did not reference a verified package.",
                )
            return manifest

    def deactivate(self) -> bool:
        with self._lock:
            path = self.state_root / "active.json"
            if not path.exists():
                return False
            _assert_regular_file(path)
            tombstone = self.state_root / f".inactive-{uuid4().hex}.json"
            os.replace(path, tombstone)
            tombstone.unlink()
            return True

    def repair(
        self,
        source: Path,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> ModelPackageVerification:
        with self._lock:
            final_path = self.package_path(manifest)
            if not final_path.exists():
                return self.install(source, manifest)
            staging = resolve_beneath(
                self.packages_root,
                f".repair-{manifest.package_id}-{uuid4().hex}",
            )
            backup = resolve_beneath(
                self.packages_root,
                f".backup-{manifest.package_id}-{uuid4().hex}",
            )
            ensure_private_directory(staging)
            try:
                self._populate_staging(source, staging, manifest)
                if not self._verify_path(staging, manifest).valid:
                    raise ModelPackageError(
                        "MODEL_PACKAGE_REPAIR_SOURCE_INVALID",
                        "The repair source failed exact verification.",
                    )
                _assert_directory_not_reparse(final_path)
                os.replace(final_path, backup)
                try:
                    os.replace(staging, final_path)
                    repaired = self.verify(manifest)
                    if not repaired.valid:
                        raise ModelPackageError(
                            "MODEL_PACKAGE_REPAIR_FAILED",
                            "The repaired package failed exact verification.",
                        )
                except Exception:
                    if final_path.exists():
                        _safe_remove_tree(final_path, managed_root=self.packages_root)
                    os.replace(backup, final_path)
                    raise
                _safe_remove_tree(backup, managed_root=self.packages_root)
                return repaired
            finally:
                if staging.exists():
                    _safe_remove_tree(staging, managed_root=self.packages_root)
                if backup.exists() and final_path.exists():
                    _safe_remove_tree(backup, managed_root=self.packages_root)

    def remove(
        self,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> bool:
        staged = self.stage_remove(manifest)
        if staged is None:
            return False
        self.commit_staged_removal(staged, manifest)
        return True

    def stage_remove(
        self,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> StagedModelPackageRemoval | None:
        """Make a package unavailable without irreversibly deleting its bytes."""

        with self._lock:
            active = self.active_manifest()
            if active is not None and (
                active.package_id,
                active.package_version,
            ) == (manifest.package_id, manifest.package_version):
                raise ModelPackageError(
                    "MODEL_PACKAGE_ACTIVE",
                    "Deactivate the model package before removing it.",
                )
            target = self.package_path(manifest)
            try:
                target.lstat()
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise ModelPackageError(
                    "MODEL_PACKAGE_IO_ERROR",
                    "The managed model package could not be inspected.",
                ) from exc
            _assert_directory_not_reparse(target)
            tombstone = resolve_beneath(
                self.packages_root,
                f".removing-{manifest.fingerprint}-{uuid4().hex}.pending",
            )
            os.replace(target, tombstone)
            return StagedModelPackageRemoval(
                manifest_fingerprint=manifest.fingerprint,
                original_path=target,
                tombstone_path=tombstone,
            )

    def staged_removals(
        self,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> tuple[StagedModelPackageRemoval, ...]:
        """Discover only exact owned removal tombstones for startup recovery."""

        with self._lock:
            original_path = self.package_path(manifest)
            entries: list[Path] = []
            try:
                with os.scandir(self.packages_root) as scanner:
                    for directory_entry in scanner:
                        if len(entries) >= MAX_MANAGED_MODEL_DIRECTORY_ENTRIES:
                            raise ModelPackageError(
                                "MODEL_PACKAGE_ENTRY_LIMIT",
                                "The managed model storage exceeded its fixed entry bound.",
                            )
                        entries.append(Path(directory_entry.path))
            except ModelPackageError:
                raise
            except OSError as exc:
                raise ModelPackageError(
                    "MODEL_PACKAGE_IO_ERROR",
                    "The managed model storage could not be inspected.",
                ) from exc
            entries.sort(key=lambda value: value.name.casefold())
            expected_prefix = f".removing-{manifest.fingerprint}-"
            removals: list[StagedModelPackageRemoval] = []
            for candidate in entries:
                if not (
                    candidate.name.startswith(expected_prefix)
                    and candidate.name.endswith(".pending")
                ):
                    continue
                staged = StagedModelPackageRemoval(
                    manifest_fingerprint=manifest.fingerprint,
                    original_path=original_path,
                    tombstone_path=candidate,
                )
                self._assert_staged_removal(staged, manifest)
                _assert_directory_not_reparse(candidate)
                removals.append(staged)
            if len(removals) > 1:
                raise ModelPackageError(
                    "MODEL_PACKAGE_REMOVAL_STATE_INVALID",
                    "Multiple staged removals could not be reconciled safely.",
                )
            return tuple(removals)

    def rollback_staged_removal(
        self,
        staged: StagedModelPackageRemoval,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> None:
        """Restore only the exact package directory moved by ``stage_remove``."""

        with self._lock:
            self._assert_staged_removal(staged, manifest)
            if staged.original_path.exists():
                raise ModelPackageError(
                    "MODEL_PACKAGE_REMOVAL_COLLISION",
                    "The staged model package removal could not be restored safely.",
                )
            _assert_directory_not_reparse(staged.tombstone_path)
            staged.original_path.parent.mkdir(parents=True, exist_ok=True)
            _assert_directory_not_reparse(staged.original_path.parent)
            os.replace(staged.tombstone_path, staged.original_path)
            _assert_directory_not_reparse(staged.original_path)

    def commit_staged_removal(
        self,
        staged: StagedModelPackageRemoval,
        manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
    ) -> None:
        """Irreversibly delete a previously staged, exactly owned package."""

        with self._lock:
            self._assert_staged_removal(staged, manifest)
            if staged.original_path.exists():
                raise ModelPackageError(
                    "MODEL_PACKAGE_REMOVAL_COLLISION",
                    "The staged model package removal no longer had exclusive ownership.",
                )
            _safe_remove_tree(
                staged.tombstone_path,
                managed_root=self.packages_root,
            )

    def _assert_staged_removal(
        self,
        staged: StagedModelPackageRemoval,
        manifest: ModelPackageManifest,
    ) -> None:
        expected_original = self.package_path(manifest)
        expected_prefix = f".removing-{manifest.fingerprint}-"
        tombstone_name = staged.tombstone_path.name
        nonce = tombstone_name.removeprefix(expected_prefix).removesuffix(".pending")
        if (
            staged.manifest_fingerprint != manifest.fingerprint
            or staged.original_path != expected_original
            or staged.tombstone_path.parent != self.packages_root
            or not tombstone_name.startswith(expected_prefix)
            or not tombstone_name.endswith(".pending")
            or len(nonce) != 32
            or any(character not in "0123456789abcdef" for character in nonce)
        ):
            raise ModelPackageError(
                "MODEL_PACKAGE_REMOVAL_OWNERSHIP_INVALID",
                "The staged model package removal ownership proof was invalid.",
            )

    def _populate_staging(
        self,
        source: Path,
        staging: Path,
        manifest: ModelPackageManifest,
    ) -> None:
        source_absolute = source.absolute()
        if not source_absolute.exists():
            raise ModelPackageError(
                "MODEL_PACKAGE_SOURCE_MISSING",
                "The local model package source does not exist.",
            )
        if _is_reparse(source_absolute):
            raise ModelPackageError(
                "MODEL_PACKAGE_SOURCE_REPARSE_POINT",
                "Model package sources must not be links or reparse points.",
            )
        if source_absolute.is_dir():
            self._copy_directory(source_absolute, staging, manifest)
            return
        if source_absolute.is_file() and source_absolute.suffix.casefold() == ".zip":
            self._extract_zip(source_absolute, staging, manifest)
            return
        raise ModelPackageError(
            "MODEL_PACKAGE_SOURCE_UNSUPPORTED",
            "The model package source must be a directory or ZIP archive.",
        )

    @staticmethod
    def _copy_directory(
        source: Path,
        staging: Path,
        manifest: ModelPackageManifest,
    ) -> None:
        inventory = _directory_inventory(
            source,
            allowed_directories=_manifest_directories(manifest),
        )
        expected = {artifact.path: artifact for artifact in manifest.artifacts}
        if set(inventory) != set(expected):
            raise ModelPackageError(
                "MODEL_PACKAGE_INVENTORY_MISMATCH",
                "The model package source inventory did not match the allow-list.",
            )
        for relative_path, source_path in inventory.items():
            artifact = expected[relative_path]
            destination = resolve_beneath(staging, Path(*PurePosixPath(relative_path).parts))
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_exact_file(source_path, destination, artifact)

    @staticmethod
    def _extract_zip(
        archive: Path,
        staging: Path,
        manifest: ModelPackageManifest,
    ) -> None:
        _assert_regular_file(archive)
        expected = {artifact.path: artifact for artifact in manifest.artifacts}
        allowed_directories = {
            parent.as_posix()
            for artifact in manifest.artifacts
            for parent in _parents(PurePosixPath(artifact.path))
        }
        seen: set[str] = set()
        seen_folded: set[str] = set()
        try:
            with archive.open("rb") as archive_stream:
                before = os.fstat(archive_stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ModelPackageError(
                        "MODEL_ARCHIVE_INVALID",
                        "The model archive was not a regular file.",
                    )
                preflight_entries = _preflight_zip_central_directory(
                    archive_stream,
                    before.st_size,
                )
                archive_stream.seek(0, os.SEEK_SET)
                with zipfile.ZipFile(archive_stream, "r") as package:
                    infos = package.infolist()
                    if len(infos) != preflight_entries:
                        raise ModelPackageError(
                            "MODEL_ARCHIVE_INVALID",
                            "The model archive member count changed after preflight.",
                        )
                    for info in infos:
                        raw_name = info.filename.rstrip("/") if info.is_dir() else info.filename
                        try:
                            normalized = _normalize_member_path(raw_name)
                        except ValueError as exc:
                            raise ModelPackageError(
                                "MODEL_ARCHIVE_PATH_UNSAFE",
                                "The model archive contained an unsafe member path.",
                            ) from exc
                        folded = normalized.casefold()
                        if normalized in seen or folded in seen_folded:
                            raise ModelPackageError(
                                "MODEL_ARCHIVE_PATH_COLLISION",
                                "The model archive contained a path collision.",
                            )
                        seen.add(normalized)
                        seen_folded.add(folded)
                        if info.flag_bits & 0x1:
                            raise ModelPackageError(
                                "MODEL_ARCHIVE_ENCRYPTED",
                                "Encrypted model archive entries are not accepted.",
                            )
                        mode = (info.external_attr >> 16) & 0xFFFF
                        kind = stat.S_IFMT(mode)
                        if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                            raise ModelPackageError(
                                "MODEL_ARCHIVE_SPECIAL_ENTRY",
                                "Model archives must not contain links or special files.",
                            )
                        if info.is_dir():
                            if normalized not in allowed_directories:
                                raise ModelPackageError(
                                    "MODEL_PACKAGE_INVENTORY_MISMATCH",
                                    "The model archive contained an unexpected directory.",
                                )
                            continue
                        artifact = expected.get(normalized)
                        if artifact is None:
                            raise ModelPackageError(
                                "MODEL_PACKAGE_INVENTORY_MISMATCH",
                                "The model archive contained an unexpected file.",
                            )
                        if info.file_size != artifact.size_bytes:
                            raise ModelPackageError(
                                "MODEL_ARTIFACT_SIZE_MISMATCH",
                                "A model archive entry had an unexpected size.",
                            )
                        if info.file_size and (
                            info.compress_size == 0
                            or info.file_size
                            > info.compress_size * MAX_MODEL_PACKAGE_COMPRESSION_RATIO
                        ):
                            raise ModelPackageError(
                                "MODEL_ARCHIVE_COMPRESSION_LIMIT",
                                "A model archive entry exceeded its compression-ratio bound.",
                            )
                        destination = resolve_beneath(
                            staging,
                            Path(*PurePosixPath(normalized).parts),
                        )
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with package.open(info, "r") as source_stream:
                            _write_verified_stream(source_stream, destination, artifact)
                after = os.fstat(archive_stream.fileno())
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise ModelPackageError(
                        "MODEL_ARCHIVE_CHANGED_DURING_READ",
                        "The model archive changed during installation.",
                    )
        except (OSError, UnicodeDecodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ModelPackageError(
                "MODEL_ARCHIVE_INVALID",
                "The model archive could not be read safely.",
            ) from exc
        if {artifact.path for artifact in manifest.artifacts} - seen:
            raise ModelPackageError(
                "MODEL_PACKAGE_INVENTORY_MISMATCH",
                "The model archive omitted an allow-listed artifact.",
            )

    @staticmethod
    def _verify_path(
        package_path: Path,
        manifest: ModelPackageManifest,
    ) -> ModelPackageVerification:
        error_codes: list[str] = []
        verified: list[str] = []
        total = 0
        if not package_path.exists():
            error_codes.append("MODEL_PACKAGE_MISSING")
        else:
            try:
                _assert_directory_not_reparse(package_path)
                inventory = _directory_inventory(
                    package_path,
                    allowed_directories=_manifest_directories(manifest),
                )
                expected = {artifact.path: artifact for artifact in manifest.artifacts}
                if set(inventory) != set(expected):
                    error_codes.append("MODEL_PACKAGE_INVENTORY_MISMATCH")
                for relative_path in sorted(set(inventory) & set(expected)):
                    artifact = expected[relative_path]
                    source = inventory[relative_path]
                    size, digest = _hash_file_bounded(source, artifact.size_bytes)
                    total += size
                    if size != artifact.size_bytes:
                        error_codes.append("MODEL_ARTIFACT_SIZE_MISMATCH")
                    elif digest != artifact.sha256:
                        error_codes.append("MODEL_ARTIFACT_HASH_MISMATCH")
                    else:
                        verified.append(relative_path)
            except ModelPackageError as exc:
                error_codes.append(exc.code)
            except OSError:
                error_codes.append("MODEL_PACKAGE_IO_ERROR")
        return ModelPackageVerification(
            package_id=manifest.package_id,
            package_version=manifest.package_version,
            package_path=package_path,
            manifest_fingerprint=manifest.fingerprint,
            valid=not error_codes,
            error_codes=tuple(sorted(set(error_codes))),
            verified_files=tuple(verified),
            total_size_bytes=total,
        )


def verify_model_package_path(
    package_path: Path,
    manifest: ModelPackageManifest = KOKORO_LOCAL_ONNX_MANIFEST,
) -> ModelPackageVerification:
    """Verify an explicit package directory without creating manager state."""

    return ModelPackageManager._verify_path(package_path.absolute(), manifest)


def _parents(path: PurePosixPath) -> tuple[PurePosixPath, ...]:
    values: list[PurePosixPath] = []
    current = path.parent
    while current != PurePosixPath("."):
        values.append(current)
        current = current.parent
    return tuple(values)


def _manifest_directories(manifest: ModelPackageManifest) -> set[str]:
    return {
        parent.as_posix()
        for artifact in manifest.artifacts
        for parent in _parents(PurePosixPath(artifact.path))
    }


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _assert_directory_not_reparse(path: Path) -> None:
    if _is_reparse(path) or not path.is_dir():
        raise ModelPackageError(
            "MODEL_PACKAGE_REPARSE_POINT",
            "Managed model directories must not be links or reparse points.",
        )


def _assert_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ModelPackageError(
            "MODEL_PACKAGE_IO_ERROR",
            "A managed model file could not be inspected.",
        ) from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(metadata.st_mode)
        or attributes & _REPARSE_POINT_ATTRIBUTE
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise ModelPackageError(
            "MODEL_PACKAGE_SPECIAL_FILE",
            "Managed model inventories accept regular files only.",
        )


def _directory_inventory(
    root: Path,
    *,
    allowed_directories: set[str] | None = None,
) -> dict[str, Path]:
    _assert_directory_not_reparse(root)
    inventory: dict[str, Path] = {}
    seen_paths: set[str] = set()
    folded_paths: set[str] = set()
    pending: list[tuple[Path, PurePosixPath | None]] = [(root, None)]
    visited_entries = 0
    while pending:
        directory, relative_parent = pending.pop()
        _assert_directory_not_reparse(directory)
        entries: list[os.DirEntry[str]] = []
        try:
            with os.scandir(directory) as scanner:
                for entry in scanner:
                    visited_entries += 1
                    if visited_entries > MAX_MANAGED_MODEL_DIRECTORY_ENTRIES:
                        raise ModelPackageError(
                            "MODEL_PACKAGE_ENTRY_LIMIT",
                            "The model package contained too many entries.",
                        )
                    entries.append(entry)
        except ModelPackageError:
            raise
        except OSError as exc:
            raise ModelPackageError(
                "MODEL_PACKAGE_IO_ERROR",
                "A model package directory could not be inspected.",
            ) from exc
        entries.sort(key=lambda entry: entry.name.casefold())
        for entry in entries:
            relative = (
                PurePosixPath(entry.name)
                if relative_parent is None
                else relative_parent / entry.name
            )
            normalized = _normalize_member_path(relative.as_posix())
            entry_path = Path(entry.path)
            casefolded = normalized.casefold()
            if normalized in seen_paths or casefolded in folded_paths:
                raise ModelPackageError(
                    "MODEL_PACKAGE_PATH_COLLISION",
                    "The model package contained a path collision.",
                )
            seen_paths.add(normalized)
            folded_paths.add(casefolded)
            if entry.is_symlink() or _is_reparse(entry_path):
                raise ModelPackageError(
                    "MODEL_PACKAGE_REPARSE_POINT",
                    "Model package inventories must not contain links or reparse points.",
                )
            if entry.is_dir(follow_symlinks=False):
                if allowed_directories is not None and normalized not in allowed_directories:
                    raise ModelPackageError(
                        "MODEL_PACKAGE_INVENTORY_MISMATCH",
                        "The model package contained an unexpected directory.",
                    )
                pending.append((entry_path, relative))
                continue
            if not entry.is_file(follow_symlinks=False):
                raise ModelPackageError(
                    "MODEL_PACKAGE_SPECIAL_FILE",
                    "Model package inventories accept regular files only.",
                )
            inventory[normalized] = entry_path
    return inventory


def _hash_file_bounded(path: Path, expected_size: int) -> tuple[int, str]:
    _assert_regular_file(path)
    before = path.stat(follow_symlinks=False)
    if before.st_size != expected_size:
        return before.st_size, ""
    hasher = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > expected_size:
                raise ModelPackageError(
                    "MODEL_ARTIFACT_SIZE_MISMATCH",
                    "A model artifact exceeded its allow-listed size.",
                )
            hasher.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ModelPackageError(
            "MODEL_ARTIFACT_CHANGED_DURING_READ",
            "A model artifact changed during verification.",
        )
    return total, hasher.hexdigest()


def _copy_exact_file(
    source: Path,
    destination: Path,
    artifact: ModelPackageArtifact,
) -> None:
    _assert_regular_file(source)
    before = source.stat(follow_symlinks=False)
    with source.open("rb") as input_stream:
        _write_verified_stream(input_stream, destination, artifact)
    after = source.stat(follow_symlinks=False)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        destination.unlink(missing_ok=True)
        raise ModelPackageError(
            "MODEL_ARTIFACT_CHANGED_DURING_READ",
            "A model artifact changed during installation.",
        )


def _write_verified_stream(
    source: IO[bytes],
    destination: Path,
    artifact: ModelPackageArtifact,
) -> None:
    hasher = hashlib.sha256()
    total = 0
    try:
        with destination.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > artifact.size_bytes:
                    raise ModelPackageError(
                        "MODEL_ARTIFACT_SIZE_MISMATCH",
                        "A model artifact exceeded its allow-listed size.",
                    )
                output.write(chunk)
                hasher.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if total != artifact.size_bytes:
        destination.unlink(missing_ok=True)
        raise ModelPackageError(
            "MODEL_ARTIFACT_SIZE_MISMATCH",
            "A model artifact did not match its allow-listed size.",
        )
    if hasher.hexdigest() != artifact.sha256:
        destination.unlink(missing_ok=True)
        raise ModelPackageError(
            "MODEL_ARTIFACT_HASH_MISMATCH",
            "A model artifact did not match its allow-listed hash.",
        )


def _safe_remove_tree(path: Path, *, managed_root: Path) -> None:
    root = managed_root.resolve(strict=True)
    candidate = path.absolute()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ModelPackageError(
            "MODEL_PACKAGE_PATH_ESCAPE",
            "The model package path escaped its managed root.",
        ) from exc
    if candidate == root:
        raise ModelPackageError(
            "MODEL_PACKAGE_PATH_ESCAPE",
            "The model storage root cannot be removed.",
        )
    if not candidate.exists():
        return
    inventory_paths: list[Path] = []
    directories: list[Path] = []
    pending = [candidate]
    visited_entries = 0
    while pending:
        current = pending.pop()
        _assert_directory_not_reparse(current)
        directories.append(current)
        try:
            with os.scandir(current) as scanner:
                for entry in scanner:
                    visited_entries += 1
                    if visited_entries > MAX_MANAGED_MODEL_DIRECTORY_ENTRIES:
                        raise ModelPackageError(
                            "MODEL_PACKAGE_ENTRY_LIMIT",
                            "The managed model directory exceeded its fixed entry bound.",
                        )
                    entry_path = Path(entry.path)
                    if entry.is_symlink() or _is_reparse(entry_path):
                        raise ModelPackageError(
                            "MODEL_PACKAGE_REPARSE_POINT",
                            "A managed model directory contained a reparse point.",
                        )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry_path)
                    elif entry.is_file(follow_symlinks=False):
                        inventory_paths.append(entry_path)
                    else:
                        raise ModelPackageError(
                            "MODEL_PACKAGE_SPECIAL_FILE",
                            "A managed model directory contained a special file.",
                        )
        except ModelPackageError:
            raise
        except OSError as exc:
            raise ModelPackageError(
                "MODEL_PACKAGE_IO_ERROR",
                "A managed model directory could not be inspected for removal.",
            ) from exc
    for file_path in inventory_paths:
        file_path.unlink()
    for directory in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        directory.rmdir()


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    _assert_directory_not_reparse(path.parent)
    payload = (canonical_json(value) + "\n").encode("utf-8")
    if len(payload) > MAX_MODEL_PACKAGE_STATE_BYTES:
        raise ModelPackageError(
            "MODEL_ACTIVE_STATE_INVALID",
            "The active model state exceeded its fixed byte bound.",
        )
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
