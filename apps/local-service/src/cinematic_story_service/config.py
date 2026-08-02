from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .util import ensure_private_directory, new_id


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    """Allow-listed service composition settings.

    Production bootstrap constructs this object directly. Environment variables are deliberately
    not consulted, which prevents host, port, token, documentation, and CORS overrides.
    """

    data_dir: Path
    bearer_token: str
    instance_id: str = field(default_factory=new_id)
    max_import_bytes: int = 100 * 1024 * 1024
    parser_deadline_seconds: float = 30.0
    worker_enabled: bool = True
    worker_poll_seconds: float = 0.025
    ffmpeg_executable: str | None = None
    allow_ffmpeg_path_lookup: bool = False
    kokoro_development_enabled: bool = False
    kokoro_development_url: str = "http://127.0.0.1:8880/health"
    kokoro_probe_timeout_seconds: float = 0.25
    docs_enabled: bool = False
    runtime_shutdown_evidence_enabled: bool = False

    def validated(self) -> ServiceSettings:
        data_dir = self.data_dir.expanduser().resolve(strict=False)
        if data_dir == Path(data_dir.anchor):
            raise ValueError("The service data directory cannot be a filesystem root.")
        if data_dir.exists() and not data_dir.is_dir():
            raise ValueError("The service data path must be a directory.")
        if not self.bearer_token or len(self.bearer_token) > 512:
            raise ValueError("A bounded bearer token is required.")
        if self.max_import_bytes < 1:
            raise ValueError("The import byte limit must be positive.")
        if not 0.1 <= self.parser_deadline_seconds <= 30:
            raise ValueError("The parser deadline is outside the safe range.")
        if not 0.001 <= self.worker_poll_seconds <= 5:
            raise ValueError("The worker polling interval is outside the safe range.")
        if not 0.05 <= self.kokoro_probe_timeout_seconds <= 2:
            raise ValueError("The Kokoro health timeout is outside the safe range.")
        parsed_kokoro_url = urlsplit(self.kokoro_development_url)
        if (
            parsed_kokoro_url.scheme != "http"
            or parsed_kokoro_url.hostname != "127.0.0.1"
            or parsed_kokoro_url.username is not None
            or parsed_kokoro_url.password is not None
            or parsed_kokoro_url.query
            or parsed_kokoro_url.fragment
            or parsed_kokoro_url.path != "/health"
        ):
            raise ValueError("The development Kokoro health URL must be fixed to loopback /health.")
        try:
            kokoro_port = parsed_kokoro_url.port
        except ValueError as exc:
            raise ValueError("The development Kokoro health port is invalid.") from exc
        if kokoro_port is None or not 1 <= kokoro_port <= 65535:
            raise ValueError("The development Kokoro health URL requires a valid port.")
        ensure_private_directory(data_dir)
        ensure_private_directory(data_dir / "projects")
        return ServiceSettings(
            data_dir=data_dir,
            bearer_token=self.bearer_token,
            instance_id=self.instance_id,
            max_import_bytes=self.max_import_bytes,
            parser_deadline_seconds=self.parser_deadline_seconds,
            worker_enabled=self.worker_enabled,
            worker_poll_seconds=self.worker_poll_seconds,
            ffmpeg_executable=self.ffmpeg_executable,
            allow_ffmpeg_path_lookup=self.allow_ffmpeg_path_lookup,
            kokoro_development_enabled=self.kokoro_development_enabled,
            kokoro_development_url=self.kokoro_development_url,
            kokoro_probe_timeout_seconds=self.kokoro_probe_timeout_seconds,
            docs_enabled=self.docs_enabled,
            runtime_shutdown_evidence_enabled=self.runtime_shutdown_evidence_enabled,
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "cinematic-story-studio.sqlite3"
