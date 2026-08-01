from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import func, select

from cinematic_story_service.audition_repository import (
    DEFAULT_AUDITION_PAGE_SIZE,
    MAX_AUDITION_PAGE_SIZE,
    AuditionRepository,
    _public_provenance,
)
from cinematic_story_service.config import ServiceSettings
from cinematic_story_service.database import Database
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    ModelPackageManager,
    ModelPackageVerification,
)
from cinematic_story_service.models import (
    ModelPackageManifestRow,
    ProjectRow,
    PronunciationDictionaryRow,
)
from cinematic_story_service.schemas import (
    ClearAuditionCacheRequest,
    InstallModelPackageRequest,
    ModelInstallationOperationRequest,
)
from cinematic_story_service.util import canonical_json, parse_json, utc_now


class _FakeModelPackageManager:
    def __init__(self, package_path: Path) -> None:
        self.package_path = package_path
        self.active = False

    def _verification(self) -> ModelPackageVerification:
        return ModelPackageVerification(
            package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            package_version=KOKORO_LOCAL_ONNX_MANIFEST.package_version,
            package_path=self.package_path,
            manifest_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
            valid=True,
            error_codes=(),
            verified_files=tuple(
                artifact.path for artifact in KOKORO_LOCAL_ONNX_MANIFEST.artifacts
            ),
            total_size_bytes=KOKORO_LOCAL_ONNX_MANIFEST.total_size_bytes,
        )

    def install_from_archive(self, _archive: Path, _manifest: object) -> ModelPackageVerification:
        return self._verification()

    def repair(self, _archive: Path, _manifest: object) -> ModelPackageVerification:
        return self._verification()

    def verify(self, _manifest: object) -> ModelPackageVerification:
        return self._verification()

    def activate(self, _manifest: object) -> Path:
        self.active = True
        return self.package_path

    def deactivate(self) -> bool:
        was_active = self.active
        self.active = False
        return was_active

    def remove(self, _manifest: object) -> bool:
        return True


def test_public_provenance_projects_private_fields_and_fails_closed() -> None:
    valid = {
        "origin": "application",
        "producerId": "cinematic-story-service",
        "producerVersion": "0.1.0",
        "recordedAt": "2026-08-01T00:00:00.000Z",
        "inputFingerprint": "ab" * 32,
        "reasonCode": "MODEL_VERIFIED",
        "details": {"privateEvidence": True},
        "conversionRepository": "https://example.invalid/private-source",
    }
    assert _public_provenance(canonical_json(valid)) == {
        key: valid[key]
        for key in (
            "origin",
            "producerId",
            "producerVersion",
            "recordedAt",
            "inputFingerprint",
            "reasonCode",
        )
    }

    invalid_values = (
        [],
        {key: value for key, value in valid.items() if key != "recordedAt"},
        {**valid, "origin": "mutable_provider"},
        {**valid, "producerId": "not a safe code"},
        {**valid, "recordedAt": "not-a-timestamp"},
        {**valid, "inputFingerprint": "latest"},
        {**valid, "inputFingerprint": None},
        {**valid, "reasonCode": "not a safe code"},
        {**valid, "reasonCode": None},
    )
    for invalid in invalid_values:
        with pytest.raises(ServiceError) as raised:
            _public_provenance(canonical_json(invalid))
        assert raised.value.status_code == 500
        assert raised.value.code == "SPEECH_PROVENANCE_INVALID"
    with pytest.raises(ServiceError) as malformed:
        _public_provenance("{")
    assert malformed.value.status_code == 500
    assert malformed.value.code == "SPEECH_PROVENANCE_INVALID"


@pytest.fixture
def repository(
    tmp_path: Path,
) -> Iterator[tuple[AuditionRepository, Database, ServiceSettings]]:
    settings = ServiceSettings(
        data_dir=tmp_path / "private-data",
        bearer_token="ab" * 32,
        worker_enabled=False,
    ).validated()
    database = Database(settings.database_path)
    now = utc_now()
    with database.session() as session:
        for project_id in ("project-1", "project-2"):
            session.add(
                ProjectRow(
                    id=project_id,
                    name=project_id,
                    status="draft",
                    revision=1,
                    story_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    auditions = AuditionRepository(database, settings)
    try:
        yield auditions, database, settings
    finally:
        auditions.shutdown_runtimes()
        database.close()


def test_seeded_model_packages_are_bounded_and_cursor_bound_to_project(
    repository: tuple[AuditionRepository, Database, ServiceSettings],
) -> None:
    auditions, _database, _settings = repository

    first, cursor, total = auditions.list_model_packages(
        project_id="project-1",
        cursor=None,
        limit=1,
    )
    assert len(first) == 1
    assert total == 2
    assert cursor is not None

    second, final_cursor, second_total = auditions.list_model_packages(
        project_id="project-1",
        cursor=cursor,
        limit=1,
    )
    assert len(second) == 1
    assert second_total == total
    assert final_cursor is None
    assert first[0]["manifest"]["modelPackageId"] != second[0]["manifest"]["modelPackageId"]

    with pytest.raises(ServiceError) as error:
        auditions.list_model_packages(
            project_id="project-2",
            cursor=cursor,
            limit=1,
        )
    assert error.value.code == "INVALID_CURSOR"
    assert DEFAULT_AUDITION_PAGE_SIZE == 50
    assert MAX_AUDITION_PAGE_SIZE == 200


def test_dictionary_is_lazily_initialized_once_and_visible_in_workspace(
    repository: tuple[AuditionRepository, Database, ServiceSettings],
) -> None:
    auditions, database, _settings = repository
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PronunciationDictionaryRow)
                .where(PronunciationDictionaryRow.project_id == "project-1")
            )
            == 0
        )

    dictionary, entries, cursor, total = auditions.list_pronunciation_entries(
        project_id="project-1",
        cursor=None,
        limit=DEFAULT_AUDITION_PAGE_SIZE,
    )
    assert dictionary["revision"] == 1
    assert dictionary["entryCount"] == 0
    assert entries == []
    assert cursor is None
    assert total == 0

    workspace = auditions.workspace_snapshot("project-1")
    assert workspace["currentDictionary"] == dictionary
    assert workspace["roles"] == {"items": [], "pageSize": 0, "total": 0}
    assert workspace["runtimeInstances"] == []
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PronunciationDictionaryRow)
                .where(PronunciationDictionaryRow.project_id == "project-1")
            )
            == 1
        )


def test_fixture_model_lifecycle_is_explicit_and_idempotent(
    repository: tuple[AuditionRepository, Database, ServiceSettings],
) -> None:
    auditions, _database, _settings = repository
    packages, _cursor, _total = auditions.list_model_packages(
        project_id="project-1",
        cursor=None,
        limit=MAX_AUDITION_PAGE_SIZE,
    )
    fixture = next(
        item
        for item in packages
        if item["manifest"]["sourceClassification"] == "repository_fixture"
    )["manifest"]
    verify = ModelInstallationOperationRequest(
        model_package_id=fixture["modelPackageId"],
        expected_manifest_fingerprint=fixture["manifestFingerprint"],
        expected_installation_revision=None,
        action="verify",
        reason="Verify the deterministic fixture.",
        idempotency_key="fixture-verify",
    )
    first = auditions.perform_model_package_action(
        project_id="project-1",
        request=verify,
        actor_id="local_user",
    )
    replay = auditions.perform_model_package_action(
        project_id="project-1",
        request=verify,
        actor_id="local_user",
    )
    assert replay == first
    assert first["installation"]["status"] == "installed"
    assert first["verification"]["status"] == "verified"

    activated = auditions.perform_model_package_action(
        project_id="project-1",
        request=ModelInstallationOperationRequest(
            model_package_id=fixture["modelPackageId"],
            expected_manifest_fingerprint=fixture["manifestFingerprint"],
            expected_installation_revision=1,
            action="activate",
            reason="Activate the verified deterministic fixture.",
            idempotency_key="fixture-activate",
        ),
        actor_id="local_user",
    )
    assert activated["installation"]["status"] == "active"
    assert activated["installation"]["installationRevision"] == 2


def test_empty_cache_clear_is_idempotent_and_does_not_advance_project_revision(
    repository: tuple[AuditionRepository, Database, ServiceSettings],
) -> None:
    auditions, _database, _settings = repository
    request = ClearAuditionCacheRequest(
        expected_project_revision=1,
        reason="Clear private audition cache data.",
        idempotency_key="clear-empty-cache",
    )
    expected = {
        "projectId": "project-1",
        "clearedRecordCount": 0,
        "alreadyClearedRecordCount": 0,
        "projectRevision": 1,
    }
    assert (
        auditions.clear_cache(
            project_id="project-1",
            request=request,
            actor_id="local_user",
        )
        == expected
    )
    assert (
        auditions.clear_cache(
            project_id="project-1",
            request=request,
            actor_id="local_user",
        )
        == expected
    )
    with pytest.raises(ServiceError) as error:
        auditions.clear_cache(
            project_id="project-1",
            request=ClearAuditionCacheRequest(
                expected_project_revision=1,
                reason="A different operation using the same key.",
                idempotency_key="clear-empty-cache",
            ),
            actor_id="local_user",
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_managed_model_install_is_persisted_path_free_and_not_reported_live(
    tmp_path: Path,
) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "private-data",
        bearer_token="ab" * 32,
        worker_enabled=False,
    ).validated()
    database = Database(settings.database_path)
    now = utc_now()
    with database.session() as session:
        session.add(
            ProjectRow(
                id="project-1",
                name="project-1",
                status="draft",
                revision=1,
                story_id=None,
                created_at=now,
                updated_at=now,
            )
        )
    package_path = (
        settings.data_dir
        / "models"
        / "packages"
        / KOKORO_LOCAL_ONNX_MANIFEST.package_id
        / KOKORO_LOCAL_ONNX_MANIFEST.package_version
    )
    manager = _FakeModelPackageManager(package_path)
    auditions = AuditionRepository(
        database,
        settings,
        model_package_manager=cast(ModelPackageManager, manager),
    )
    try:
        packages, _cursor, _total = auditions.list_model_packages(
            project_id="project-1",
            cursor=None,
            limit=MAX_AUDITION_PAGE_SIZE,
        )
        kokoro = next(
            item
            for item in packages
            if item["manifest"]["modelPackageId"] == KOKORO_LOCAL_ONNX_MANIFEST.package_id
        )["manifest"]
        assert kokoro["manifestFingerprint"] == KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
        assert kokoro["attributionRequirements"] == list(
            KOKORO_LOCAL_ONNX_MANIFEST.attribution_requirements
        )
        assert kokoro["requiredRuntimeDependencies"] == list(
            KOKORO_LOCAL_ONNX_MANIFEST.required_runtime_dependencies
        )
        assert kokoro["compatibilityConstraints"] == list(
            KOKORO_LOCAL_ONNX_MANIFEST.compatibility_constraints
        )
        assert set(kokoro["provenance"]) == {
            "origin",
            "producerId",
            "producerVersion",
            "recordedAt",
        }
        with database.session() as session:
            stored_manifest = session.scalar(
                select(ModelPackageManifestRow).where(
                    ModelPackageManifestRow.package_id == KOKORO_LOCAL_ONNX_MANIFEST.package_id
                )
            )
            assert stored_manifest is not None
            stored_provenance = parse_json(stored_manifest.provenance_json, {})
        assert isinstance(stored_provenance, dict)
        assert set(stored_provenance) == {
            "origin",
            "producerId",
            "producerVersion",
            "recordedAt",
            *KOKORO_LOCAL_ONNX_MANIFEST.provenance.to_dict(),
        }
        for key, value in KOKORO_LOCAL_ONNX_MANIFEST.provenance.to_dict().items():
            assert stored_provenance[key] == value
        archive = settings.data_dir / "model-staging" / "opaque-upload.zip"
        archive.write_bytes(b"private-staged-archive")
        installed = auditions.install_model_package(
            project_id="project-1",
            model_package_id=kokoro["modelPackageId"],
            request=InstallModelPackageRequest(
                expected_manifest_fingerprint=kokoro["manifestFingerprint"],
                expected_installation_revision=None,
                acknowledge_restricted_local_use=True,
                reason="Install the restricted local preview model.",
                idempotency_key="install-kokoro",
            ),
            archive_path=archive,
            actor_id="local_user",
        )
        assert installed["installation"]["status"] == "installed"
        assert installed["verification"]["status"] == "verified"
        assert "/" not in installed["installation"]["storageKey"]
        assert "models" not in installed["installation"]["storageKey"]

        activated = auditions.perform_model_package_action(
            project_id="project-1",
            request=ModelInstallationOperationRequest(
                model_package_id=kokoro["modelPackageId"],
                expected_manifest_fingerprint=kokoro["manifestFingerprint"],
                expected_installation_revision=1,
                action="activate",
                reason="Activate the installed restricted preview model.",
                idempotency_key="activate-kokoro",
            ),
            actor_id="local_user",
        )
        assert activated["installation"]["status"] == "active"
        health = next(
            item
            for item in auditions.workspace_snapshot("project-1")["runtimeHealth"]
            if item["providerId"] == "kokoro-local-onnx"
        )
        assert health["status"] == "unavailable"
        assert health["reasonCode"] == "INSTALLED_NOT_LIVE"
        assert health["runtimeInstanceId"] is None

        deactivated = auditions.perform_model_package_action(
            project_id="project-1",
            request=ModelInstallationOperationRequest(
                model_package_id=kokoro["modelPackageId"],
                expected_manifest_fingerprint=kokoro["manifestFingerprint"],
                expected_installation_revision=2,
                action="deactivate",
                reason="Deactivate before an explicit managed repair.",
                idempotency_key="deactivate-kokoro",
            ),
            actor_id="local_user",
        )
        assert deactivated["installation"]["status"] == "inactive"
        repaired = auditions.repair_model_package(
            project_id="project-1",
            model_package_id=kokoro["modelPackageId"],
            request=InstallModelPackageRequest(
                expected_manifest_fingerprint=kokoro["manifestFingerprint"],
                expected_installation_revision=3,
                acknowledge_restricted_local_use=True,
                reason="Repair from a newly staged exact archive.",
                idempotency_key="repair-kokoro",
            ),
            archive_path=archive,
            actor_id="local_user",
        )
        assert repaired["installation"]["status"] == "installed"
        assert repaired["installation"]["installationRevision"] == 4
        assert repaired["verification"]["status"] == "verified"
    finally:
        auditions.shutdown_runtimes()
        database.close()


def test_restart_rejects_tampered_persisted_manifest_projection(
    repository: tuple[AuditionRepository, Database, ServiceSettings],
) -> None:
    _auditions, database, settings = repository
    with database.session() as session:
        fixture = session.scalar(
            select(ModelPackageManifestRow).where(
                ModelPackageManifestRow.source_classification == "repository_fixture"
            )
        )
        assert fixture is not None
        fixture.official_source_reference = "repository://tampered"

    with pytest.raises(ServiceError) as error:
        AuditionRepository(database, settings)
    assert error.value.code == "MODEL_MANIFEST_CONFLICT"


def test_model_archive_rejects_lexical_symlink_inside_staging(
    repository: tuple[AuditionRepository, Database, ServiceSettings],
) -> None:
    auditions, _database, settings = repository
    staging_root = settings.data_dir / "model-staging"
    target = staging_root / "repository-owned-target.zip"
    linked_archive = staging_root / "repository-owned-link.zip"
    target.write_bytes(b"repository-owned synthetic archive")
    try:
        linked_archive.symlink_to(target.name)
    except OSError:
        pytest.skip("This Windows account cannot create a file symlink.")

    with pytest.raises(ServiceError) as error:
        auditions._validated_model_archive(linked_archive)
    assert error.value.code == "MODEL_ARCHIVE_INVALID"


def test_model_archive_rejects_lexical_reparse_attribute_before_resolution(
    repository: tuple[AuditionRepository, Database, ServiceSettings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auditions, _database, settings = repository
    archive = settings.data_dir / "model-staging" / "repository-owned-reparse.zip"
    archive.write_bytes(b"repository-owned synthetic archive")
    original_lstat = Path.lstat

    def reparse_lstat(path: Path) -> object:
        metadata = original_lstat(path)
        if path == archive:
            return SimpleNamespace(
                st_file_attributes=0x400,
                st_mode=metadata.st_mode,
                st_size=metadata.st_size,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(ServiceError) as error:
        auditions._validated_model_archive(archive)
    assert error.value.code == "MODEL_ARCHIVE_INVALID"
