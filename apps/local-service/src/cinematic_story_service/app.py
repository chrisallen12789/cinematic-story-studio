from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any, BinaryIO, cast

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.types import Message, Receive

from .config import ServiceSettings
from .database import Database
from .errors import ServiceError
from .jobs import JobRepository, JobWorker
from .projects import ProjectRepository, StoryImportService
from .providers import ProviderRegistry
from .schemas import (
    CorrectDialogueSpeakerRequest,
    CreateJobRequest,
    CreateProjectRequest,
    DecideImportReviewRequest,
)
from .tools import FfmpegCapabilityChecker
from .util import (
    PROTOCOL_VERSION,
    SERVICE_VERSION,
    ensure_private_directory,
    new_id,
    utc_now,
)

_LOGGER = logging.getLogger("cinematic_story_service")
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}
_MULTIPART_OVERHEAD_ALLOWANCE = 64 * 1024


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
                max_fields=2,
                max_part_size=min(max_import_bytes + 1024, 101 * 1024 * 1024),
            )
            return await parser.parse()
        except StarletteHttpException as exc:
            if exc.status_code == 400 and exc.detail == "request_body_too_large":
                raise _BodyLimitExceeded from exc
            raise
    finally:
        request._receive = original_receive


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
    projects = ProjectRepository(database)
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
    )
    jobs.reconcile_interrupted()
    jobs.reconcile_orphaned_extractions()
    worker = JobWorker(settings, jobs, projects)
    providers = ProviderRegistry(settings)
    ffmpeg = FfmpegCapabilityChecker(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.worker_enabled:
            worker.start()
        try:
            yield
        finally:
            if settings.worker_enabled:
                worker.stop()
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
    app.state.imports = imports
    app.state.jobs = jobs
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
                response = await call_next(request)
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
        return {
            "correlationId": _correlation_id(request),
            **projects.get_project_detail(project_id),
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
