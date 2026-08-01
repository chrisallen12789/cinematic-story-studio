from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cinematic_story_service.audition_repository import AuditionRepository
from cinematic_story_service.config import ServiceSettings
from cinematic_story_service.database import Database
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    ModelPackageError,
    ModelPackageManager,
    ModelPackageVerification,
    StagedModelPackageRemoval,
)
from cinematic_story_service.models import (
    ModelInstallationRow,
    ModelPackageManifestRow,
    ProjectRow,
)
from cinematic_story_service.schemas import (
    InstallModelPackageRequest,
    ModelInstallationOperationRequest,
)
from cinematic_story_service.util import canonical_json, new_id, stable_id, utc_now


class _FilesystemModelManager:
    """Small exact-state double for repository transaction-boundary tests."""

    def __init__(self, package_path: Path) -> None:
        self.package_path = package_path
        self.active = False

    def _verification(self) -> ModelPackageVerification:
        return ModelPackageVerification(
            package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            package_version=KOKORO_LOCAL_ONNX_MANIFEST.package_version,
            package_path=self.package_path,
            manifest_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
            valid=self.package_path.is_dir(),
            error_codes=(),
            verified_files=tuple(
                artifact.path for artifact in KOKORO_LOCAL_ONNX_MANIFEST.artifacts
            ),
            total_size_bytes=KOKORO_LOCAL_ONNX_MANIFEST.total_size_bytes,
        )

    def install_from_archive(
        self,
        _archive: Path,
        _manifest: object,
    ) -> ModelPackageVerification:
        self.package_path.mkdir(parents=True)
        (self.package_path / "owned-model.bin").write_bytes(b"owned-model")
        return self._verification()

    def verify(self, _manifest: object) -> ModelPackageVerification:
        return self._verification()

    def activate(self, _manifest: object) -> Path:
        if not self.package_path.is_dir():
            raise ModelPackageError("MODEL_PACKAGE_MISSING", "The package is missing.")
        self.active = True
        return self.package_path

    def deactivate(self) -> bool:
        was_active = self.active
        self.active = False
        return was_active

    def stage_remove(self, _manifest: object) -> StagedModelPackageRemoval | None:
        if self.active:
            raise ModelPackageError("MODEL_PACKAGE_ACTIVE", "The package is active.")
        if not self.package_path.exists():
            return None
        tombstone = self.package_path.parent.parent / (
            f".removing-{KOKORO_LOCAL_ONNX_MANIFEST.fingerprint}-{'1' * 32}.pending"
        )
        os.replace(self.package_path, tombstone)
        return StagedModelPackageRemoval(
            manifest_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
            original_path=self.package_path,
            tombstone_path=tombstone,
        )

    def rollback_staged_removal(
        self,
        staged: StagedModelPackageRemoval,
        _manifest: object,
    ) -> None:
        os.replace(staged.tombstone_path, staged.original_path)

    def commit_staged_removal(
        self,
        staged: StagedModelPackageRemoval,
        _manifest: object,
    ) -> None:
        (staged.tombstone_path / "owned-model.bin").unlink()
        staged.tombstone_path.rmdir()

    def remove(self, manifest: object) -> bool:
        staged = self.stage_remove(manifest)
        if staged is None:
            return False
        self.commit_staged_removal(staged, manifest)
        return True


def _model_request(
    *,
    action: str,
    revision: int,
    key: str,
) -> ModelInstallationOperationRequest:
    return ModelInstallationOperationRequest.model_validate(
        {
            "modelPackageId": KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            "expectedManifestFingerprint": KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
            "expectedInstallationRevision": revision,
            "action": action,
            "reason": f"Exercise {action} commit compensation.",
            "idempotencyKey": key,
        }
    )


def test_model_install_idempotency_binds_the_exact_archive_bytes(tmp_path: Path) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "private-data",
        bearer_token="ab" * 32,
        worker_enabled=False,
    ).validated()
    database = Database(settings.database_path)
    with database.session() as session:
        session.add(
            ProjectRow(
                id="project-1",
                name="project-1",
                status="draft",
                revision=1,
                story_id=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    repository = AuditionRepository(database, settings)
    package_path = (
        settings.data_dir
        / "models"
        / "packages"
        / KOKORO_LOCAL_ONNX_MANIFEST.package_id
        / KOKORO_LOCAL_ONNX_MANIFEST.package_version
    )
    repository._model_package_manager = cast(
        ModelPackageManager,
        _FilesystemModelManager(package_path),
    )
    first_archive = settings.data_dir / "model-staging" / "first-owned-upload.zip"
    second_archive = settings.data_dir / "model-staging" / "second-owned-upload.zip"
    first_archive.write_bytes(b"repository-owned model archive one")
    second_archive.write_bytes(b"repository-owned model archive two")
    request = InstallModelPackageRequest(
        expected_manifest_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
        expected_installation_revision=None,
        acknowledge_restricted_local_use=True,
        reason="Bind the exact local package archive to idempotency.",
        idempotency_key="install-exact-archive",
    )

    try:
        installed = repository.install_model_package(
            project_id="project-1",
            model_package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            request=request,
            archive_path=first_archive,
            actor_id="local_user",
        )
        assert installed["installation"]["installationRevision"] == 1
        replayed = repository.install_model_package(
            project_id="project-1",
            model_package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            request=request,
            archive_path=first_archive,
            actor_id="local_user",
        )
        assert replayed == installed

        with pytest.raises(ServiceError) as error:
            repository.install_model_package(
                project_id="project-1",
                model_package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
                request=request,
                archive_path=second_archive,
                actor_id="local_user",
            )
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
        with database.session() as session:
            rows = list(session.scalars(select(ModelInstallationRow)))
        assert len(rows) == 1
    finally:
        repository.shutdown_runtimes()
        database.close()


def test_model_filesystem_mutations_compensate_database_commit_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "private-data",
        bearer_token="ab" * 32,
        worker_enabled=False,
    ).validated()
    database = Database(settings.database_path)
    with database.session() as session:
        session.add(
            ProjectRow(
                id="project-1",
                name="project-1",
                status="draft",
                revision=1,
                story_id=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    repository = AuditionRepository(database, settings)
    package_path = (
        settings.data_dir
        / "models"
        / "packages"
        / KOKORO_LOCAL_ONNX_MANIFEST.package_id
        / KOKORO_LOCAL_ONNX_MANIFEST.package_version
    )
    manager = _FilesystemModelManager(package_path)
    repository._model_package_manager = cast(ModelPackageManager, manager)
    archive = settings.data_dir / "model-staging" / "owned-upload.zip"
    archive.write_bytes(b"repository-owned model archive")

    original_commit = Session.commit
    failure_key: str | None = None
    observed_filesystem: list[tuple[str, bool, bool, int]] = []

    def fail_selected_model_commit(database_session: Session) -> None:
        nonlocal failure_key
        row = next(
            (
                value
                for value in database_session.identity_map.values()
                if isinstance(value, ModelInstallationRow) and value.idempotency_key == failure_key
            ),
            None,
        )
        if row is not None and failure_key is not None:
            observed_filesystem.append(
                (
                    row.operation,
                    manager.active,
                    package_path.exists(),
                    len(list(package_path.parent.parent.glob(".removing-*.pending"))),
                )
            )
            failure_key = None
            raise RuntimeError(f"injected {row.operation} commit failure")
        original_commit(database_session)

    monkeypatch.setattr(Session, "commit", fail_selected_model_commit)

    def latest_installation() -> ModelInstallationRow | None:
        with database.session() as session:
            return session.scalar(
                select(ModelInstallationRow).order_by(ModelInstallationRow.revision.desc()).limit(1)
            )

    try:
        failure_key = "install-failure"
        with pytest.raises(RuntimeError, match="injected install commit failure"):
            repository.install_model_package(
                project_id="project-1",
                model_package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
                request=InstallModelPackageRequest(
                    expected_manifest_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
                    expected_installation_revision=None,
                    acknowledge_restricted_local_use=True,
                    reason="Exercise install commit compensation.",
                    idempotency_key="install-failure",
                ),
                archive_path=archive,
                actor_id="local_user",
            )
        assert latest_installation() is None
        assert not package_path.exists()
        assert manager.active is False

        installed = repository.install_model_package(
            project_id="project-1",
            model_package_id=KOKORO_LOCAL_ONNX_MANIFEST.package_id,
            request=InstallModelPackageRequest(
                expected_manifest_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
                expected_installation_revision=None,
                acknowledge_restricted_local_use=True,
                reason="Establish a package for action compensation.",
                idempotency_key="install-success",
            ),
            archive_path=archive,
            actor_id="local_user",
        )
        assert installed["installation"]["installationRevision"] == 1

        failure_key = "activate-failure"
        with pytest.raises(RuntimeError, match="injected activate commit failure"):
            repository.perform_model_package_action(
                project_id="project-1",
                request=_model_request(
                    action="activate",
                    revision=1,
                    key="activate-failure",
                ),
                actor_id="local_user",
            )
        assert manager.active is False
        assert latest_installation().state == "installed"  # type: ignore[union-attr]

        repository.perform_model_package_action(
            project_id="project-1",
            request=_model_request(
                action="activate",
                revision=1,
                key="activate-success",
            ),
            actor_id="local_user",
        )
        assert manager.active is True

        failure_key = "deactivate-failure"
        with pytest.raises(RuntimeError, match="injected deactivate commit failure"):
            repository.perform_model_package_action(
                project_id="project-1",
                request=_model_request(
                    action="deactivate",
                    revision=2,
                    key="deactivate-failure",
                ),
                actor_id="local_user",
            )
        assert manager.active is True
        assert latest_installation().state == "active"  # type: ignore[union-attr]

        repository.perform_model_package_action(
            project_id="project-1",
            request=_model_request(
                action="deactivate",
                revision=2,
                key="deactivate-success",
            ),
            actor_id="local_user",
        )
        assert manager.active is False

        failure_key = "remove-failure"
        with pytest.raises(RuntimeError, match="injected remove commit failure"):
            repository.perform_model_package_action(
                project_id="project-1",
                request=_model_request(
                    action="remove",
                    revision=3,
                    key="remove-failure",
                ),
                actor_id="local_user",
            )
        assert package_path.is_dir()
        assert list(package_path.parent.parent.glob(".removing-*.pending")) == []
        assert latest_installation().state == "inactive"  # type: ignore[union-attr]

        removed = repository.perform_model_package_action(
            project_id="project-1",
            request=_model_request(
                action="remove",
                revision=3,
                key="remove-success",
            ),
            actor_id="local_user",
        )
        assert removed["installation"]["status"] == "removed"
        assert not package_path.exists()
        assert list(package_path.parent.parent.glob(".removing-*.pending")) == []
        assert observed_filesystem == [
            ("install", False, True, 0),
            ("activate", True, True, 0),
            ("deactivate", False, True, 0),
            ("remove", False, False, 1),
        ]
    finally:
        repository.shutdown_runtimes()
        database.close()


def test_startup_reconciles_both_staged_model_removal_commit_windows(
    tmp_path: Path,
) -> None:
    settings = ServiceSettings(
        data_dir=tmp_path / "private-data",
        bearer_token="ab" * 32,
        worker_enabled=False,
    ).validated()
    database = Database(settings.database_path)
    initial = AuditionRepository(database, settings)
    package_path = initial._model_package_manager.package_path(KOKORO_LOCAL_ONNX_MANIFEST)
    package_path.mkdir(parents=True)
    (package_path / "owned-model.bin").write_bytes(b"owned-model")
    installation_id = stable_id(
        "phase3b-installation",
        KOKORO_LOCAL_ONNX_MANIFEST.package_id,
    )
    now = utc_now()
    with database.session() as session:
        manifest = session.scalar(
            select(ModelPackageManifestRow).where(
                ModelPackageManifestRow.manifest_fingerprint
                == KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
            )
        )
        assert manifest is not None
        inactive = ModelInstallationRow(
            id=new_id(),
            installation_id=installation_id,
            manifest_id=manifest.id,
            revision=1,
            operation="deactivate",
            state="inactive",
            storage_key=package_path.relative_to(settings.data_dir).as_posix(),
            installed_byte_count=KOKORO_LOCAL_ONNX_MANIFEST.total_size_bytes,
            package_fingerprint=KOKORO_LOCAL_ONNX_MANIFEST.fingerprint,
            job_id=None,
            supersedes_installation_record_id=None,
            actor_id="local_user",
            reason="Establish the pre-commit removal state.",
            idempotency_key="startup-removal-inactive",
            warnings_json="[]",
            provenance_json=canonical_json({"origin": "test"}),
            created_at=now,
            completed_at=now,
        )
        session.add(inactive)

    pre_commit = initial._model_package_manager.stage_remove(KOKORO_LOCAL_ONNX_MANIFEST)
    assert pre_commit is not None
    assert not package_path.exists()
    assert pre_commit.tombstone_path.is_dir()
    initial.shutdown_runtimes()

    restored = AuditionRepository(database, settings)
    assert package_path.is_dir()
    assert (package_path / "owned-model.bin").read_bytes() == b"owned-model"
    assert not pre_commit.tombstone_path.exists()

    post_commit = restored._model_package_manager.stage_remove(KOKORO_LOCAL_ONNX_MANIFEST)
    assert post_commit is not None
    with database.session() as session:
        manifest = session.scalar(
            select(ModelPackageManifestRow).where(
                ModelPackageManifestRow.manifest_fingerprint
                == KOKORO_LOCAL_ONNX_MANIFEST.fingerprint
            )
        )
        prior = session.scalar(
            select(ModelInstallationRow).where(
                ModelInstallationRow.installation_id == installation_id,
                ModelInstallationRow.revision == 1,
            )
        )
        assert manifest is not None
        assert prior is not None
        session.add(
            ModelInstallationRow(
                id=new_id(),
                installation_id=installation_id,
                manifest_id=manifest.id,
                revision=2,
                operation="remove",
                state="removed",
                storage_key=prior.storage_key,
                installed_byte_count=prior.installed_byte_count,
                package_fingerprint=prior.package_fingerprint,
                job_id=None,
                supersedes_installation_record_id=prior.id,
                actor_id="local_user",
                reason="Establish the post-commit removal state.",
                idempotency_key="startup-removal-removed",
                warnings_json="[]",
                provenance_json=canonical_json({"origin": "test"}),
                created_at=utc_now(),
                completed_at=utc_now(),
            )
        )
    restored.shutdown_runtimes()

    finalized = AuditionRepository(database, settings)
    try:
        assert not package_path.exists()
        assert not post_commit.tombstone_path.exists()
        assert finalized._model_package_manager.staged_removals(KOKORO_LOCAL_ONNX_MANIFEST) == ()
    finally:
        finalized.shutdown_runtimes()
        database.close()
