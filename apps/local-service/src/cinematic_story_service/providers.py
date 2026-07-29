from __future__ import annotations

import http.client
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .config import ServiceSettings
from .util import ANALYZER_VERSION, utc_now

ConnectionFactory = Callable[..., http.client.HTTPConnection]


class ProviderRegistry:
    """Typed, content-free provider health.

    Phase 0 intentionally performs no cloud requests. Kokoro is represented truthfully as a
    development-only adapter and its absence never blocks project work.
    """

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        connection_factory: ConnectionFactory = http.client.HTTPConnection,
    ) -> None:
        self.settings = settings
        self.connection_factory = connection_factory

    def health(self) -> list[dict[str, Any]]:
        checked_at = utc_now()
        kokoro = self._kokoro_health(checked_at)
        return [
            {
                "providerId": "deterministic-story-analyzer",
                "kind": "language",
                "executionLocation": "local",
                "status": "available",
                "capabilities": ["story_structure", "dialogue_attribution"],
                "version": ANALYZER_VERSION,
                "redactedReason": "Deterministic local analysis is available.",
                "checkedAt": checked_at,
            },
            kokoro,
            {
                "providerId": "cloud-speech",
                "kind": "speech",
                "executionLocation": "cloud",
                "status": "disabled",
                "capabilities": ["text_to_speech"],
                "redactedReason": "Cloud providers are disabled; no content was transmitted.",
                "checkedAt": checked_at,
            },
            {
                "providerId": "cloud-language",
                "kind": "language",
                "executionLocation": "cloud",
                "status": "disabled",
                "capabilities": ["story_analysis"],
                "redactedReason": "Cloud providers are disabled; no content was transmitted.",
                "checkedAt": checked_at,
            },
        ]

    def _kokoro_health(self, checked_at: str) -> dict[str, Any]:
        base = {
            "providerId": "kokoro-docker-dev",
            "kind": "speech",
            "executionLocation": "local",
            "capabilities": ["text_to_speech"],
            "checkedAt": checked_at,
        }
        if not self.settings.kokoro_development_enabled:
            return {
                **base,
                "status": "unavailable",
                "redactedReason": "The development-only Kokoro adapter is not enabled.",
            }
        parsed = urlsplit(self.settings.kokoro_development_url)
        # ServiceSettings validates the exact loopback boundary. Keep this defensive check at the
        # adapter boundary so a future alternate composition cannot introduce SSRF.
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/health":
            return {
                **base,
                "status": "unavailable",
                "redactedReason": "The development Kokoro endpoint configuration is unsafe.",
            }
        connection: http.client.HTTPConnection | None = None
        try:
            connection = self.connection_factory(
                "127.0.0.1",
                parsed.port,
                timeout=self.settings.kokoro_probe_timeout_seconds,
            )
            connection.request(
                "GET",
                "/health",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "cinematic-story-service-health/1",
                },
            )
            response = connection.getresponse()
            body = response.read(4097)
            if len(body) > 4096:
                return {
                    **base,
                    "status": "degraded",
                    "redactedReason": "The development Kokoro health response was invalid.",
                }
            if not 200 <= response.status < 300:
                return {
                    **base,
                    "status": "unavailable",
                    "redactedReason": "The development Kokoro endpoint reported unavailable.",
                }
            if body:
                try:
                    decoded = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return {
                        **base,
                        "status": "degraded",
                        "redactedReason": "The development Kokoro health response was invalid.",
                    }
                if not isinstance(decoded, dict):
                    return {
                        **base,
                        "status": "degraded",
                        "redactedReason": "The development Kokoro health response was invalid.",
                    }
            return {
                **base,
                "status": "available",
                "redactedReason": "The development Kokoro endpoint answered a content-free probe.",
            }
        except (OSError, TimeoutError, http.client.HTTPException):
            return {
                **base,
                "status": "unavailable",
                "redactedReason": "The development Kokoro endpoint is unavailable.",
            }
        finally:
            if connection is not None:
                connection.close()
