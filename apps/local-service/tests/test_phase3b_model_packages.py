from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

import cinematic_story_service.model_packages as model_packages
from cinematic_story_service.model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    MAX_MANAGED_MODEL_DIRECTORY_ENTRIES,
    MAX_MODEL_PACKAGE_ENTRIES,
    MODEL_PACKAGE_SCHEMA_VERSION,
    ModelPackageArtifact,
    ModelPackageError,
    ModelPackageManager,
    ModelPackageManifest,
    ModelPackageProvenance,
    verify_model_package_path,
)

_REVISION = "1939ad2a8e416c0acfeecc08a694d14ef25f2231"
_REPOSITORY = "onnx-community/Kokoro-82M-v1.0-ONNX"
_SOURCE_PREFIX = f"https://huggingface.co/{_REPOSITORY}/resolve/{_REVISION}/"
_FILES = {
    "onnx/model_quantized.onnx": b"model",
    "voices/af_heart.bin": b"voice",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tiny_manifest() -> ModelPackageManifest:
    return ModelPackageManifest(
        package_id="kokoro-test-q8-af-heart",
        package_version=f"1.0.0+{_REVISION}",
        provider_id="kokoro-local-onnx",
        provider_version="1.0.0",
        model_id=_REPOSITORY,
        model_version="1.0-test",
        runtime_id="onnxruntime-cpu",
        runtime_version="1.28.0",
        platform="windows",
        architecture="x64",
        source_repository=_REPOSITORY,
        source_revision=_REVISION,
        source_classification="maintainer_referenced_conversion",
        official_source_reference="https://huggingface.co/hexgrad/Kokoro-82M",
        license_id="Apache-2.0",
        commercial_use_classification="restricted",
        attribution_requirements=("Retain Apache-2.0 license text.",),
        required_runtime_dependencies=("onnxruntime==1.28.0",),
        compatibility_constraints=("platform:windows", "architecture:x64"),
        revocation_state="active",
        provenance=ModelPackageProvenance(
            conversion_repository=f"https://huggingface.co/{_REPOSITORY}",
            conversion_revision=_REVISION,
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
        artifacts=tuple(
            ModelPackageArtifact(
                path=path,
                size_bytes=len(value),
                sha256=_sha256(value),
                source_url=f"{_SOURCE_PREFIX}{path}",
                role="model" if path.startswith("onnx/") else "voice",
            )
            for path, value in _FILES.items()
        ),
    )


def _write_source(root: Path, files: dict[str, bytes] | None = None) -> Path:
    root.mkdir(parents=True)
    for relative, value in (files or _FILES).items():
        destination = root / Path(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)
    return root


def _manager(root: Path, manifest: ModelPackageManifest) -> ModelPackageManager:
    return ModelPackageManager(root, allowed_manifests=(manifest,))


def test_kokoro_manifest_pins_exact_q8_voice_inventory_and_restricted_rights() -> None:
    manifest = KOKORO_LOCAL_ONNX_MANIFEST
    artifacts = {artifact.path: artifact for artifact in manifest.artifacts}

    assert (
        manifest.fingerprint == "03702762c09a71ee54b7ea3bfa4939d1c622b01d68709e2180a39ca62ec264b0"
    )
    assert manifest.source_revision == _REVISION
    assert manifest.provider_id == "kokoro-local-onnx"
    assert manifest.provider_version == "1.0.0"
    assert manifest.runtime_id == "onnxruntime-cpu"
    assert manifest.runtime_version == "1.28.0"
    assert manifest.platform == "windows"
    assert manifest.architecture == "x64"
    assert manifest.source_classification == "maintainer_referenced_conversion"
    assert manifest.official_source_reference == "https://huggingface.co/hexgrad/Kokoro-82M"
    assert manifest.model_id == _REPOSITORY
    assert manifest.license_id == "Apache-2.0"
    assert manifest.commercial_use_classification == "restricted"
    assert manifest.required_runtime_dependencies == (
        "kokorog2p==0.6.7",
        "numpy==2.5.1",
        "onnxruntime==1.28.0",
    )
    assert manifest.revocation_state == "active"
    assert manifest.provenance.maintainer_reference_revision == (
        "dfb907a02bba8152ca444717ca5d78747ccb4bec"
    )
    assert manifest.voice_id == "af_heart"
    assert manifest.voice_rights_state == "unknown"
    assert manifest.usage_classification == "restricted"
    assert manifest.redistribution_approved is False
    assert set(artifacts) == {
        "config.json",
        "onnx/model_quantized.onnx",
        "tokenizer.json",
        "tokenizer_config.json",
        "voices/af_heart.bin",
    }
    assert (
        artifacts["onnx/model_quantized.onnx"].size_bytes,
        artifacts["onnx/model_quantized.onnx"].sha256,
    ) == (
        92_361_116,
        "fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478",
    )
    assert (
        artifacts["voices/af_heart.bin"].size_bytes,
        artifacts["voices/af_heart.bin"].sha256,
    ) == (
        522_240,
        "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b",
    )
    assert all(f"/resolve/{_REVISION}/" in item.source_url for item in artifacts.values())
    assert set(manifest.to_dict()) == {
        "architecture",
        "artifacts",
        "attributionRequirements",
        "commercialUseClassification",
        "compatibilityConstraints",
        "licenseId",
        "modelId",
        "modelVersion",
        "officialSourceReference",
        "packageId",
        "packageVersion",
        "platform",
        "provenance",
        "providerId",
        "providerVersion",
        "redistributionApproved",
        "requiredRuntimeDependencies",
        "revocationState",
        "runtimeId",
        "runtimeVersion",
        "schemaVersion",
        "sourceClassification",
        "sourceRepository",
        "sourceRevision",
        "totalExpandedBytes",
        "usageClassification",
        "voiceId",
        "voiceRightsState",
    }


def test_model_package_directory_install_activate_deactivate_and_remove(tmp_path: Path) -> None:
    manifest = _tiny_manifest()
    source = _write_source(tmp_path / "source")
    manager = _manager(tmp_path / "managed", manifest)

    installed = manager.install(source, manifest)
    assert installed.valid
    assert installed.verified_files == tuple(sorted(_FILES))
    assert verify_model_package_path(installed.package_path, manifest).valid

    active_path = manager.activate(manifest)
    assert active_path == installed.package_path
    assert manager.active_manifest() == manifest
    active_state = json.loads((manager.state_root / "active.json").read_text(encoding="utf-8"))
    assert active_state == {
        "manifestFingerprint": manifest.fingerprint,
        "packageId": manifest.package_id,
        "packageVersion": manifest.package_version,
        "schemaVersion": MODEL_PACKAGE_SCHEMA_VERSION,
    }

    with pytest.raises(ModelPackageError) as active_error:
        manager.remove(manifest)
    assert active_error.value.code == "MODEL_PACKAGE_ACTIVE"
    assert manager.deactivate() is True
    assert manager.deactivate() is False

    staged = manager.stage_remove(manifest)
    assert staged is not None
    assert not installed.package_path.exists()
    assert staged.tombstone_path.is_dir()
    manager.rollback_staged_removal(staged, manifest)
    assert manager.verify(manifest).valid
    assert not staged.tombstone_path.exists()

    committed = manager.stage_remove(manifest)
    assert committed is not None
    manager.commit_staged_removal(committed, manifest)
    assert not committed.tombstone_path.exists()
    assert manager.remove(manifest) is False


def test_model_package_rejects_unexpected_inventory_and_hash_then_repairs(tmp_path: Path) -> None:
    manifest = _tiny_manifest()
    clean_source = _write_source(tmp_path / "clean")
    extra_source = _write_source(tmp_path / "extra")
    (extra_source / "unexpected").mkdir()
    manager = _manager(tmp_path / "managed", manifest)

    with pytest.raises(ModelPackageError) as inventory_error:
        manager.install(extra_source, manifest)
    assert inventory_error.value.code == "MODEL_PACKAGE_INVENTORY_MISMATCH"

    installed = manager.install(clean_source, manifest)
    (installed.package_path / "onnx" / "model_quantized.onnx").write_bytes(b"MODEL")
    broken = manager.verify(manifest)
    assert not broken.valid
    assert broken.error_codes == ("MODEL_ARTIFACT_HASH_MISMATCH",)

    (installed.package_path / "onnx" / "model_quantized.onnx").write_bytes(b"shorter")
    different_size = manager.verify(manifest)
    assert not different_size.valid
    assert different_size.error_codes == ("MODEL_ARTIFACT_SIZE_MISMATCH",)

    repaired = manager.repair(clean_source, manifest)
    assert repaired.valid
    assert manager.verify(manifest).valid


def test_model_package_zip_accepts_exact_inventory_and_rejects_unsafe_paths(
    tmp_path: Path,
) -> None:
    manifest = _tiny_manifest()
    exact_archive = tmp_path / "exact.zip"
    with zipfile.ZipFile(exact_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, value in _FILES.items():
            archive.writestr(path, value)
    exact_manager = _manager(tmp_path / "exact-managed", manifest)
    assert exact_manager.install_from_archive(exact_archive, manifest).valid

    traversal_archive = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal_archive, "w") as archive:
        archive.writestr("../escape", b"escape")
    traversal_manager = _manager(tmp_path / "traversal-managed", manifest)
    with pytest.raises(ModelPackageError) as traversal_error:
        traversal_manager.install_from_archive(traversal_archive, manifest)
    assert traversal_error.value.code == "MODEL_ARCHIVE_PATH_UNSAFE"
    assert not (tmp_path / "escape").exists()

    collision_archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(collision_archive, "w") as archive:
        archive.writestr("onnx/model_quantized.onnx", _FILES["onnx/model_quantized.onnx"])
        archive.writestr("ONNX/model_quantized.onnx", _FILES["onnx/model_quantized.onnx"])
    collision_manager = _manager(tmp_path / "collision-managed", manifest)
    with pytest.raises(ModelPackageError) as collision_error:
        collision_manager.install_from_archive(collision_archive, manifest)
    assert collision_error.value.code == "MODEL_ARCHIVE_PATH_COLLISION"

    symlink_archive = tmp_path / "symlink.zip"
    symlink = zipfile.ZipInfo("onnx/model_quantized.onnx")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink_archive, "w") as archive:
        archive.writestr(symlink, "../outside")
        archive.writestr("voices/af_heart.bin", _FILES["voices/af_heart.bin"])
    symlink_manager = _manager(tmp_path / "symlink-managed", manifest)
    with pytest.raises(ModelPackageError) as symlink_error:
        symlink_manager.install_from_archive(symlink_archive, manifest)
    assert symlink_error.value.code == "MODEL_ARCHIVE_SPECIAL_ENTRY"


def test_model_package_zip_rejects_declared_entry_overflow_before_zipfile_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _tiny_manifest()
    archive_path = tmp_path / "declared-overflow.zip"
    entry_count = (MAX_MODEL_PACKAGE_ENTRIES * 2) + 1
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index in range(entry_count):
            archive.writestr(f"entries/{index:03d}.bin", b"x")

    constructor_calls = 0

    def forbidden_zipfile_constructor(*_args: object, **_kwargs: object) -> None:
        nonlocal constructor_calls
        constructor_calls += 1
        pytest.fail("ZipFile was constructed before the entry bound was enforced.")

    monkeypatch.setattr(model_packages.zipfile, "ZipFile", forbidden_zipfile_constructor)
    manager = _manager(tmp_path / "managed-declared-overflow", manifest)

    with pytest.raises(ModelPackageError) as entry_limit_error:
        manager.install_from_archive(archive_path, manifest)

    assert entry_limit_error.value.code == "MODEL_ARCHIVE_ENTRY_LIMIT"
    assert constructor_calls == 0
    assert not manager.package_path(manifest).exists()


def test_model_package_zip_stream_counts_forged_central_directory_before_zipfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _tiny_manifest()
    archive_path = tmp_path / "forged-entry-count.zip"
    entry_count = (MAX_MODEL_PACKAGE_ENTRIES * 2) + 1
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index in range(entry_count):
            archive.writestr(f"entries/{index:03d}.bin", b"x")
    archive_bytes = bytearray(archive_path.read_bytes())
    eocd_offset = archive_bytes.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    assert int.from_bytes(archive_bytes[eocd_offset + 10 : eocd_offset + 12], "little") == (
        entry_count
    )
    archive_bytes[eocd_offset + 8 : eocd_offset + 10] = (1).to_bytes(2, "little")
    archive_bytes[eocd_offset + 10 : eocd_offset + 12] = (1).to_bytes(2, "little")
    archive_path.write_bytes(archive_bytes)

    constructor_calls = 0

    def forbidden_zipfile_constructor(*_args: object, **_kwargs: object) -> None:
        nonlocal constructor_calls
        constructor_calls += 1
        pytest.fail("ZipFile was constructed before the entry bound was enforced.")

    monkeypatch.setattr(model_packages.zipfile, "ZipFile", forbidden_zipfile_constructor)
    manager = _manager(tmp_path / "managed-forged-count", manifest)

    with pytest.raises(ModelPackageError) as entry_limit_error:
        manager.install_from_archive(archive_path, manifest)

    assert entry_limit_error.value.code == "MODEL_ARCHIVE_ENTRY_LIMIT"
    assert constructor_calls == 0
    assert not manager.package_path(manifest).exists()


def test_model_package_rejects_reparse_source_when_supported(tmp_path: Path) -> None:
    manifest = _tiny_manifest()
    source = _write_source(tmp_path / "source")
    outside = tmp_path / "outside-model"
    outside.write_bytes(_FILES["onnx/model_quantized.onnx"])
    model_path = source / "onnx" / "model_quantized.onnx"
    model_path.unlink()
    try:
        model_path.symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create a symlink.")

    manager = _manager(tmp_path / "managed", manifest)
    with pytest.raises(ModelPackageError) as reparse_error:
        manager.install(source, manifest)
    assert reparse_error.value.code == "MODEL_PACKAGE_REPARSE_POINT"


def test_model_package_source_inventory_fails_at_bound_without_mutating_source(
    tmp_path: Path,
) -> None:
    manifest = _tiny_manifest()
    source = tmp_path / "oversized-source"
    source.mkdir()
    sentinels = [
        source / f"sentinel-{index:03d}.bin"
        for index in range(MAX_MANAGED_MODEL_DIRECTORY_ENTRIES + 1)
    ]
    for sentinel in sentinels:
        sentinel.write_bytes(b"source sentinel")
    manager = _manager(tmp_path / "managed", manifest)

    with pytest.raises(ModelPackageError) as entry_limit_error:
        manager.install(source, manifest)

    assert entry_limit_error.value.code == "MODEL_PACKAGE_ENTRY_LIMIT"
    assert all(sentinel.read_bytes() == b"source sentinel" for sentinel in sentinels)
    assert not manager.package_path(manifest).exists()


def test_staged_removal_discovery_fails_at_bound_without_touching_entries(
    tmp_path: Path,
) -> None:
    manifest = _tiny_manifest()
    manager = _manager(tmp_path / "managed", manifest)
    tombstone = manager.packages_root / f".removing-{manifest.fingerprint}-{'0' * 32}.pending"
    tombstone.mkdir()
    sentinels = [
        manager.packages_root / f"unrelated-{index:03d}.txt"
        for index in range(MAX_MANAGED_MODEL_DIRECTORY_ENTRIES)
    ]
    for sentinel in sentinels:
        sentinel.write_bytes(b"managed-root sentinel")

    with pytest.raises(ModelPackageError) as entry_limit_error:
        manager.staged_removals(manifest)

    assert entry_limit_error.value.code == "MODEL_PACKAGE_ENTRY_LIMIT"
    assert tombstone.is_dir()
    assert all(sentinel.read_bytes() == b"managed-root sentinel" for sentinel in sentinels)


def test_staged_removal_commit_validates_bound_before_deleting_anything(
    tmp_path: Path,
) -> None:
    manifest = _tiny_manifest()
    source = _write_source(tmp_path / "source")
    manager = _manager(tmp_path / "managed", manifest)
    installed = manager.install(source, manifest)
    staged = manager.stage_remove(manifest)
    assert staged is not None
    unrelated = manager.packages_root / "unrelated-sibling.txt"
    unrelated.write_bytes(b"unrelated sibling")
    sentinels = [
        staged.tombstone_path / f"oversized-{index:03d}.bin"
        for index in range(MAX_MANAGED_MODEL_DIRECTORY_ENTRIES)
    ]
    for sentinel in sentinels:
        sentinel.write_bytes(b"removal sentinel")

    with pytest.raises(ModelPackageError) as entry_limit_error:
        manager.commit_staged_removal(staged, manifest)

    assert entry_limit_error.value.code == "MODEL_PACKAGE_ENTRY_LIMIT"
    assert not installed.package_path.exists()
    assert staged.tombstone_path.is_dir()
    assert all(sentinel.read_bytes() == b"removal sentinel" for sentinel in sentinels)
    assert unrelated.read_bytes() == b"unrelated sibling"
