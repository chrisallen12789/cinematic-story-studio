from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

SCHEMA_VERSION = "1.0.0"
SERVICE_VERSION = "0.1.0"
PROTOCOL_VERSION = "1.0.0"
ANALYZER_ID = "deterministic-story-analyzer"
ANALYZER_VERSION = "1.0.0"
CHECKPOINT_SCHEMA_VERSION = 1

_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"|?*;&`$]')
_WINDOWS_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def stable_id(namespace: str, *parts: object) -> str:
    seed = "\x1f".join((namespace, *(str(part) for part in parts)))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def request_fingerprint(value: Any) -> str:
    return sha256_text(canonical_json(value))


def safe_display_filename(raw_name: str | None) -> str:
    if raw_name is None or not raw_name.strip():
        raise ValueError("A source filename is required.")
    if len(raw_name) > 255:
        raise ValueError("The source filename is too long.")
    if _UNSAFE_FILENAME_CHARS.search(raw_name):
        raise ValueError("The source filename contains unsafe characters.")
    if raw_name in {".", ".."} or "/" in raw_name or "\\" in raw_name:
        raise ValueError("The source filename must not contain a path.")
    path = PurePath(raw_name)
    if path.is_absolute() or len(path.parts) != 1 or path.name != raw_name:
        raise ValueError("The source filename must not contain a path.")
    return raw_name


def resolve_beneath(root: Path, relative: str | Path) -> Path:
    root_resolved = root.resolve(strict=False)
    relative_path = Path(relative)
    if relative_path.is_absolute() or relative_path.drive:
        raise ValueError("Absolute paths are not accepted.")
    candidate = (root_resolved / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("The path escapes its managed root.") from exc
    return candidate


def ensure_private_directory(path: Path) -> Path:
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode)
        or bool(
            int(getattr(existing, "st_file_attributes", 0))
            & _WINDOWS_REPARSE_POINT_ATTRIBUTE
        )
    ):
        raise ValueError("Private storage directories must not be links or reparse points.")
    path.mkdir(parents=True, exist_ok=True)
    created = path.lstat()
    if (
        not stat.S_ISDIR(created.st_mode)
        or stat.S_ISLNK(created.st_mode)
        or bool(
            int(getattr(created, "st_file_attributes", 0))
            & _WINDOWS_REPARSE_POINT_ATTRIBUTE
        )
    ):
        raise ValueError("Private storage directories must be ordinary directories.")
    try:
        os.chmod(path, 0o700)
    except OSError:
        # Windows ACL ownership is authoritative; chmod is best-effort portability hardening.
        pass
    return path


def utf8_byte_offset(text: str, codepoint_offset: int) -> int:
    return len(text[:codepoint_offset].encode("utf-8"))


def line_and_column(text: str, codepoint_offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, codepoint_offset) + 1
    previous_newline = text.rfind("\n", 0, codepoint_offset)
    column = codepoint_offset - previous_newline
    return line, column


def text_span(
    *,
    source_document_id: str,
    text: str,
    start: int,
    end: int,
    text_sha256: str,
) -> dict[str, Any]:
    line, column = line_and_column(text, start)
    return {
        "sourceDocumentId": source_document_id,
        "offsetUnit": "unicode-code-point",
        "startOffset": start,
        "endOffset": end,
        "startUtf8Byte": utf8_byte_offset(text, start),
        "endUtf8Byte": utf8_byte_offset(text, end),
        "line": line,
        "column": column,
        "textSha256": text_sha256,
    }


def provenance(
    *,
    origin: str,
    actor_id: str,
    recorded_at: str,
    source_references: list[dict[str, Any]] | None = None,
    input_fingerprint: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "origin": origin,
        "recordedAt": recorded_at,
        "actorId": actor_id,
    }
    if source_references:
        result["sourceReferences"] = source_references
    if input_fingerprint is not None:
        result["inputFingerprint"] = input_fingerprint
    if notes is not None:
        result["notes"] = notes
    return result
