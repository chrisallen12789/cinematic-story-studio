from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ServiceError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: dict[str, str | int | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def envelope(self, correlation_id: str) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "correlationId": correlation_id,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}


def not_found(resource: str = "resource") -> ServiceError:
    return ServiceError(
        404,
        "RESOURCE_NOT_FOUND",
        f"The requested {resource} was not found.",
    )
