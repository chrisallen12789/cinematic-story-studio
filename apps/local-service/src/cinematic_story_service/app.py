from __future__ import annotations

import hmac
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, BinaryIO, Literal, cast

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError as PydanticValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.types import Message, Receive

from .audition_repository import (
    DEFAULT_AUDITION_PAGE_SIZE,
    MAX_AUDITION_PAGE_SIZE,
    AuditionRepository,
)
from .auditions import MAX_AUDITION_AUDIO_BYTES
from .casting import DEFAULT_CASTING_PAGE_SIZE, MAX_CASTING_PAGE_SIZE
from .casting_repository import CastingRepository
from .config import ServiceSettings
from .database import Database
from .errors import ServiceError
from .jobs import JobRepository, JobWorker
from .model_packages import (
    KOKORO_LOCAL_ONNX_MANIFEST,
    MAX_MANAGED_MODEL_DIRECTORY_ENTRIES,
    ModelPackageError,
)
from .projects import ProjectRepository, StoryImportService
from .providers import ProviderRegistry
from .schemas import (
    AppendAnalysisCorrectionRequest,
    AppendCastingCorrectionRequest,
    ClearAuditionCacheRequest,
    CorrectDialogueSpeakerRequest,
    CreateAnalysisRunRequest,
    CreateAuditionScriptRequest,
    CreateAuditionSessionRequest,
    CreateCastingRunRequest,
    CreateCustomProductionRoleRequest,
    CreateJobRequest,
    CreateProjectRequest,
    CreatePronunciationEntryRequest,
    DecideAnalysisReviewRequest,
    DecideAuditionReviewRequest,
    DecideCastingReviewRequest,
    DecideImportReviewRequest,
    DecidePronunciationEntryRequest,
    GenerateAuditionRequest,
    InstallModelPackageRequest,
    ListAuditionReviewDecisionsQuery,
    ModelInstallationOperationRequest,
    PreviewNormalizationRequest,
)
from .story_intelligence import StoryIntelligenceRepository
from .tools import FfmpegCapabilityChecker
from .util import (
    PROTOCOL_VERSION,
    SERVICE_VERSION,
    ensure_private_directory,
    new_id,
    resolve_beneath,
    utc_now,
)
from .whole_book_analysis import (
    DEFAULT_ANALYSIS_PAGE_SIZE,
    MAX_ANALYSIS_PAGE_SIZE,
)

_LOGGER = logging.getLogger("cinematic_story_service")
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}
_MULTIPART_OVERHEAD_ALLOWANCE = 64 * 1024
_MUTATION_BODY_LIMIT = 64 * 1024
_LOCAL_ACTOR_ID = "local_user"
_MAX_MODEL_PACKAGE_UPLOAD_BYTES = KOKORO_LOCAL_ONNX_MANIFEST.total_size_bytes + (1024 * 1024)
_MODEL_STAGING_ARCHIVE_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.zip$"
)
_REPARSE_POINT_ATTRIBUTE = 0x400
_PHASE_2_MUTATION_PATH = re.compile(
    r"^/api/v1/projects/[^/]+/analysis-runs"
    r"(?:$|/[^/]+/(?:corrections|reviews/[^/]+/decisions)$)"
)
_PHASE_3_MUTATION_PATH = re.compile(
    r"^/api/v1/projects/[^/]+/casting-runs"
    r"(?:$|/[^/]+/(?:roles|corrections|reviews/[^/]+/decisions)$)"
)
_PHASE_3B_MUTATION_PATHS = (
    re.compile(r"^/api/v1/projects/[^/]+/speech/model-packages/[^/]+/actions$"),
    re.compile(r"^/api/v1/projects/[^/]+/pronunciations/entries(?:/[^/]+/decisions)?$"),
    re.compile(
        r"^/api/v1/projects/[^/]+/audition-sessions"
        r"(?:$|/[^/]+/(?:scripts|normalization-preview|generate)$)"
    ),
    re.compile(r"^/api/v1/projects/[^/]+/audition-reviews/[^/]+/[^/]+/decisions$"),
    re.compile(r"^/api/v1/projects/[^/]+/audition-cache/clear$"),
)


class _BodyLimitExceeded(MultiPartException):
    def __init__(self) -> None:
        super().__init__("request_body_too_large")


class _CappedReceive:
    def __init__(self, receive: Receive, limit: int) -> None:
        self.receive = receive
        self.limit = limit
        self.received = 0

    async def __call__(self) -> Message:
        message = await self.receive()
        if message["type"] == "http.request":
            self.received += len(message.get("body", b""))
            if self.received > self.limit:
                raise _BodyLimitExceeded
        return message


class _PrivateMultiPartParser(MultiPartParser):
    """Keep Starlette's rollover files inside application-owned private storage."""

    def __init__(self, *args: Any, spool_directory: Path, **kwargs: Any) -> None:
        self._spool_directory = spool_directory
        super().__init__(*args, **kwargs)

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        upload = self._current_part.file
        if upload is None:
            return
        public_spool = upload.file
        if not self._files_to_close_on_error or (
            id(self._files_to_close_on_error[-1]) != id(public_spool)
        ):
            raise MultiPartException("The upload staging state is invalid.")
        private_spool = SpooledTemporaryFile(
            max_size=self.spool_max_size,
            dir=str(self._spool_directory),
        )
        public_spool.close()
        self._files_to_close_on_error[-1] = private_spool
        self._current_part.file = StarletteUploadFile(
            file=cast(BinaryIO, private_spool),
            size=0,
            filename=upload.filename,
            headers=upload.headers,
        )


async def _bounded_import_form(
    request: Request,
    max_import_bytes: int,
    spool_directory: Path,
    *,
    max_fields: int = 2,
) -> Any:
    body_limit = max_import_bytes + _MULTIPART_OVERHEAD_ALLOWANCE
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is not None:
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise ServiceError(
                400,
                "INVALID_CONTENT_LENGTH",
                "The request content length is invalid.",
            ) from exc
        if content_length < 0:
            raise ServiceError(
                400,
                "INVALID_CONTENT_LENGTH",
                "The request content length is invalid.",
            )
        if content_length > body_limit:
            raise _BodyLimitExceeded

    original_receive = request._receive
    request._receive = _CappedReceive(original_receive, body_limit)
    try:
        try:
            parser = _PrivateMultiPartParser(
                request.headers,
                request.stream(),
                spool_directory=spool_directory,
                max_files=1,
                max_fields=max_fields,
                max_part_size=min(max_import_bytes + 1024, 101 * 1024 * 1024),
            )
            return await parser.parse()
        except StarletteHttpException as exc:
            if exc.status_code == 400 and exc.detail == "request_body_too_large":
                raise _BodyLimitExceeded from exc
            raise
    finally:
        request._receive = original_receive


def _stage_private_model_archive(
    upload: StarletteUploadFile,
    staging_directory: Path,
) -> Path:
    if Path(upload.filename or "").suffix.casefold() != ".zip":
        raise ServiceError(
            422,
            "MODEL_PACKAGE_ARCHIVE_REQUIRED",
            "The selected local model package must be a ZIP archive.",
        )
    staged = resolve_beneath(staging_directory, f"{new_id()}.zip")
    written = 0
    try:
        upload.file.seek(0)
        with staged.open("xb") as destination:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_MODEL_PACKAGE_UPLOAD_BYTES:
                    raise ServiceError(
                        413,
                        "MODEL_PACKAGE_TOO_LARGE",
                        "The local model package exceeded its fixed upload bound.",
                    )
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if written == 0:
            raise ServiceError(
                422,
                "MODEL_PACKAGE_ARCHIVE_EMPTY",
                "The selected local model package was empty.",
            )
        try:
            os.chmod(staged, 0o600)
        except OSError:
            pass
        return staged
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _reconcile_model_staging(staging_directory: Path) -> int:
    root = staging_directory.resolve(strict=True)
    entries: list[Path] = []
    try:
        with os.scandir(root) as scanner:
            for entry in scanner:
                if len(entries) >= MAX_MANAGED_MODEL_DIRECTORY_ENTRIES:
                    raise ModelPackageError(
                        "MODEL_PACKAGE_ENTRY_LIMIT",
                        "The model staging directory exceeded its fixed entry bound.",
                    )
                candidate = Path(entry.path)
                entries.append(candidate)
    except ModelPackageError:
        raise
    except OSError as exc:
        raise ModelPackageError(
            "MODEL_PACKAGE_IO_ERROR",
            "The model staging directory could not be inspected.",
        ) from exc

    removable: list[Path] = []
    for candidate in entries:
        if _MODEL_STAGING_ARCHIVE_PATTERN.fullmatch(candidate.name) is not None:
            try:
                metadata = candidate.lstat()
            except OSError:
                continue
            if (
                not candidate.is_file()
                or candidate.is_symlink()
                or int(getattr(metadata, "st_file_attributes", 0)) & _REPARSE_POINT_ATTRIBUTE
            ):
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved.parent == root:
                removable.append(resolved)

    removed = 0
    for resolved in removable:
        try:
            resolved.unlink()
        except OSError:
            continue
        removed += 1
    return removed


async def _bounded_mutation_json_call(
    request: Request,
    call_next: Any,
) -> Any:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    applies = (
        request.method in {"POST", "PUT", "PATCH"}
        and content_type == "application/json"
        and (
            _PHASE_2_MUTATION_PATH.fullmatch(request.url.path) is not None
            or _PHASE_3_MUTATION_PATH.fullmatch(request.url.path) is not None
            or any(pattern.fullmatch(request.url.path) for pattern in _PHASE_3B_MUTATION_PATHS)
        )
    )
    if not applies:
        return await call_next(request)
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is not None:
        try:
            content_length = int(raw_content_length)
        except ValueError:
            return _error_response(
                request,
                ServiceError(
                    400,
                    "INVALID_CONTENT_LENGTH",
                    "The request content length is invalid.",
                ),
            )
        if content_length < 0:
            return _error_response(
                request,
                ServiceError(
                    400,
                    "INVALID_CONTENT_LENGTH",
                    "The request content length is invalid.",
                ),
            )
        if content_length > _MUTATION_BODY_LIMIT:
            return _error_response(
                request,
                ServiceError(
                    413,
                    "REQUEST_BODY_TOO_LARGE",
                    "The mutation body exceeds 64 KiB.",
                ),
            )
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > _MUTATION_BODY_LIMIT:
            return _error_response(
                request,
                ServiceError(
                    413,
                    "REQUEST_BODY_TOO_LARGE",
                    "The mutation body exceeds 64 KiB.",
                ),
            )
        chunks.append(chunk)
    request._body = b"".join(chunks)
    return await call_next(request)


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", new_id())


def _error_response(request: Request, error: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.envelope(_correlation_id(request)),
    )


def _validated_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not 1 <= len(value) <= 160 or any(ord(character) < 33 for character in value):
        raise ServiceError(
            400,
            "INVALID_IDEMPOTENCY_KEY",
            "The idempotency key is invalid.",
        )
    return value


def create_app(settings: ServiceSettings) -> FastAPI:
    settings = settings.validated()
    database = Database(settings.database_path)
    multipart_spool_directory = ensure_private_directory(settings.data_dir / "multipart-staging")
    model_staging_directory = ensure_private_directory(settings.data_dir / "model-staging")
    reconciled_model_staging = _reconcile_model_staging(model_staging_directory)
    if reconciled_model_staging:
        _LOGGER.info(
            "reconciled_model_staging count=%d",
            reconciled_model_staging,
        )
    projects = ProjectRepository(database)
    story_intelligence = StoryIntelligenceRepository(database, projects)
    casting = CastingRepository(database, projects, story_intelligence)
    imports = StoryImportService(settings, projects)
    reconciled_staging = imports.reconcile_staging()
    if reconciled_staging:
        _LOGGER.info(
            "reconciled_import_staging count=%d",
            reconciled_staging,
        )
    jobs = JobRepository(
        database,
        projects,
        settings.instance_id,
        settings.parser_deadline_seconds,
        story_intelligence=story_intelligence,
        casting=casting,
    )
    auditions = AuditionRepository(
        database,
        settings,
        story_intelligence=story_intelligence,
    )
    jobs.set_audition_terminal_handler(auditions.mark_job_terminal)
    jobs.set_audition_publication_handler(auditions.publish_generation_result)
    jobs.reconcile_interrupted()
    jobs.reconcile_orphaned_extractions()
    worker = JobWorker(
        settings,
        jobs,
        projects,
        audition_runner=auditions.run_generation_job,
    )
    providers = ProviderRegistry(settings)
    ffmpeg = FfmpegCapabilityChecker(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.worker_enabled:
            worker.start()
        try:
            yield
        finally:
            auditions.begin_runtime_shutdown()
            worker_quiesced = not settings.worker_enabled
            initial_worker_error: Exception | None = None
            if settings.worker_enabled:
                try:
                    worker.stop()
                    worker_quiesced = True
                except Exception as exc:
                    initial_worker_error = exc
            runtime_shutdown_error: Exception | None = None
            try:
                auditions.shutdown_runtimes()
            except Exception as exc:
                runtime_shutdown_error = exc
            second_worker_drain = False
            if not worker_quiesced:
                try:
                    worker.stop()
                    worker_quiesced = True
                    second_worker_drain = True
                except Exception as exc:
                    cause = runtime_shutdown_error or initial_worker_error
                    if cause is not None:
                        raise exc from cause
                    raise
            if runtime_shutdown_error is not None or second_worker_drain:
                try:
                    auditions.shutdown_runtimes()
                    runtime_shutdown_error = None
                except Exception as exc:
                    if runtime_shutdown_error is not None:
                        raise exc from runtime_shutdown_error
                    raise
            database.close()

    app = FastAPI(
        title="Cinematic Story Service",
        version=SERVICE_VERSION,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.projects = projects
    app.state.story_intelligence = story_intelligence
    app.state.casting = casting
    app.state.imports = imports
    app.state.jobs = jobs
    app.state.auditions = auditions
    app.state.worker = worker
    app.state.providers = providers
    app.state.ffmpeg = ffmpeg

    @app.middleware("http")
    async def security_boundary(request: Request, call_next: Any) -> JSONResponse:
        request.state.correlation_id = new_id()
        host_header = request.headers.get("host", "")
        host = host_header.rsplit(":", 1)[0].casefold() if host_header else ""
        if host not in _ALLOWED_HOSTS:
            response = _error_response(
                request,
                ServiceError(
                    400,
                    "INVALID_HOST",
                    "The local service accepts loopback requests only.",
                ),
            )
        elif request.url.path == "/api/v1" or request.url.path.startswith("/api/v1/"):
            authorization = request.headers.get("authorization", "")
            scheme, separator, supplied_token = authorization.partition(" ")
            authenticated = (
                separator == " "
                and scheme.casefold() == "bearer"
                and bool(supplied_token)
                and hmac.compare_digest(supplied_token, settings.bearer_token)
            )
            if not authenticated:
                response = _error_response(
                    request,
                    ServiceError(
                        401,
                        "AUTHENTICATION_REQUIRED",
                        "Valid launch authentication is required.",
                    ),
                )
                response.headers["WWW-Authenticate"] = "Bearer"
            else:
                response = await _bounded_mutation_json_call(request, call_next)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        return _error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            ServiceError(
                422,
                "INVALID_REQUEST",
                "The request did not match the expected contract.",
            ),
        )

    @app.exception_handler(StarletteHttpException)
    async def http_error_handler(request: Request, exc: StarletteHttpException) -> JSONResponse:
        if exc.status_code == 404:
            error = ServiceError(404, "RESOURCE_NOT_FOUND", "The requested resource was not found.")
        elif exc.status_code == 405:
            error = ServiceError(405, "METHOD_NOT_ALLOWED", "That method is not allowed.")
        else:
            error = ServiceError(
                exc.status_code,
                "HTTP_REQUEST_FAILED",
                "The request could not be completed.",
            )
        return _error_response(request, error)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _exc: Exception) -> JSONResponse:
        correlation_id = _correlation_id(request)
        _LOGGER.error(
            "unhandled_service_error correlation_id=%s component=api",
            correlation_id,
        )
        return _error_response(
            request,
            ServiceError(
                500,
                "INTERNAL_ERROR",
                "The local service could not complete the request.",
                retryable=True,
            ),
        )

    @app.get("/api/v1/health")
    def health(request: Request) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "status": "ready",
            "serviceVersion": SERVICE_VERSION,
            "contractVersion": PROTOCOL_VERSION,
            "instanceId": settings.instance_id,
            "database": {"status": "ready"},
            "worker": {
                "status": "ready" if settings.worker_enabled else "disabled",
            },
            "checkedAt": utc_now(),
        }

    @app.get("/api/v1/providers/health")
    def provider_health(request: Request) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "providers": providers.health(),
        }

    @app.get("/api/v1/capabilities/ffmpeg")
    def ffmpeg_capability(request: Request) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            **ffmpeg.check(),
        }

    @app.get("/api/v1/projects")
    def list_projects(
        request: Request,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        items, next_cursor = projects.list_projects(cursor=cursor, limit=limit)
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "items": items,
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.post("/api/v1/projects")
    def create_project(
        request: Request,
        body: CreateProjectRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        project = projects.create_project(
            name=body.name,
            idempotency_key=_validated_idempotency_key(idempotency_key),
        )
        return {
            "correlationId": _correlation_id(request),
            "project": project,
        }

    @app.get("/api/v1/projects/{project_id}")
    def project_detail(request: Request, project_id: str) -> dict[str, Any]:
        detail = projects.get_project_detail(project_id)
        summary = story_intelligence.project_summary(project_id)
        casting_summary = casting.project_summary(project_id)
        current_run = summary["currentRun"]
        return {
            "correlationId": _correlation_id(request),
            **detail,
            "currentAnalysisRun": current_run,
            "analysisGateReviews": (
                story_intelligence.list_reviews(
                    project_id=project_id,
                    run_id=current_run["runId"],
                )
                if isinstance(current_run, dict)
                else []
            ),
            "wholeBookAnalysis": summary,
            "voiceCasting": casting_summary,
            "currentCastingRun": casting_summary["currentRun"],
            "castingGateReviews": casting_summary["gateReviews"],
        }

    @app.post("/api/v1/projects/{project_id}/imports", status_code=202)
    async def import_story(
        request: Request,
        project_id: str,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        try:
            form = await _bounded_import_form(
                request,
                settings.max_import_bytes,
                multipart_spool_directory,
            )
        except _BodyLimitExceeded as exc:
            raise ServiceError(
                413,
                "IMPORT_TOO_LARGE",
                "The source exceeds the configured import size limit.",
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                400,
                "MALFORMED_MULTIPART",
                "The multipart import request is malformed.",
            ) from exc
        try:
            if any(key not in {"file", "declaredFormat"} for key in form):
                raise ServiceError(
                    422,
                    "INVALID_REQUEST",
                    "The multipart request contains an unknown field.",
                )
            upload = form.get("file")
            if not isinstance(upload, StarletteUploadFile):
                raise ServiceError(422, "SOURCE_FILE_REQUIRED", "A source file is required.")
            declared_value = form.get("declaredFormat")
            if declared_value is not None and not isinstance(declared_value, str):
                raise ServiceError(422, "INVALID_REQUEST", "The declared format is invalid.")
            result = await imports.import_upload(
                project_id=project_id,
                upload=upload,
                declared_format=declared_value,
                idempotency_key=_validated_idempotency_key(idempotency_key),
            )
        finally:
            await form.close()
        extraction_id = str(result.extraction["extractionId"])
        extraction_revision = int(result.extraction["revision"])
        input_fingerprint = str(result.extraction["sourceSha256"])
        job = jobs.create_extraction_job(
            project_id=project_id,
            extraction_id=extraction_id,
            input_revision=extraction_revision,
            input_fingerprint=input_fingerprint,
            idempotency_key=(
                _validated_idempotency_key(idempotency_key) or f"import-{extraction_id}"
            ),
        )
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "sourceDocument": result.source_document,
            "extraction": result.extraction,
            "job": job,
        }

    @app.get("/api/v1/projects/{project_id}/imports/{review_id}/review")
    def import_review(
        request: Request,
        project_id: str,
        review_id: str,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "review": projects.get_import_review(
                project_id=project_id,
                review_id=review_id,
            ),
        }

    @app.post("/api/v1/projects/{project_id}/imports/{review_id}/review/decision")
    def decide_import_review(
        request: Request,
        project_id: str,
        review_id: str,
        body: DecideImportReviewRequest,
    ) -> dict[str, Any]:
        if body.review_id != review_id:
            raise ServiceError(
                422,
                "IMPORT_REVIEW_ID_MISMATCH",
                "The Import Review identifier does not match the request path.",
            )
        review, decision, project_revision, analysis_allowed = projects.decide_import_review(
            project_id=project_id,
            review_id=review_id,
            decision=body.decision,
            rationale=body.rationale,
            expected_revision=body.expected_revision,
            evidence_fingerprint=body.evidence_fingerprint,
            idempotency_key=body.idempotency_key,
        )
        return {
            "correlationId": _correlation_id(request),
            "review": review,
            "decision": decision,
            "projectRevision": project_revision,
            "analysisAllowed": analysis_allowed,
        }

    @app.post(
        "/api/v1/projects/{project_id}/imports/{source_document_id}/reextract",
        status_code=202,
    )
    def reextract_import(
        request: Request,
        project_id: str,
        source_document_id: str,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        validated_idempotency_key = _validated_idempotency_key(idempotency_key)
        extraction = projects.create_reextraction(
            project_id=project_id,
            source_document_id=source_document_id,
            idempotency_key=validated_idempotency_key,
        )
        job = jobs.create_extraction_job(
            project_id=project_id,
            extraction_id=str(extraction["extractionId"]),
            input_revision=int(extraction["revision"]),
            input_fingerprint=str(extraction["sourceSha256"]),
            idempotency_key=(
                validated_idempotency_key or f"reextract-{extraction['extractionId']}"
            ),
        )
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "extraction": extraction,
            "job": job,
        }

    @app.post("/api/v1/projects/{project_id}/analysis-runs", status_code=202)
    def create_analysis_run(
        request: Request,
        project_id: str,
        body: CreateAnalysisRunRequest,
    ) -> dict[str, Any]:
        run, job = jobs.create_whole_book_run(
            project_id=project_id,
            expected_extraction_id=body.expected_extraction_id,
            expected_extraction_revision=body.expected_extraction_revision,
            expected_review_id=body.expected_review_id,
            expected_review_revision=body.expected_review_revision,
            expected_evidence_fingerprint=body.expected_evidence_fingerprint,
            expected_profile_fingerprint=body.expected_profile_fingerprint,
            idempotency_key=body.idempotency_key,
        )
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "run": run,
            "job": job,
        }

    @app.get("/api/v1/projects/{project_id}/analysis-runs")
    def list_analysis_runs(
        request: Request,
        project_id: str,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_ANALYSIS_PAGE_SIZE),
        ] = DEFAULT_ANALYSIS_PAGE_SIZE,
    ) -> dict[str, Any]:
        runs, next_cursor, total = story_intelligence.list_runs(
            project_id=project_id,
            cursor=cursor,
            limit=limit,
        )
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "pageSize": len(runs),
            "total": total,
            "runs": runs,
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.get("/api/v1/projects/{project_id}/analysis-runs/{run_id}")
    def get_analysis_run(
        request: Request,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "run": story_intelligence.get_run(
                project_id=project_id,
                run_id=run_id,
            ),
        }

    @app.get("/api/v1/projects/{project_id}/analysis-runs/{run_id}/entities/{collection}")
    def list_analysis_entities(
        request: Request,
        project_id: str,
        run_id: str,
        collection: str,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_ANALYSIS_PAGE_SIZE),
        ] = DEFAULT_ANALYSIS_PAGE_SIZE,
        confidence_max: Annotated[
            float | None,
            Query(alias="confidenceMax", ge=0, le=1),
        ] = None,
        requires_review: Annotated[
            bool | None,
            Query(alias="requiresReview"),
        ] = None,
        speaker_state: Annotated[
            str | None,
            Query(alias="speakerState", max_length=24),
        ] = None,
    ) -> dict[str, Any]:
        if speaker_state not in {None, "unknown", "ambiguous", "proposed", "corrected"}:
            raise ServiceError(
                422,
                "INVALID_SPEAKER_STATE",
                "The dialogue speaker-state filter is invalid.",
            )
        items, next_cursor, total = story_intelligence.list_entities(
            project_id=project_id,
            run_id=run_id,
            collection=collection,
            cursor=cursor,
            limit=limit,
            confidence_max=confidence_max,
            requires_review=requires_review,
            speaker_state=speaker_state,
        )
        run = story_intelligence.get_run(project_id=project_id, run_id=run_id)
        snapshot = run.get("currentSnapshot")
        if not isinstance(snapshot, dict) or not isinstance(
            snapshot.get("snapshotId"),
            str,
        ):
            raise ServiceError(
                409,
                "ANALYSIS_SNAPSHOT_REQUIRED",
                "The requested analysis collection is not published yet.",
            )
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "pageSize": len(items),
            "total": total,
            "collection": collection,
            "runId": run_id,
            "snapshotId": snapshot["snapshotId"],
            "items": items,
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.get("/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections")
    def list_analysis_corrections(
        request: Request,
        project_id: str,
        run_id: str,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_ANALYSIS_PAGE_SIZE),
        ] = DEFAULT_ANALYSIS_PAGE_SIZE,
    ) -> dict[str, Any]:
        items, next_cursor, total = story_intelligence.list_corrections(
            project_id=project_id,
            run_id=run_id,
            cursor=cursor,
            limit=limit,
        )
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "pageSize": len(items),
            "total": total,
            "runId": run_id,
            "items": items,
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.post("/api/v1/projects/{project_id}/analysis-runs/{run_id}/corrections")
    def append_analysis_correction(
        request: Request,
        project_id: str,
        run_id: str,
        body: AppendAnalysisCorrectionRequest,
    ) -> dict[str, Any]:
        correction, invalidated_gate_ids = story_intelligence.append_correction(
            project_id=project_id,
            run_id=run_id,
            category=body.category,
            target_collection=body.target_collection,
            target_entity_id=body.target_entity_id,
            expected_target_revision=body.expected_target_revision,
            expected_run_fingerprint=body.expected_run_fingerprint,
            previous_value_fingerprint=body.previous_value_fingerprint,
            patch=dict(body.patch),
            reason=body.reason,
            supersedes_correction_id=body.supersedes_correction_id,
            idempotency_key=body.idempotency_key,
        )
        return {
            "correlationId": _correlation_id(request),
            "correction": correction,
            "invalidatedGateIds": invalidated_gate_ids,
            "run": story_intelligence.get_run(
                project_id=project_id,
                run_id=run_id,
            ),
            "reviews": story_intelligence.list_reviews(
                project_id=project_id,
                run_id=run_id,
            ),
        }

    @app.get("/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews")
    def list_analysis_reviews(
        request: Request,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "runId": run_id,
            "items": story_intelligence.list_reviews(
                project_id=project_id,
                run_id=run_id,
            ),
        }

    @app.post("/api/v1/projects/{project_id}/analysis-runs/{run_id}/reviews/{gate_id}/decisions")
    def decide_analysis_review(
        request: Request,
        project_id: str,
        run_id: str,
        gate_id: str,
        body: DecideAnalysisReviewRequest,
    ) -> dict[str, Any]:
        review, decision = story_intelligence.decide_review(
            project_id=project_id,
            run_id=run_id,
            gate_id=gate_id,
            decision=body.decision,
            expected_revision=body.expected_revision,
            expected_artifact_fingerprint=body.expected_artifact_fingerprint,
            expected_evidence_fingerprint=body.expected_evidence_fingerprint,
            acknowledged_warning_ids=body.acknowledged_warning_ids,
            rationale=body.rationale,
            idempotency_key=body.idempotency_key,
        )
        return {
            "correlationId": _correlation_id(request),
            "review": review,
            "decision": decision,
            "run": story_intelligence.get_run(
                project_id=project_id,
                run_id=run_id,
            ),
        }

    @app.get("/api/v1/projects/{project_id}/casting/catalog")
    def get_voice_catalog(
        request: Request,
        project_id: str,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_CASTING_PAGE_SIZE),
        ] = DEFAULT_CASTING_PAGE_SIZE,
        expected_catalog_revision_id: Annotated[
            str | None,
            Query(alias="expectedCatalogRevisionId", max_length=128),
        ] = None,
        expected_catalog_fingerprint: Annotated[
            str | None,
            Query(
                alias="expectedCatalogFingerprint",
                pattern=r"^[a-f0-9]{64}$",
            ),
        ] = None,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            **casting.catalog_page(
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                expected_revision_id=expected_catalog_revision_id,
                expected_fingerprint=expected_catalog_fingerprint,
            ),
        }

    @app.post("/api/v1/projects/{project_id}/casting-runs", status_code=202)
    def create_casting_run(
        request: Request,
        project_id: str,
        body: CreateCastingRunRequest,
    ) -> dict[str, Any]:
        run, job = casting.create_run(
            project_id=project_id,
            expected_analysis_run_id=body.expected_analysis_run_id,
            expected_snapshot_id=body.expected_snapshot_id,
            expected_snapshot_revision=body.expected_snapshot_revision,
            expected_snapshot_fingerprint=body.expected_snapshot_fingerprint,
            expected_correction_set_fingerprint=(body.expected_correction_set_fingerprint),
            expected_import_review_decision_id=(body.expected_import_review_decision_id),
            expected_analysis_gate_decision_ids=(
                body.expected_analysis_gate_decision_ids.model_dump()
            ),
            expected_catalog_revision_id=body.expected_catalog_revision_id,
            expected_catalog_fingerprint=body.expected_catalog_fingerprint,
            expected_casting_profile_fingerprint=(body.expected_casting_profile_fingerprint),
            idempotency_key=body.idempotency_key,
        )
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "run": run,
            "job": job,
        }

    @app.get("/api/v1/projects/{project_id}/casting-runs")
    def list_casting_runs(
        request: Request,
        project_id: str,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_CASTING_PAGE_SIZE),
        ] = DEFAULT_CASTING_PAGE_SIZE,
    ) -> dict[str, Any]:
        runs, next_cursor, total = casting.list_runs(
            project_id=project_id,
            cursor=cursor,
            limit=limit,
        )
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "items": runs,
            "total": total,
            "pageSize": len(runs),
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.get("/api/v1/projects/{project_id}/casting-runs/{run_id}")
    def get_casting_run(
        request: Request,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "run": casting.get_run(project_id=project_id, run_id=run_id),
        }

    def casting_evidence(
        *,
        expected_run_fingerprint: str,
        expected_catalog_revision_id: str,
        expected_catalog_fingerprint: str,
        expected_snapshot_id: str,
        expected_snapshot_revision: int,
        expected_snapshot_fingerprint: str,
    ) -> dict[str, Any]:
        return {
            "expected_run_fingerprint": expected_run_fingerprint,
            "expected_catalog_revision_id": expected_catalog_revision_id,
            "expected_catalog_fingerprint": expected_catalog_fingerprint,
            "expected_snapshot_id": expected_snapshot_id,
            "expected_snapshot_revision": expected_snapshot_revision,
            "expected_snapshot_fingerprint": expected_snapshot_fingerprint,
        }

    @app.get("/api/v1/projects/{project_id}/casting-runs/{run_id}/roles")
    def list_production_roles(
        request: Request,
        project_id: str,
        run_id: str,
        expected_run_fingerprint: Annotated[
            str,
            Query(alias="expectedRunFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_catalog_revision_id: Annotated[
            str,
            Query(alias="expectedCatalogRevisionId", min_length=1, max_length=128),
        ],
        expected_catalog_fingerprint: Annotated[
            str,
            Query(alias="expectedCatalogFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_snapshot_id: Annotated[
            str,
            Query(alias="expectedSnapshotId", min_length=1, max_length=128),
        ],
        expected_snapshot_revision: Annotated[
            int,
            Query(alias="expectedSnapshotRevision", ge=1),
        ],
        expected_snapshot_fingerprint: Annotated[
            str,
            Query(alias="expectedSnapshotFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_CASTING_PAGE_SIZE),
        ] = DEFAULT_CASTING_PAGE_SIZE,
    ) -> dict[str, Any]:
        items, next_cursor, total = casting.list_roles(
            project_id=project_id,
            run_id=run_id,
            cursor=cursor,
            limit=limit,
            evidence=casting_evidence(
                expected_run_fingerprint=expected_run_fingerprint,
                expected_catalog_revision_id=expected_catalog_revision_id,
                expected_catalog_fingerprint=expected_catalog_fingerprint,
                expected_snapshot_id=expected_snapshot_id,
                expected_snapshot_revision=expected_snapshot_revision,
                expected_snapshot_fingerprint=expected_snapshot_fingerprint,
            ),
        )
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "castingRunId": run_id,
            "items": items,
            "total": total,
            "pageSize": len(items),
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.post("/api/v1/projects/{project_id}/casting-runs/{run_id}/roles")
    def create_custom_production_role(
        request: Request,
        project_id: str,
        run_id: str,
        body: CreateCustomProductionRoleRequest,
    ) -> dict[str, Any]:
        role, invalidated_gate_ids, run, reviews = casting.create_custom_role(
            project_id=project_id,
            run_id=run_id,
            definition_id=body.definition_id,
            label=body.label,
            performance_requirements=body.performance_requirements.model_dump(by_alias=True),
            reason=body.reason,
            expected_run_fingerprint=body.expected_run_fingerprint,
            expected_catalog_revision_id=body.expected_catalog_revision_id,
            expected_catalog_fingerprint=body.expected_catalog_fingerprint,
            expected_snapshot_id=body.expected_snapshot_id,
            expected_snapshot_revision=body.expected_snapshot_revision,
            expected_snapshot_fingerprint=body.expected_snapshot_fingerprint,
            expected_correction_set_fingerprint=(body.expected_correction_set_fingerprint),
            expected_casting_profile_fingerprint=(body.expected_casting_profile_fingerprint),
            idempotency_key=body.idempotency_key,
        )
        return {
            "correlationId": _correlation_id(request),
            "role": role,
            "invalidatedGateIds": invalidated_gate_ids,
            "run": run,
            "reviews": reviews,
        }

    @app.get("/api/v1/projects/{project_id}/casting-runs/{run_id}/roles/{role_id}/candidates")
    def list_casting_candidates(
        request: Request,
        project_id: str,
        run_id: str,
        role_id: str,
        expected_role_revision: Annotated[
            int,
            Query(alias="expectedRoleRevision", ge=1),
        ],
        expected_run_fingerprint: Annotated[
            str,
            Query(alias="expectedRunFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_catalog_revision_id: Annotated[
            str,
            Query(alias="expectedCatalogRevisionId", min_length=1, max_length=128),
        ],
        expected_catalog_fingerprint: Annotated[
            str,
            Query(alias="expectedCatalogFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_snapshot_id: Annotated[
            str,
            Query(alias="expectedSnapshotId", min_length=1, max_length=128),
        ],
        expected_snapshot_revision: Annotated[
            int,
            Query(alias="expectedSnapshotRevision", ge=1),
        ],
        expected_snapshot_fingerprint: Annotated[
            str,
            Query(alias="expectedSnapshotFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_CASTING_PAGE_SIZE),
        ] = DEFAULT_CASTING_PAGE_SIZE,
    ) -> dict[str, Any]:
        items, next_cursor, total = casting.list_candidates(
            project_id=project_id,
            run_id=run_id,
            role_id=role_id,
            expected_role_revision=expected_role_revision,
            cursor=cursor,
            limit=limit,
            evidence=casting_evidence(
                expected_run_fingerprint=expected_run_fingerprint,
                expected_catalog_revision_id=expected_catalog_revision_id,
                expected_catalog_fingerprint=expected_catalog_fingerprint,
                expected_snapshot_id=expected_snapshot_id,
                expected_snapshot_revision=expected_snapshot_revision,
                expected_snapshot_fingerprint=expected_snapshot_fingerprint,
            ),
        )
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "castingRunId": run_id,
            "items": items,
            "total": total,
            "pageSize": len(items),
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    def casting_page_response(
        request: Request,
        run_id: str,
        values: tuple[list[dict[str, Any]], str | None, int],
    ) -> dict[str, Any]:
        items, next_cursor, total = values
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "castingRunId": run_id,
            "items": items,
            "total": total,
            "pageSize": len(items),
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.get("/api/v1/projects/{project_id}/casting-runs/{run_id}/conflicts")
    def list_casting_conflicts(
        request: Request,
        project_id: str,
        run_id: str,
        expected_run_fingerprint: Annotated[
            str,
            Query(alias="expectedRunFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_catalog_revision_id: Annotated[
            str,
            Query(alias="expectedCatalogRevisionId", min_length=1, max_length=128),
        ],
        expected_catalog_fingerprint: Annotated[
            str,
            Query(alias="expectedCatalogFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_snapshot_id: Annotated[
            str,
            Query(alias="expectedSnapshotId", min_length=1, max_length=128),
        ],
        expected_snapshot_revision: Annotated[
            int,
            Query(alias="expectedSnapshotRevision", ge=1),
        ],
        expected_snapshot_fingerprint: Annotated[
            str,
            Query(alias="expectedSnapshotFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_CASTING_PAGE_SIZE),
        ] = DEFAULT_CASTING_PAGE_SIZE,
    ) -> dict[str, Any]:
        return casting_page_response(
            request,
            run_id,
            casting.list_conflicts(
                project_id=project_id,
                run_id=run_id,
                cursor=cursor,
                limit=limit,
                evidence=casting_evidence(
                    expected_run_fingerprint=expected_run_fingerprint,
                    expected_catalog_revision_id=expected_catalog_revision_id,
                    expected_catalog_fingerprint=expected_catalog_fingerprint,
                    expected_snapshot_id=expected_snapshot_id,
                    expected_snapshot_revision=expected_snapshot_revision,
                    expected_snapshot_fingerprint=expected_snapshot_fingerprint,
                ),
            ),
        )

    @app.get("/api/v1/projects/{project_id}/casting-runs/{run_id}/assignments")
    def list_cast_assignments(
        request: Request,
        project_id: str,
        run_id: str,
        expected_run_fingerprint: Annotated[
            str,
            Query(alias="expectedRunFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_catalog_revision_id: Annotated[
            str,
            Query(alias="expectedCatalogRevisionId", min_length=1, max_length=128),
        ],
        expected_catalog_fingerprint: Annotated[
            str,
            Query(alias="expectedCatalogFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_snapshot_id: Annotated[
            str,
            Query(alias="expectedSnapshotId", min_length=1, max_length=128),
        ],
        expected_snapshot_revision: Annotated[
            int,
            Query(alias="expectedSnapshotRevision", ge=1),
        ],
        expected_snapshot_fingerprint: Annotated[
            str,
            Query(alias="expectedSnapshotFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_CASTING_PAGE_SIZE),
        ] = DEFAULT_CASTING_PAGE_SIZE,
    ) -> dict[str, Any]:
        return casting_page_response(
            request,
            run_id,
            casting.list_assignments(
                project_id=project_id,
                run_id=run_id,
                cursor=cursor,
                limit=limit,
                evidence=casting_evidence(
                    expected_run_fingerprint=expected_run_fingerprint,
                    expected_catalog_revision_id=expected_catalog_revision_id,
                    expected_catalog_fingerprint=expected_catalog_fingerprint,
                    expected_snapshot_id=expected_snapshot_id,
                    expected_snapshot_revision=expected_snapshot_revision,
                    expected_snapshot_fingerprint=expected_snapshot_fingerprint,
                ),
            ),
        )

    @app.get("/api/v1/projects/{project_id}/casting-runs/{run_id}/corrections")
    def list_casting_corrections(
        request: Request,
        project_id: str,
        run_id: str,
        expected_run_fingerprint: Annotated[
            str,
            Query(alias="expectedRunFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_catalog_revision_id: Annotated[
            str,
            Query(alias="expectedCatalogRevisionId", min_length=1, max_length=128),
        ],
        expected_catalog_fingerprint: Annotated[
            str,
            Query(alias="expectedCatalogFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_snapshot_id: Annotated[
            str,
            Query(alias="expectedSnapshotId", min_length=1, max_length=128),
        ],
        expected_snapshot_revision: Annotated[
            int,
            Query(alias="expectedSnapshotRevision", ge=1),
        ],
        expected_snapshot_fingerprint: Annotated[
            str,
            Query(alias="expectedSnapshotFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MAX_CASTING_PAGE_SIZE),
        ] = DEFAULT_CASTING_PAGE_SIZE,
    ) -> dict[str, Any]:
        return casting_page_response(
            request,
            run_id,
            casting.list_corrections(
                project_id=project_id,
                run_id=run_id,
                cursor=cursor,
                limit=limit,
                evidence=casting_evidence(
                    expected_run_fingerprint=expected_run_fingerprint,
                    expected_catalog_revision_id=expected_catalog_revision_id,
                    expected_catalog_fingerprint=expected_catalog_fingerprint,
                    expected_snapshot_id=expected_snapshot_id,
                    expected_snapshot_revision=expected_snapshot_revision,
                    expected_snapshot_fingerprint=expected_snapshot_fingerprint,
                ),
            ),
        )

    @app.post("/api/v1/projects/{project_id}/casting-runs/{run_id}/corrections")
    def append_casting_correction(
        request: Request,
        project_id: str,
        run_id: str,
        body: AppendCastingCorrectionRequest,
    ) -> dict[str, Any]:
        correction, assignment, invalidated_gate_ids, run, reviews = casting.append_correction(
            project_id=project_id,
            run_id=run_id,
            operation=body.operation,
            target_role_id=body.target_role_id,
            expected_role_revision=body.expected_role_revision,
            expected_run_fingerprint=body.expected_run_fingerprint,
            expected_catalog_fingerprint=body.expected_catalog_fingerprint,
            expected_snapshot_fingerprint=body.expected_snapshot_fingerprint,
            expected_correction_set_fingerprint=(body.expected_correction_set_fingerprint),
            previous_effective_fingerprint=(body.previous_effective_fingerprint),
            voice_profile_id=body.voice_profile_id,
            corrected_value=body.corrected_value,
            reason=body.reason,
            supersedes_correction_id=body.supersedes_correction_id,
            idempotency_key=body.idempotency_key,
        )
        return {
            "correlationId": _correlation_id(request),
            "correction": correction,
            "assignment": assignment,
            "invalidatedGateIds": invalidated_gate_ids,
            "run": run,
            "reviews": reviews,
        }

    @app.get("/api/v1/projects/{project_id}/casting-runs/{run_id}/reviews")
    def list_casting_reviews(
        request: Request,
        project_id: str,
        run_id: str,
        expected_run_fingerprint: Annotated[
            str,
            Query(alias="expectedRunFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_catalog_revision_id: Annotated[
            str,
            Query(alias="expectedCatalogRevisionId", min_length=1, max_length=128),
        ],
        expected_catalog_fingerprint: Annotated[
            str,
            Query(alias="expectedCatalogFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_snapshot_id: Annotated[
            str,
            Query(alias="expectedSnapshotId", min_length=1, max_length=128),
        ],
        expected_snapshot_revision: Annotated[
            int,
            Query(alias="expectedSnapshotRevision", ge=1),
        ],
        expected_snapshot_fingerprint: Annotated[
            str,
            Query(alias="expectedSnapshotFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_approved_cast_snapshot_id: Annotated[
            str,
            Query(
                alias="expectedApprovedCastSnapshotId",
                min_length=1,
                max_length=128,
            ),
        ],
        expected_approved_cast_snapshot_revision: Annotated[
            int,
            Query(alias="expectedApprovedCastSnapshotRevision", ge=1),
        ],
    ) -> dict[str, Any]:
        items = casting.list_reviews(
            project_id=project_id,
            run_id=run_id,
            evidence=casting_evidence(
                expected_run_fingerprint=expected_run_fingerprint,
                expected_catalog_revision_id=expected_catalog_revision_id,
                expected_catalog_fingerprint=expected_catalog_fingerprint,
                expected_snapshot_id=expected_snapshot_id,
                expected_snapshot_revision=expected_snapshot_revision,
                expected_snapshot_fingerprint=expected_snapshot_fingerprint,
            ),
            expected_cast_snapshot_id=(expected_approved_cast_snapshot_id),
            expected_cast_snapshot_revision=(expected_approved_cast_snapshot_revision),
        )
        return {
            "correlationId": _correlation_id(request),
            "castingRunId": run_id,
            "items": items,
        }

    @app.post("/api/v1/projects/{project_id}/casting-runs/{run_id}/reviews/{gate_id}/decisions")
    def decide_casting_review(
        request: Request,
        project_id: str,
        run_id: str,
        gate_id: str,
        body: DecideCastingReviewRequest,
    ) -> dict[str, Any]:
        review, decision, snapshot, run = casting.decide_review(
            project_id=project_id,
            run_id=run_id,
            gate_id=gate_id,
            decision=body.decision,
            expected_revision=body.expected_revision,
            expected_evidence_fingerprint=body.expected_evidence_fingerprint,
            expected_run_fingerprint=body.expected_run_fingerprint,
            expected_approved_cast_snapshot_id=(body.expected_approved_cast_snapshot_id),
            expected_approved_cast_snapshot_revision=(
                body.expected_approved_cast_snapshot_revision
            ),
            warning_acknowledgement_ids=(body.warning_acknowledgement_ids),
            rationale=body.rationale,
            supersedes_decision_id=body.supersedes_decision_id,
            idempotency_key=body.idempotency_key,
        )
        return {
            "correlationId": _correlation_id(request),
            "review": review,
            "decision": decision,
            "snapshot": snapshot,
            "run": run,
        }

    def audition_page_response(
        request: Request,
        project_id: str,
        page: tuple[list[dict[str, Any]], str | None, int],
    ) -> dict[str, Any]:
        items, next_cursor, total = page
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "projectId": project_id,
            "pageSize": len(items),
            "total": total,
            "items": items,
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.get("/api/v1/projects/{project_id}/auditions/workspace")
    def audition_workspace(
        request: Request,
        project_id: str,
        role_cursor: Annotated[str | None, Query(max_length=512, alias="roleCursor")] = None,
        role_limit: Annotated[
            int,
            Query(ge=1, le=MAX_AUDITION_PAGE_SIZE, alias="roleLimit"),
        ] = DEFAULT_AUDITION_PAGE_SIZE,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "workspace": auditions.workspace_snapshot(
                project_id,
                role_cursor=role_cursor,
                role_limit=role_limit,
            ),
        }

    @app.get("/api/v1/projects/{project_id}/speech/model-packages")
    def list_model_packages(
        request: Request,
        project_id: str,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_AUDITION_PAGE_SIZE)] = (
            DEFAULT_AUDITION_PAGE_SIZE
        ),
    ) -> dict[str, Any]:
        return audition_page_response(
            request,
            project_id,
            auditions.list_model_packages(
                project_id=project_id,
                cursor=cursor,
                limit=limit,
            ),
        )

    async def apply_local_model_archive(
        request: Request,
        *,
        project_id: str,
        model_package_id: str,
        operation: Literal["install", "repair"],
    ) -> dict[str, Any]:
        try:
            form = await _bounded_import_form(
                request,
                _MAX_MODEL_PACKAGE_UPLOAD_BYTES,
                model_staging_directory,
                max_fields=5,
            )
        except _BodyLimitExceeded as exc:
            raise ServiceError(
                413,
                "MODEL_PACKAGE_TOO_LARGE",
                "The local model package exceeded its fixed upload bound.",
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                400,
                "MALFORMED_MODEL_PACKAGE_UPLOAD",
                "The local model package upload was malformed.",
            ) from exc
        staged_archive: Path | None = None
        try:
            keys = [str(key) for key, _value in form.multi_items()]
            allowed = {
                "file",
                "expectedManifestFingerprint",
                "expectedInstallationRevision",
                "acknowledgeRestrictedLocalUse",
                "reason",
                "idempotencyKey",
            }
            required = allowed - {"expectedInstallationRevision"}
            if (
                len(keys) != len(set(keys))
                or not required.issubset(keys)
                or any(key not in allowed for key in keys)
            ):
                raise ServiceError(
                    422,
                    "INVALID_MODEL_PACKAGE_UPLOAD",
                    "The local model package upload fields were invalid.",
                )
            upload = form.get("file")
            if not isinstance(upload, StarletteUploadFile):
                raise ServiceError(
                    422,
                    "MODEL_PACKAGE_ARCHIVE_REQUIRED",
                    "A local model package ZIP archive is required.",
                )
            values: dict[str, str | None] = {}
            for key in required - {"file"}:
                value = form.get(key)
                if not isinstance(value, str):
                    raise ServiceError(
                        422,
                        "INVALID_MODEL_PACKAGE_UPLOAD",
                        "The local model package upload fields were invalid.",
                    )
                values[key] = value
            revision_value = form.get("expectedInstallationRevision")
            if revision_value is not None and not isinstance(revision_value, str):
                raise ServiceError(
                    422,
                    "INVALID_MODEL_PACKAGE_UPLOAD",
                    "The local model package upload fields were invalid.",
                )
            values["expectedInstallationRevision"] = revision_value
            if values["acknowledgeRestrictedLocalUse"] not in {"true", "false"}:
                raise ServiceError(
                    422,
                    "INVALID_MODEL_PACKAGE_UPLOAD",
                    "The restricted-use acknowledgement must be explicit.",
                )
            try:
                upload_request = InstallModelPackageRequest.model_validate(values)
            except PydanticValidationError as exc:
                raise ServiceError(
                    422,
                    "INVALID_MODEL_PACKAGE_UPLOAD",
                    "The local model package upload fields were invalid.",
                ) from exc
            staged_archive = _stage_private_model_archive(
                upload,
                model_staging_directory,
            )
            action = (
                auditions.install_model_package
                if operation == "install"
                else auditions.repair_model_package
            )
            return {
                "correlationId": _correlation_id(request),
                **action(
                    project_id=project_id,
                    model_package_id=model_package_id,
                    request=upload_request,
                    archive_path=staged_archive,
                    actor_id=_LOCAL_ACTOR_ID,
                ),
            }
        finally:
            if staged_archive is not None:
                staged_archive.unlink(missing_ok=True)
            await form.close()

    @app.post("/api/v1/projects/{project_id}/speech/model-packages/{model_package_id}/install")
    async def install_local_model_package(
        request: Request,
        project_id: str,
        model_package_id: str,
    ) -> dict[str, Any]:
        return await apply_local_model_archive(
            request,
            project_id=project_id,
            model_package_id=model_package_id,
            operation="install",
        )

    @app.post("/api/v1/projects/{project_id}/speech/model-packages/{model_package_id}/repair")
    async def repair_local_model_package(
        request: Request,
        project_id: str,
        model_package_id: str,
    ) -> dict[str, Any]:
        return await apply_local_model_archive(
            request,
            project_id=project_id,
            model_package_id=model_package_id,
            operation="repair",
        )

    @app.post("/api/v1/projects/{project_id}/speech/model-packages/{model_package_id}/actions")
    def perform_model_package_action(
        request: Request,
        project_id: str,
        model_package_id: str,
        body: ModelInstallationOperationRequest,
    ) -> dict[str, Any]:
        if body.model_package_id != model_package_id:
            raise ServiceError(
                422,
                "MODEL_PACKAGE_ID_MISMATCH",
                "The model package identifier does not match the request path.",
            )
        return {
            "correlationId": _correlation_id(request),
            **auditions.perform_model_package_action(
                project_id=project_id,
                request=body,
                actor_id=_LOCAL_ACTOR_ID,
            ),
        }

    @app.get("/api/v1/projects/{project_id}/pronunciations/entries")
    def list_pronunciation_entries(
        request: Request,
        project_id: str,
        expected_dictionary_revision: Annotated[
            int | None,
            Query(alias="expectedDictionaryRevision", ge=1),
        ] = None,
        expected_dictionary_fingerprint: Annotated[
            str | None,
            Query(
                alias="expectedDictionaryFingerprint",
                pattern=r"^[a-f0-9]{64}$",
            ),
        ] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_AUDITION_PAGE_SIZE)] = (
            DEFAULT_AUDITION_PAGE_SIZE
        ),
    ) -> dict[str, Any]:
        dictionary, items, next_cursor, total = auditions.list_pronunciation_entries(
            project_id=project_id,
            cursor=cursor,
            limit=limit,
            expected_dictionary_revision=expected_dictionary_revision,
            expected_dictionary_fingerprint=expected_dictionary_fingerprint,
        )
        result: dict[str, Any] = {
            "correlationId": _correlation_id(request),
            "projectId": project_id,
            "dictionary": dictionary,
            "pageSize": len(items),
            "total": total,
            "items": items,
        }
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    @app.post("/api/v1/projects/{project_id}/pronunciations/entries")
    def create_pronunciation_entry(
        request: Request,
        project_id: str,
        body: CreatePronunciationEntryRequest,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            **auditions.create_pronunciation_entry(
                project_id=project_id,
                request=body,
                actor_id=_LOCAL_ACTOR_ID,
            ),
        }

    @app.post("/api/v1/projects/{project_id}/pronunciations/entries/{entry_id}/decisions")
    def decide_pronunciation_entry(
        request: Request,
        project_id: str,
        entry_id: str,
        body: DecidePronunciationEntryRequest,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            **auditions.decide_pronunciation_entry(
                project_id=project_id,
                entry_id=entry_id,
                request=body,
                actor_id=_LOCAL_ACTOR_ID,
            ),
        }

    @app.get("/api/v1/projects/{project_id}/audition-sessions")
    def list_audition_sessions(
        request: Request,
        project_id: str,
        role_id: Annotated[
            str | None,
            Query(alias="roleId", min_length=1, max_length=128),
        ] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_AUDITION_PAGE_SIZE)] = (
            DEFAULT_AUDITION_PAGE_SIZE
        ),
    ) -> dict[str, Any]:
        return audition_page_response(
            request,
            project_id,
            auditions.list_sessions(
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                role_id=role_id,
            ),
        )

    @app.post("/api/v1/projects/{project_id}/audition-sessions")
    def create_audition_session(
        request: Request,
        project_id: str,
        body: CreateAuditionSessionRequest,
    ) -> dict[str, Any]:
        if body.evidence.project_id != project_id:
            raise ServiceError(
                422,
                "AUDITION_PROJECT_ID_MISMATCH",
                "The audition project identifier does not match the request path.",
            )
        return {
            "correlationId": _correlation_id(request),
            "session": auditions.create_session(project_id=project_id, request=body),
        }

    @app.post("/api/v1/projects/{project_id}/audition-sessions/{audition_session_id}/scripts")
    def create_audition_script(
        request: Request,
        project_id: str,
        audition_session_id: str,
        body: CreateAuditionScriptRequest,
    ) -> dict[str, Any]:
        if body.audition_session_id != audition_session_id:
            raise ServiceError(
                422,
                "AUDITION_SESSION_ID_MISMATCH",
                "The audition session identifier does not match the request path.",
            )
        return {
            "correlationId": _correlation_id(request),
            **auditions.create_script(project_id=project_id, request=body),
        }

    @app.post(
        "/api/v1/projects/{project_id}/audition-sessions/{audition_session_id}"
        "/normalization-preview"
    )
    def preview_audition_normalization(
        request: Request,
        project_id: str,
        audition_session_id: str,
        body: PreviewNormalizationRequest,
    ) -> dict[str, Any]:
        if body.audition_session_id != audition_session_id:
            raise ServiceError(
                422,
                "AUDITION_SESSION_ID_MISMATCH",
                "The audition session identifier does not match the request path.",
            )
        return {
            "correlationId": _correlation_id(request),
            **auditions.preview_normalization(project_id=project_id, request=body),
        }

    @app.post(
        "/api/v1/projects/{project_id}/audition-sessions/{audition_session_id}/generate",
        status_code=202,
    )
    def generate_audition(
        request: Request,
        project_id: str,
        audition_session_id: str,
        body: GenerateAuditionRequest,
    ) -> dict[str, Any]:
        if body.preview.audition_session_id != audition_session_id:
            raise ServiceError(
                422,
                "AUDITION_SESSION_ID_MISMATCH",
                "The audition session identifier does not match the request path.",
            )
        result = auditions.queue_generation(
            project_id=project_id,
            request=body,
            jobs=jobs,
        )
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            **result,
        }

    @app.get("/api/v1/projects/{project_id}/audition-clips")
    def list_audition_clips(
        request: Request,
        project_id: str,
        audition_session_id: Annotated[
            str | None,
            Query(alias="auditionSessionId", min_length=1, max_length=128),
        ] = None,
        role_id: Annotated[
            str | None,
            Query(alias="roleId", min_length=1, max_length=128),
        ] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_AUDITION_PAGE_SIZE)] = (
            DEFAULT_AUDITION_PAGE_SIZE
        ),
    ) -> dict[str, Any]:
        return audition_page_response(
            request,
            project_id,
            auditions.list_clips(
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                audition_session_id=audition_session_id,
                role_id=role_id,
            ),
        )

    @app.get("/api/v1/projects/{project_id}/audition-clips/{clip_id}/audio")
    def load_audition_audio(
        project_id: str,
        clip_id: str,
        audition_session_id: Annotated[
            str,
            Query(alias="auditionSessionId", min_length=1, max_length=128),
        ],
        audio_artifact_id: Annotated[
            str,
            Query(alias="audioArtifactId", min_length=1, max_length=128),
        ],
        expected_clip_revision: Annotated[
            int,
            Query(alias="expectedClipRevision", ge=1),
        ],
        expected_clip_fingerprint: Annotated[
            str,
            Query(alias="expectedClipFingerprint", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_artifact_sha256: Annotated[
            str,
            Query(alias="expectedArtifactSha256", pattern=r"^[a-f0-9]{64}$"),
        ],
        expected_byte_size: Annotated[
            int,
            Query(alias="byteSize", ge=45, le=MAX_AUDITION_AUDIO_BYTES),
        ],
    ) -> Response:
        payload, _descriptor = auditions.get_audio_bytes(
            project_id=project_id,
            clip_id=clip_id,
            audition_session_id=audition_session_id,
            audio_artifact_id=audio_artifact_id,
            expected_clip_revision=expected_clip_revision,
            expected_clip_fingerprint=expected_clip_fingerprint,
            expected_artifact_sha256=expected_artifact_sha256,
            expected_byte_size=expected_byte_size,
        )
        return Response(
            content=payload,
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-store",
                "Content-Length": str(len(payload)),
            },
        )

    @app.get("/api/v1/projects/{project_id}/audition-review-decisions")
    def list_audition_review_decisions(
        request: Request,
        project_id: str,
        gate_id: Annotated[
            str,
            Query(alias="gateId", min_length=1, max_length=48),
        ],
        role_id: Annotated[
            str | None,
            Query(alias="roleId", min_length=1, max_length=128),
        ] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_AUDITION_PAGE_SIZE)] = (
            DEFAULT_AUDITION_PAGE_SIZE
        ),
    ) -> dict[str, Any]:
        try:
            query = ListAuditionReviewDecisionsQuery.model_validate(
                {
                    "gateId": gate_id,
                    "roleId": role_id,
                    "cursor": cursor,
                    "limit": limit,
                }
            )
        except PydanticValidationError as exc:
            raise ServiceError(
                422,
                "AUDITION_REVIEW_HISTORY_SCOPE_INVALID",
                "The audition review history scope is invalid.",
            ) from exc
        result = audition_page_response(
            request,
            project_id,
            auditions.list_review_decisions(
                project_id=project_id,
                gate_id=query.gate_id,
                role_id=query.role_id,
                cursor=query.cursor,
                limit=query.limit,
            ),
        )
        result["gateId"] = query.gate_id
        result["roleId"] = query.role_id
        return result

    @app.post("/api/v1/projects/{project_id}/audition-reviews/{gate_id}/{review_id}/decisions")
    def decide_audition_review(
        request: Request,
        project_id: str,
        gate_id: str,
        review_id: str,
        body: DecideAuditionReviewRequest,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            **auditions.decide_review(
                project_id=project_id,
                gate_id=gate_id,
                review_id=review_id,
                request=body,
                actor_id=_LOCAL_ACTOR_ID,
            ),
        }

    @app.post("/api/v1/projects/{project_id}/audition-cache/clear")
    def clear_audition_cache(
        request: Request,
        project_id: str,
        body: ClearAuditionCacheRequest,
    ) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            **auditions.clear_cache(
                project_id=project_id,
                request=body,
                actor_id=_LOCAL_ACTOR_ID,
            ),
        }

    @app.put("/api/v1/projects/{project_id}/dialogue-lines/{line_id}/speaker")
    def correct_speaker(
        request: Request,
        project_id: str,
        line_id: str,
        body: CorrectDialogueSpeakerRequest,
    ) -> dict[str, Any]:
        attribution, correction, project_revision, line_revision = projects.correct_speaker(
            project_id=project_id,
            line_id=line_id,
            character_id=body.character_id,
            reason=body.reason,
            expected_revision=body.expected_revision,
        )
        return {
            "correlationId": _correlation_id(request),
            "attribution": attribution,
            "appendedCorrection": correction,
            "projectRevision": project_revision,
            "lineRevision": line_revision,
        }

    @app.post("/api/v1/projects/{project_id}/jobs")
    def create_job(
        request: Request,
        project_id: str,
        body: CreateJobRequest,
    ) -> dict[str, Any]:
        job = jobs.create_job(
            project_id=project_id,
            job_type=body.type,
            input_revision=body.input_revision,
            idempotency_key=body.idempotency_key,
        )
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "job": job,
        }

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(request: Request, job_id: str) -> dict[str, Any]:
        return {
            "correlationId": _correlation_id(request),
            "job": jobs.get_job(job_id),
        }

    @app.get("/api/v1/jobs/{job_id}/events")
    def get_job_events(
        request: Request,
        job_id: str,
        after_sequence: Annotated[int, Query(alias="afterSequence", ge=0)] = 0,
    ) -> dict[str, Any]:
        events, last_sequence = jobs.get_events(job_id, after_sequence=after_sequence)
        return {
            "correlationId": _correlation_id(request),
            "events": events,
            "lastSequence": last_sequence,
        }

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
        job = jobs.cancel(job_id)
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "job": job,
        }

    @app.post("/api/v1/jobs/{job_id}/retry")
    def retry_job(request: Request, job_id: str) -> dict[str, Any]:
        job = jobs.retry(job_id)
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "job": job,
        }

    @app.post("/api/v1/jobs/{job_id}/resume")
    def resume_job(request: Request, job_id: str) -> dict[str, Any]:
        job = jobs.resume(job_id)
        worker.wake()
        return {
            "correlationId": _correlation_id(request),
            "job": job,
        }

    return app
