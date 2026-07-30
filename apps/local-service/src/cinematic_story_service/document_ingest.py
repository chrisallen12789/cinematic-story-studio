from __future__ import annotations

import codecs
import hashlib
import importlib.metadata
import io
import posixpath
import re
import stat
import sys
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from urllib.parse import unquote, urlsplit

from lxml import etree  # type: ignore[import-untyped]
from pypdf import PdfReader

from .errors import ServiceError
from .util import canonical_json, sha256_text, utc_now

INGEST_CONTRACT_VERSION = "1.0.0"
TEXT_ADAPTER_VERSION = "1.0.0"
DOCX_ADAPTER_VERSION = "1.0.0"
EPUB_ADAPTER_VERSION = "1.0.0"
PDF_ADAPTER_VERSION = "1.0.0"

MAX_ARCHIVE_MEMBERS = 2_048
MAX_ARCHIVE_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 100.0
MAX_ARCHIVE_PATH_DEPTH = 20
MAX_ARCHIVE_MEMBER_NAME_CHARACTERS = 512
MAX_EXTRACTED_CHARACTERS = 10_000_000
MAX_EXTRACTED_SECTIONS = 10_000
MAX_PDF_PAGES = 2_000
PARSER_DEADLINE_SECONDS = 30.0
PARSER_PROCESS_MEMORY_LIMIT_BYTES = 768 * 1024 * 1024
IMPORT_PREVIEW_CHARACTERS = 8_000

SupportedFormat = Literal["txt", "markdown", "docx", "epub", "pdf"]
ExtractionStatus = Literal["complete", "partial"]
Retryability = Literal["retryable", "not_retryable"]
SectionKind = Literal[
    "document",
    "heading",
    "paragraph",
    "list_item",
    "table",
    "footnote",
    "endnote",
    "chapter",
    "page",
]
CancellationCheck = Callable[[], bool]
ProgressCallback = Callable[[str, int], None]

_MEDIA_TYPES: dict[SupportedFormat, str] = {
    "txt": "text/plain",
    "markdown": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
}
_SUFFIX_FORMATS: dict[str, SupportedFormat] = {
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
    ".docx": "docx",
    ".epub": "epub",
    ".pdf": "pdf",
}
_ZIP_COMPRESSIONS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_XML_FORBIDDEN = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_PACKAGE_RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_DOCUMENT_RELATIONSHIP_TYPES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
}
_WORDPROCESSING_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
_EPUB_CONTAINER_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:container"
_EPUB_PACKAGE_NAMESPACE = "http://www.idpf.org/2007/opf"
_XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"


@dataclass(frozen=True, slots=True)
class DocumentProbe:
    contract_version: str
    declared_format: SupportedFormat
    detected_format: SupportedFormat
    media_type: str
    source_sha256: str
    source_byte_count: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
            "declaredFormat": self.declared_format,
            "detectedFormat": self.detected_format,
            "mediaType": self.media_type,
            "sourceSha256": self.source_sha256,
            "sourceByteCount": self.source_byte_count,
        }


@dataclass(frozen=True, slots=True)
class SourceLocation:
    kind: Literal["text", "package_part", "pdf_page"]
    member: str | None = None
    page: int | None = None
    start: int | None = None
    end: int | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "member": self.member,
            "page": self.page,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True, slots=True)
class ExtractedSection:
    ordinal: int
    kind: SectionKind
    title: str | None
    start: int
    end: int
    location: SourceLocation

    def to_wire(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "kind": self.kind,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "location": self.location.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class ExtractionWarning:
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    requires_human_review: bool

    def to_wire(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "requiresHumanReview": self.requires_human_review,
        }


@dataclass(frozen=True, slots=True)
class ImportManifest:
    contract_version: str
    original_preserved: bool
    source_sha256: str
    source_byte_count: int
    declared_format: SupportedFormat
    detected_format: SupportedFormat
    media_type: str
    extracted_text_sha256: str
    extracted_character_count: int
    section_count: int
    page_count: int | None

    def to_wire(self) -> dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
            "originalPreserved": self.original_preserved,
            "sourceSha256": self.source_sha256,
            "sourceByteCount": self.source_byte_count,
            "declaredFormat": self.declared_format,
            "detectedFormat": self.detected_format,
            "mediaType": self.media_type,
            "extractedTextSha256": self.extracted_text_sha256,
            "extractedCharacterCount": self.extracted_character_count,
            "sectionCount": self.section_count,
            "pageCount": self.page_count,
        }


@dataclass(frozen=True, slots=True)
class ParserExecutionRecord:
    contract_version: str
    adapter_id: str
    adapter_version: str
    parser_dependency: str
    parser_version: str
    started_at: str
    completed_at: str
    duration_ms: int
    retryability: Retryability
    network_access_permitted: Literal[False]
    status: ExtractionStatus
    limits_profile: dict[str, str | int | float]
    limits_fingerprint: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
            "adapterId": self.adapter_id,
            "adapterVersion": self.adapter_version,
            "parserDependency": self.parser_dependency,
            "parserVersion": self.parser_version,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "durationMs": self.duration_ms,
            "retryability": self.retryability,
            "networkAccessPermitted": self.network_access_permitted,
            "status": self.status,
            "limitsProfile": self.limits_profile,
            "limitsFingerprint": self.limits_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class DocumentExtractionRequest:
    contract_version: str
    source_path: Path
    display_name: str
    declared_format: SupportedFormat
    source_sha256: str
    source_byte_count: int
    deadline_seconds: float = PARSER_DEADLINE_SECONDS


@dataclass(frozen=True, slots=True)
class DocumentExtractionResult:
    contract_version: str
    adapter_id: str
    adapter_version: str
    parser_dependency: str
    parser_version: str
    source_sha256: str
    source_byte_count: int
    declared_format: SupportedFormat
    detected_format: SupportedFormat
    media_type: str
    canonical_text: str
    extracted_text_sha256: str
    sections: tuple[ExtractedSection, ...]
    warnings: tuple[ExtractionWarning, ...]
    confidence: float
    started_at: str
    completed_at: str
    retryability: Retryability
    review_required: Literal[True]
    provenance: dict[str, Any]
    page_count: int | None
    title: str | None
    status: ExtractionStatus
    encoding: str | None
    newline_style: str | None
    manifest: ImportManifest
    parser_execution: ParserExecutionRecord

    def manifest_json(self) -> str:
        return canonical_json(self.manifest.to_wire())

    def parser_execution_json(self) -> str:
        return canonical_json(self.parser_execution.to_wire())

    def to_wire(self) -> dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
            "adapterId": self.adapter_id,
            "adapterVersion": self.adapter_version,
            "parserDependency": self.parser_dependency,
            "parserVersion": self.parser_version,
            "sourceSha256": self.source_sha256,
            "sourceByteCount": self.source_byte_count,
            "declaredFormat": self.declared_format,
            "detectedFormat": self.detected_format,
            "mediaType": self.media_type,
            "canonicalText": self.canonical_text,
            "extractedTextSha256": self.extracted_text_sha256,
            "sections": [section.to_wire() for section in self.sections],
            "warnings": [warning.to_wire() for warning in self.warnings],
            "confidence": self.confidence,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "retryability": self.retryability,
            "reviewRequired": self.review_required,
            "provenance": self.provenance,
            "pageCount": self.page_count,
            "title": self.title,
            "status": self.status,
            "encoding": self.encoding,
            "newlineStyle": self.newline_style,
            "manifest": self.manifest.to_wire(),
            "parserExecution": self.parser_execution.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class ImportReviewDecision:
    contract_version: str
    decision_id: str
    project_id: str
    source_document_id: str
    extraction_revision: int
    decision: Literal["approved", "changes_requested", "rejected"]
    actor_classification: Literal["human"]
    decided_at: str
    warning_acknowledgements: tuple[str, ...]
    reason: str
    provenance: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
            "decisionId": self.decision_id,
            "projectId": self.project_id,
            "sourceDocumentId": self.source_document_id,
            "extractionRevision": self.extraction_revision,
            "decision": self.decision,
            "actorClassification": self.actor_classification,
            "decidedAt": self.decided_at,
            "warningAcknowledgements": list(self.warning_acknowledgements),
            "reason": self.reason,
            "provenance": self.provenance,
        }


class DocumentIngestAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    @property
    def formats(self) -> frozenset[SupportedFormat]: ...

    def extract(
        self,
        request: DocumentExtractionRequest,
        *,
        cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> DocumentExtractionResult: ...


def parser_limits_profile(deadline_seconds: float) -> dict[str, str | int | float]:
    """Return the exact parser limits that bind an extraction attempt."""

    return {
        "profileId": "secure-ingest-v1",
        "ingestContractVersion": INGEST_CONTRACT_VERSION,
        "archiveMembers": MAX_ARCHIVE_MEMBERS,
        "archiveMemberBytes": MAX_ARCHIVE_MEMBER_BYTES,
        "archiveExpandedBytes": MAX_ARCHIVE_EXPANDED_BYTES,
        "maximumCompressionRatio": MAX_ARCHIVE_COMPRESSION_RATIO,
        "archivePathDepth": MAX_ARCHIVE_PATH_DEPTH,
        "archiveMemberNameCodePoints": MAX_ARCHIVE_MEMBER_NAME_CHARACTERS,
        "canonicalTextCodePoints": MAX_EXTRACTED_CHARACTERS,
        "extractedSections": MAX_EXTRACTED_SECTIONS,
        "pdfPages": MAX_PDF_PAGES,
        "parserDeadlineMs": round(deadline_seconds * 1_000),
        "parserProcessMemoryBytes": PARSER_PROCESS_MEMORY_LIMIT_BYTES,
    }


def parser_limits_fingerprint(deadline_seconds: float) -> str:
    return sha256_text(canonical_json(parser_limits_profile(deadline_seconds)))


def detect_format(display_name: str, prefix: bytes) -> SupportedFormat:
    suffix = Path(display_name).suffix.casefold()
    suffix_format = _SUFFIX_FORMATS.get(suffix)
    if suffix_format is None:
        raise ServiceError(
            400,
            "UNSUPPORTED_IMPORT_FORMAT",
            "The selected document format is not supported.",
        )
    if suffix_format in {"txt", "markdown"}:
        if prefix.startswith((b"PK\x03\x04", b"%PDF-", b"MZ", b"\x7fELF")):
            raise ServiceError(
                400,
                "UNSAFE_FILE_SIGNATURE",
                "The document signature does not match its extension.",
            )
        return suffix_format
    if suffix_format in {"docx", "epub"} and not prefix.startswith(b"PK\x03\x04"):
        raise ServiceError(
            400,
            "IMPORT_FORMAT_MISMATCH",
            "The document signature does not match its extension.",
        )
    if suffix_format == "pdf" and not prefix.startswith(b"%PDF-"):
        raise ServiceError(
            400,
            "IMPORT_FORMAT_MISMATCH",
            "The document signature does not match its extension.",
        )
    return suffix_format


def validate_plain_text_source(raw: bytes) -> tuple[str, str]:
    """Perform the Phase 0 strict decode checks before a text job is accepted."""

    text, encoding = _decode_exact_text(raw)
    if not text:
        raise ServiceError(422, "EMPTY_SOURCE", "The source contains no text.")
    if "\x00" in text:
        raise ServiceError(
            400,
            "UNSAFE_TEXT_CONTENT",
            "The source contains unsupported binary control data.",
        )
    _require_bounded_text(text)
    return text, encoding


def probe_document(
    *,
    display_name: str,
    declared_format: str | None,
    prefix: bytes,
    source_sha256: str,
    source_byte_count: int,
) -> DocumentProbe:
    detected = detect_format(display_name, prefix)
    if declared_format is not None and declared_format not in _MEDIA_TYPES:
        raise ServiceError(
            400,
            "UNSUPPORTED_IMPORT_FORMAT",
            "The declared document format is not supported.",
        )
    if declared_format is not None and declared_format != detected:
        raise ServiceError(
            400,
            "IMPORT_FORMAT_MISMATCH",
            "The declared format does not match the document.",
        )
    return DocumentProbe(
        contract_version=INGEST_CONTRACT_VERSION,
        declared_format=detected,
        detected_format=detected,
        media_type=_MEDIA_TYPES[detected],
        source_sha256=source_sha256,
        source_byte_count=source_byte_count,
    )


def adapter_for(document_format: SupportedFormat) -> DocumentIngestAdapter:
    if document_format == "txt":
        return TextDocumentAdapter("txt")
    if document_format == "markdown":
        return TextDocumentAdapter("markdown")
    if document_format == "docx":
        return DocxDocumentAdapter()
    if document_format == "epub":
        return EpubDocumentAdapter()
    return PdfDocumentAdapter()


class _Deadline:
    def __init__(self, seconds: float, cancelled: CancellationCheck) -> None:
        if seconds <= 0 or seconds > PARSER_DEADLINE_SECONDS:
            raise ServiceError(
                400,
                "PARSER_DEADLINE_INVALID",
                "The extraction deadline is outside the supported range.",
            )
        self._expires = time.monotonic() + seconds
        self._cancelled = cancelled

    def check(self) -> None:
        if self._cancelled():
            raise ServiceError(
                409,
                "EXTRACTION_CANCELLED",
                "Document extraction was cancelled.",
            )
        if time.monotonic() > self._expires:
            raise ServiceError(
                422,
                "PARSER_TIMEOUT",
                "Document extraction exceeded its bounded deadline.",
                retryable=True,
            )


class TextDocumentAdapter:
    adapter_version = TEXT_ADAPTER_VERSION

    def __init__(self, document_format: Literal["txt", "markdown"]) -> None:
        self.formats: frozenset[SupportedFormat] = frozenset({document_format})
        self.document_format = document_format
        self.adapter_id = f"builtin-{document_format}"

    def extract(
        self,
        request: DocumentExtractionRequest,
        *,
        cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> DocumentExtractionResult:
        started_at = utc_now()
        started = time.monotonic()
        deadline = _Deadline(request.deadline_seconds, cancelled)
        deadline.check()
        progress("decode_text", 150_000)
        raw = _read_verified_source(request)
        text, encoding = validate_plain_text_source(raw)
        deadline.check()
        sections = _text_sections(text, self.document_format)
        deadline.check()
        progress("finalize_extraction", 900_000)
        return _result(
            request=request,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            parser_dependency="python-standard-library",
            parser_version=".".join(str(part) for part in sys.version_info[:3]),
            text=text,
            sections=sections,
            warnings=(),
            confidence=1.0,
            title=_first_title(sections, text, request.display_name),
            page_count=None,
            status="complete",
            encoding=encoding,
            newline_style=_newline_style(text),
            started_at=started_at,
            started=started,
        )


class _SafeZip:
    def __init__(self, source: io.BytesIO) -> None:
        try:
            self.archive = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ServiceError(
                422,
                "MALFORMED_ARCHIVE",
                "The document package is malformed or truncated.",
            ) from exc
        self._members: dict[str, zipfile.ZipInfo] = {}
        self._validate()

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> _SafeZip:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _validate(self) -> None:
        infos = self.archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ServiceError(
                422,
                "ARCHIVE_MEMBER_LIMIT",
                "The document package contains too many members.",
            )
        expanded_total = 0
        folded: set[str] = set()
        for info in infos:
            name = _safe_member_name(info.filename)
            folded_name = name.casefold()
            if folded_name in folded:
                raise ServiceError(
                    422,
                    "ARCHIVE_DUPLICATE_MEMBER",
                    "The document package contains ambiguous member names.",
                )
            folded.add(folded_name)
            if info.flag_bits & 0x1:
                raise ServiceError(
                    422,
                    "ARCHIVE_ENCRYPTED_MEMBER",
                    "Encrypted document-package members are not supported.",
                )
            if info.compress_type not in _ZIP_COMPRESSIONS:
                raise ServiceError(
                    422,
                    "ARCHIVE_COMPRESSION_UNSUPPORTED",
                    "The document package uses an unsupported compression method.",
                )
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and (unix_mode & 0o170000) not in {0, 0o100000, 0o040000}:
                raise ServiceError(
                    422,
                    "ARCHIVE_UNSAFE_MEMBER",
                    "The document package contains an unsafe member type.",
                )
            if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                raise ServiceError(
                    422,
                    "ARCHIVE_MEMBER_SIZE_LIMIT",
                    "A document-package member exceeds the expansion limit.",
                )
            expanded_total += info.file_size
            if expanded_total > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ServiceError(
                    422,
                    "ARCHIVE_EXPANDED_SIZE_LIMIT",
                    "The document package exceeds the total expansion limit.",
                )
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                raise ServiceError(
                    422,
                    "ARCHIVE_COMPRESSION_RATIO_LIMIT",
                    "The document package has a suspicious compression ratio.",
                )
            self._members[name] = info

    def has(self, name: str) -> bool:
        return name in self._members

    def names(self) -> tuple[str, ...]:
        return tuple(self._members)

    def read(self, name: str) -> bytes:
        info = self._members.get(name)
        if info is None:
            raise ServiceError(
                422,
                "PACKAGE_IDENTITY_INVALID",
                "The document package is missing a required member.",
            )
        try:
            with self.archive.open(info, "r") as member:
                value = member.read(info.file_size + 1)
                has_trailing_data = bool(member.read(1))
        except (EOFError, RuntimeError, ValueError, zipfile.BadZipFile, OSError) as exc:
            raise ServiceError(
                422,
                "MALFORMED_ARCHIVE",
                "The document package could not be read safely.",
            ) from exc
        if len(value) > info.file_size or has_trailing_data:
            raise ServiceError(
                422,
                "ARCHIVE_MEMBER_SIZE_LIMIT",
                "A document-package member exceeded its declared limit.",
            )
        if len(value) != info.file_size:
            raise ServiceError(
                422,
                "MALFORMED_ARCHIVE",
                "A document-package member did not match its declared size.",
            )
        return value

    def first(self) -> str | None:
        infos = self.archive.infolist()
        return _safe_member_name(infos[0].filename) if infos else None


class DocxDocumentAdapter:
    adapter_id = "ooxml-docx"
    adapter_version = DOCX_ADAPTER_VERSION
    formats: frozenset[SupportedFormat] = frozenset({"docx"})

    def extract(
        self,
        request: DocumentExtractionRequest,
        *,
        cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> DocumentExtractionResult:
        started_at = utc_now()
        started = time.monotonic()
        deadline = _Deadline(request.deadline_seconds, cancelled)
        raw = _read_verified_source(request)
        deadline.check()
        progress("validate_package", 100_000)
        warnings: list[ExtractionWarning] = []
        with _SafeZip(io.BytesIO(raw)) as package:
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if not all(package.has(name) for name in required):
                raise ServiceError(
                    422,
                    "DOCX_IDENTITY_INVALID",
                    "The package is not a standard DOCX document.",
                )
            _validate_docx_content_types(package.read("[Content_Types].xml"))
            _validate_docx_root_relationships(package.read("_rels/.rels"))
            warnings.extend(_inspect_docx_package(package))
            deadline.check()
            progress("extract_document", 300_000)
            blocks = _docx_blocks(package.read("word/document.xml"), "word/document.xml")
            for note_name, kind in (
                ("word/footnotes.xml", "footnote"),
                ("word/endnotes.xml", "endnote"),
            ):
                if package.has(note_name):
                    blocks.extend(_docx_note_blocks(package.read(note_name), note_name, kind))
            deadline.check()
        text, sections = _join_blocks(blocks)
        _require_bounded_text(text)
        deadline.check()
        progress("finalize_extraction", 900_000)
        return _result(
            request=request,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            parser_dependency="lxml",
            parser_version=importlib.metadata.version("lxml"),
            text=text,
            sections=sections,
            warnings=tuple(warnings),
            confidence=0.97 if not warnings else 0.9,
            title=_first_title(sections, text, request.display_name),
            page_count=None,
            status="partial" if warnings else "complete",
            encoding=None,
            newline_style=None,
            started_at=started_at,
            started=started,
        )


class EpubDocumentAdapter:
    adapter_id = "epub-spine"
    adapter_version = EPUB_ADAPTER_VERSION
    formats: frozenset[SupportedFormat] = frozenset({"epub"})

    def extract(
        self,
        request: DocumentExtractionRequest,
        *,
        cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> DocumentExtractionResult:
        started_at = utc_now()
        started = time.monotonic()
        deadline = _Deadline(request.deadline_seconds, cancelled)
        raw = _read_verified_source(request)
        warnings: list[ExtractionWarning] = []
        with _SafeZip(io.BytesIO(raw)) as package:
            if package.first() != "mimetype":
                raise ServiceError(
                    422,
                    "EPUB_IDENTITY_INVALID",
                    "The EPUB mimetype member must be first.",
                )
            mimetype_info = package.archive.getinfo("mimetype")
            if (
                mimetype_info.compress_type != zipfile.ZIP_STORED
                or package.read("mimetype") != b"application/epub+zip"
            ):
                raise ServiceError(
                    422,
                    "EPUB_IDENTITY_INVALID",
                    "The package is not a standard EPUB document.",
                )
            progress("validate_package", 100_000)
            container = _secure_xml(
                package.read("META-INF/container.xml"),
                "EPUB_CONTAINER_INVALID",
            )
            container_name = etree.QName(container)
            if (
                container_name.localname != "container"
                or container_name.namespace != _EPUB_CONTAINER_NAMESPACE
                or container.get("version") != "1.0"
            ):
                raise ServiceError(
                    422,
                    "EPUB_CONTAINER_INVALID",
                    "The EPUB container identity is invalid.",
                )
            rootfile_nodes = container.xpath(
                "./container:rootfiles/container:rootfile",
                namespaces={"container": _EPUB_CONTAINER_NAMESPACE},
            )
            if len(rootfile_nodes) != 1:
                raise ServiceError(
                    422,
                    "EPUB_CONTAINER_INVALID",
                    "The EPUB container must identify exactly one package document.",
                )
            rootfile = rootfile_nodes[0]
            rootfile_path = rootfile.get("full-path")
            if not rootfile_path or rootfile.get("media-type") != "application/oebps-package+xml":
                raise ServiceError(
                    422,
                    "EPUB_CONTAINER_INVALID",
                    "The EPUB package-document declaration is invalid.",
                )
            parsed_rootfile = urlsplit(rootfile_path)
            if (
                parsed_rootfile.scheme
                or parsed_rootfile.netloc
                or parsed_rootfile.query
                or parsed_rootfile.fragment
            ):
                raise ServiceError(
                    422,
                    "EPUB_CONTAINER_INVALID",
                    "The EPUB package-document path is invalid.",
                )
            opf_name = _safe_member_name(unquote(parsed_rootfile.path))
            opf = _secure_xml(package.read(opf_name), "EPUB_PACKAGE_INVALID")
            opf_name_q = etree.QName(opf)
            if (
                opf_name_q.localname != "package"
                or opf_name_q.namespace != _EPUB_PACKAGE_NAMESPACE
                or opf.get("version") not in {"2.0", "3.0"}
            ):
                raise ServiceError(
                    422,
                    "EPUB_PACKAGE_INVALID",
                    "The EPUB package identity or version is invalid.",
                )
            manifest_nodes = opf.xpath(
                "./opf:manifest",
                namespaces={"opf": _EPUB_PACKAGE_NAMESPACE},
            )
            spine_nodes = opf.xpath(
                "./opf:spine",
                namespaces={"opf": _EPUB_PACKAGE_NAMESPACE},
            )
            metadata_nodes = opf.xpath(
                "./opf:metadata",
                namespaces={"opf": _EPUB_PACKAGE_NAMESPACE},
            )
            if len(manifest_nodes) != 1 or len(spine_nodes) != 1 or len(metadata_nodes) != 1:
                raise ServiceError(
                    422,
                    "EPUB_PACKAGE_INVALID",
                    "The EPUB package structure is invalid.",
                )
            package_dir = posixpath.dirname(opf_name)
            manifest: dict[str, tuple[str | None, str, str]] = {}
            for node in manifest_nodes[0].xpath(
                "./opf:item",
                namespaces={"opf": _EPUB_PACKAGE_NAMESPACE},
            ):
                item_id = node.get("id")
                href = node.get("href")
                media_type = node.get("media-type")
                properties = node.get("properties", "")
                if not item_id or not href or not media_type or item_id in manifest:
                    raise ServiceError(
                        422,
                        "EPUB_PACKAGE_INVALID",
                        "The EPUB manifest contains an invalid or duplicate item.",
                    )
                if _is_external_reference(href):
                    warnings.append(
                        _warning(
                            "EPUB_EXTERNAL_REFERENCE_OMITTED",
                            "External EPUB resources were not fetched.",
                        )
                    )
                    member = None
                else:
                    member = _resolve_epub_member(package_dir, href)
                if media_type not in {"application/xhtml+xml", "text/html"}:
                    warnings.append(
                        _warning(
                            "EPUB_NON_TEXT_CONTENT_OMITTED",
                            "Non-text EPUB resources were not included in canonical text.",
                        )
                    )
                manifest[item_id] = (member, media_type, properties)
            spine_ids = [
                value
                for value in spine_nodes[0].xpath(
                    "./opf:itemref/@idref",
                    namespaces={"opf": _EPUB_PACKAGE_NAMESPACE},
                )
                if isinstance(value, str)
            ]
            if not spine_ids or any(not value for value in spine_ids):
                raise ServiceError(
                    422,
                    "EPUB_SPINE_INVALID",
                    "The EPUB package does not contain a readable spine.",
                )
            title_values = metadata_nodes[0].xpath(".//*[local-name()='title']/text()")
            title = next(
                (
                    value.strip()
                    for value in title_values
                    if isinstance(value, str) and value.strip()
                ),
                None,
            )
            if title is not None:
                title = title[:255]
            blocks: list[_Block] = []
            for index, item_id in enumerate(spine_ids):
                deadline.check()
                entry = manifest.get(item_id)
                if entry is None:
                    raise ServiceError(
                        422,
                        "EPUB_SPINE_INVALID",
                        "The EPUB spine references a missing manifest item.",
                    )
                member, media_type, properties = entry
                if media_type not in {"application/xhtml+xml", "text/html"}:
                    warnings.append(
                        _warning(
                            "EPUB_SPINE_ITEM_OMITTED",
                            "A non-text EPUB spine item was omitted.",
                        )
                    )
                    continue
                if "scripted" in properties.split():
                    warnings.append(
                        _warning(
                            "EPUB_SCRIPTED_CONTENT_OMITTED",
                            "Scripted EPUB content was disabled and omitted.",
                        )
                    )
                if member is None:
                    raise ServiceError(
                        422,
                        "EPUB_REMOTE_SPINE_REFERENCE",
                        "The EPUB spine contains an external text reference.",
                    )
                document = _secure_xml(package.read(member), "EPUB_CONTENT_INVALID")
                document_name = etree.QName(document)
                if document_name.localname != "html" or (
                    media_type == "application/xhtml+xml"
                    and document_name.namespace != _XHTML_NAMESPACE
                ):
                    raise ServiceError(
                        422,
                        "EPUB_CONTENT_INVALID",
                        "An EPUB spine document has an invalid content identity.",
                    )
                blocks.extend(_epub_blocks(document, member, warnings))
                progress(
                    "extract_spine",
                    min(850_000, 150_000 + int(650_000 * (index + 1) / len(spine_ids))),
                )
        text, sections = _join_blocks(blocks)
        _require_bounded_text(text)
        deadline.check()
        return _result(
            request=request,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            parser_dependency="lxml",
            parser_version=importlib.metadata.version("lxml"),
            text=text,
            sections=sections,
            warnings=tuple(_unique_warnings(warnings)),
            confidence=0.95 if not warnings else 0.86,
            title=title or _first_title(sections, text, request.display_name),
            page_count=None,
            status="partial" if warnings else "complete",
            encoding=None,
            newline_style=None,
            started_at=started_at,
            started=started,
        )


class PdfDocumentAdapter:
    adapter_id = "pypdf-text"
    adapter_version = PDF_ADAPTER_VERSION
    formats: frozenset[SupportedFormat] = frozenset({"pdf"})

    def extract(
        self,
        request: DocumentExtractionRequest,
        *,
        cancelled: CancellationCheck,
        progress: ProgressCallback,
    ) -> DocumentExtractionResult:
        started_at = utc_now()
        started = time.monotonic()
        deadline = _Deadline(request.deadline_seconds, cancelled)
        raw = _read_verified_source(request)
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
        except Exception as exc:
            raise ServiceError(
                422,
                "PDF_MALFORMED",
                "The PDF is malformed or truncated.",
            ) from exc
        deadline.check()
        if reader.is_encrypted:
            raise ServiceError(
                422,
                "PDF_ENCRYPTED",
                "Encrypted or password-protected PDFs are not supported.",
            )
        page_count = len(reader.pages)
        deadline.check()
        if page_count == 0:
            raise ServiceError(422, "PDF_EMPTY", "The PDF contains no pages.")
        if page_count > MAX_PDF_PAGES:
            raise ServiceError(
                422,
                "PDF_PAGE_LIMIT",
                "The PDF exceeds the supported page-count limit.",
            )
        warnings = _inspect_pdf(reader, deadline)
        blocks: list[_Block] = []
        extracted_count = 0
        for index, page in enumerate(reader.pages):
            deadline.check()
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                raise ServiceError(
                    422,
                    "PDF_TEXT_EXTRACTION_FAILED",
                    "Text could not be extracted from the PDF safely.",
                ) from exc
            page_text = page_text.replace("\x00", "")
            extracted_count += len(page_text)
            if extracted_count > MAX_EXTRACTED_CHARACTERS:
                raise ServiceError(
                    422,
                    "EXTRACTED_TEXT_LIMIT",
                    "The extracted document text exceeds the supported limit.",
                )
            blocks.append(
                _Block(
                    kind="page",
                    title=f"Page {index + 1}",
                    text=page_text,
                    location=SourceLocation(kind="pdf_page", page=index + 1),
                )
            )
            progress(
                "extract_pages",
                min(900_000, 100_000 + int(800_000 * (index + 1) / page_count)),
            )
        text, sections = _join_blocks(blocks)
        if len(text.strip()) < 16:
            raise ServiceError(
                422,
                "PDF_NO_EXTRACTABLE_TEXT",
                "The PDF has no usable text layer. OCR is not available.",
            )
        non_space = sum(not character.isspace() for character in text)
        if non_space < max(32, page_count * 4):
            warnings.append(
                _warning(
                    "PDF_NEAR_EMPTY_TEXT",
                    "The PDF text layer is unusually sparse; extraction quality may be limited.",
                )
            )
        metadata_title: str | None = None
        try:
            candidate = reader.metadata.title if reader.metadata is not None else None
            if isinstance(candidate, str) and candidate.strip():
                metadata_title = candidate.strip()[:255]
        except Exception:
            warnings.append(
                _warning(
                    "PDF_METADATA_OMITTED",
                    "Unusable PDF metadata was omitted.",
                )
            )
        deadline.check()
        return _result(
            request=request,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            parser_dependency="pypdf",
            parser_version=importlib.metadata.version("pypdf"),
            text=text,
            sections=sections,
            warnings=tuple(_unique_warnings(warnings)),
            confidence=0.82 if not warnings else 0.7,
            title=metadata_title or _first_title(sections, text, request.display_name),
            page_count=page_count,
            status="partial" if warnings else "complete",
            encoding=None,
            newline_style=None,
            started_at=started_at,
            started=started,
        )


@dataclass(frozen=True, slots=True)
class _Block:
    kind: SectionKind
    title: str | None
    text: str
    location: SourceLocation


def _result(
    *,
    request: DocumentExtractionRequest,
    adapter_id: str,
    adapter_version: str,
    parser_dependency: str,
    parser_version: str,
    text: str,
    sections: tuple[ExtractedSection, ...],
    warnings: tuple[ExtractionWarning, ...],
    confidence: float,
    title: str | None,
    page_count: int | None,
    status: ExtractionStatus,
    encoding: str | None,
    newline_style: str | None,
    started_at: str,
    started: float,
) -> DocumentExtractionResult:
    if not text:
        raise ServiceError(422, "EMPTY_SOURCE", "The document contains no extractable text.")
    completed_at = utc_now()
    duration_ms = max(0, int((time.monotonic() - started) * 1_000))
    text_hash = sha256_text(text)
    manifest = ImportManifest(
        contract_version=INGEST_CONTRACT_VERSION,
        original_preserved=True,
        source_sha256=request.source_sha256,
        source_byte_count=request.source_byte_count,
        declared_format=request.declared_format,
        detected_format=request.declared_format,
        media_type=_MEDIA_TYPES[request.declared_format],
        extracted_text_sha256=text_hash,
        extracted_character_count=len(text),
        section_count=len(sections),
        page_count=page_count,
    )
    execution = ParserExecutionRecord(
        contract_version=INGEST_CONTRACT_VERSION,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        parser_dependency=parser_dependency,
        parser_version=parser_version,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        retryability="not_retryable",
        network_access_permitted=False,
        status=status,
        limits_profile=parser_limits_profile(request.deadline_seconds),
        limits_fingerprint=parser_limits_fingerprint(request.deadline_seconds),
    )
    return DocumentExtractionResult(
        contract_version=INGEST_CONTRACT_VERSION,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        parser_dependency=parser_dependency,
        parser_version=parser_version,
        source_sha256=request.source_sha256,
        source_byte_count=request.source_byte_count,
        declared_format=request.declared_format,
        detected_format=request.declared_format,
        media_type=_MEDIA_TYPES[request.declared_format],
        canonical_text=text,
        extracted_text_sha256=text_hash,
        sections=sections,
        warnings=warnings,
        confidence=confidence,
        started_at=started_at,
        completed_at=completed_at,
        retryability="not_retryable",
        review_required=True,
        provenance={
            "contractVersion": INGEST_CONTRACT_VERSION,
            "origin": "import",
            "actorId": adapter_id,
            "recordedAt": completed_at,
            "inputFingerprint": request.source_sha256,
            "notes": "Locally extracted derived text; original bytes preserved.",
        },
        page_count=page_count,
        title=title,
        status=status,
        encoding=encoding,
        newline_style=newline_style,
        manifest=manifest,
        parser_execution=execution,
    )


def _read_verified_source(request: DocumentExtractionRequest) -> bytes:
    try:
        metadata = request.source_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or request.source_path.is_symlink():
            raise OSError
        if metadata.st_size != request.source_byte_count:
            raise OSError
        raw = request.source_path.read_bytes()
    except OSError as exc:
        raise ServiceError(
            409,
            "SOURCE_INTEGRITY_FAILED",
            "The preserved source failed integrity verification.",
        ) from exc
    if hashlib.sha256(raw).hexdigest() != request.source_sha256:
        raise ServiceError(
            409,
            "SOURCE_INTEGRITY_FAILED",
            "The preserved source failed integrity verification.",
        )
    return raw


def _decode_exact_text(raw: bytes) -> tuple[str, str]:
    if raw.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8"
    try:
        return raw.decode(encoding, errors="strict"), encoding
    except UnicodeError as exc:
        raise ServiceError(
            400,
            "SOURCE_DECODE_FAILED",
            "The source is not valid UTF-8 or BOM-marked UTF-16 text.",
        ) from exc


def _require_bounded_text(text: str) -> None:
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise ServiceError(
            422,
            "EXTRACTED_TEXT_LIMIT",
            "The extracted document text exceeds the supported limit.",
        )


def _safe_member_name(raw: str) -> str:
    if (
        not raw
        or len(raw) > MAX_ARCHIVE_MEMBER_NAME_CHARACTERS
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
        or "\\" in raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
    ):
        raise ServiceError(
            422,
            "ARCHIVE_UNSAFE_PATH",
            "The document package contains an unsafe member path.",
        )
    path = PurePosixPath(raw)
    if len(path.parts) > MAX_ARCHIVE_PATH_DEPTH or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ServiceError(
            422,
            "ARCHIVE_UNSAFE_PATH",
            "The document package contains an unsafe member path.",
        )
    return path.as_posix().rstrip("/")


def _secure_xml(raw: bytes, code: str) -> etree._Element:
    if len(raw) > MAX_ARCHIVE_MEMBER_BYTES or _XML_FORBIDDEN.search(raw):
        raise ServiceError(422, code, "The document contains unsafe XML.")
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
        remove_comments=True,
    )
    try:
        return etree.fromstring(raw, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ServiceError(422, code, "The document contains malformed XML.") from exc


def _validate_docx_content_types(raw: bytes) -> None:
    root = _secure_xml(raw, "DOCX_IDENTITY_INVALID")
    root_name = etree.QName(root)
    if root_name.localname != "Types" or root_name.namespace != _CONTENT_TYPES_NAMESPACE:
        raise ServiceError(
            422,
            "DOCX_IDENTITY_INVALID",
            "The Word package content-type declaration is invalid.",
        )
    declarations = root.xpath(
        "./types:Default | ./types:Override",
        namespaces={"types": _CONTENT_TYPES_NAMESPACE},
    )
    values = [str(node.get("ContentType", "")) for node in declarations]
    standard = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    main_declarations = [
        node
        for node in declarations
        if etree.QName(node).localname == "Override"
        and node.get("PartName") == "/word/document.xml"
    ]
    if (
        len(main_declarations) != 1
        or main_declarations[0].get("ContentType") != standard
        or any("macroenabled" in value.casefold() for value in values)
    ):
        raise ServiceError(
            422,
            "DOCX_MACRO_OR_IDENTITY_INVALID",
            "Macro-enabled or non-standard Word documents are not supported.",
        )


def _validate_docx_root_relationships(raw: bytes) -> None:
    root = _secure_xml(raw, "DOCX_RELATIONSHIPS_INVALID")
    root_name = etree.QName(root)
    if (
        root_name.localname != "Relationships"
        or root_name.namespace != _PACKAGE_RELATIONSHIPS_NAMESPACE
    ):
        raise ServiceError(
            422,
            "DOCX_RELATIONSHIPS_INVALID",
            "The Word package relationship declaration is invalid.",
        )
    office_documents = [
        node
        for node in root.xpath(
            "./relationships:Relationship",
            namespaces={"relationships": _PACKAGE_RELATIONSHIPS_NAMESPACE},
        )
        if node.get("Type") in _OFFICE_DOCUMENT_RELATIONSHIP_TYPES
    ]
    if len(office_documents) != 1:
        raise ServiceError(
            422,
            "DOCX_IDENTITY_INVALID",
            "The Word package must identify exactly one main document.",
        )
    relationship = office_documents[0]
    target = str(relationship.get("Target", ""))
    parsed_target = urlsplit(target)
    if (
        str(relationship.get("TargetMode", "")).casefold() == "external"
        or target.startswith("/")
        or parsed_target.scheme
        or parsed_target.netloc
        or parsed_target.query
        or parsed_target.fragment
        or _safe_member_name(unquote(parsed_target.path)) != "word/document.xml"
    ):
        raise ServiceError(
            422,
            "DOCX_IDENTITY_INVALID",
            "The Word package main-document relationship is invalid.",
        )


def _inspect_docx_package(package: _SafeZip) -> list[ExtractionWarning]:
    warnings: list[ExtractionWarning] = []
    names = tuple(name.casefold() for name in package.names())
    if any(
        marker in name
        for name in names
        for marker in (
            "vbaproject",
            "/activex/",
            "/customui/",
            "/macrosheets/",
        )
    ):
        raise ServiceError(
            422,
            "DOCX_ACTIVE_CONTENT",
            "The Word document contains unsupported active content.",
        )
    if any("/embeddings/" in name or name.endswith(".bin") for name in names):
        warnings.append(
            _warning(
                "DOCX_EMBEDDED_OBJECT_OMITTED",
                "Embedded document objects were not extracted.",
            )
        )
    if any("/media/" in name for name in names):
        warnings.append(
            _warning(
                "DOCX_MEDIA_OMITTED",
                "Images and other Word-document media were not extracted.",
            )
        )
    if any(
        re.search(r"/(?:header|footer)\d*\.xml$", name) or name.endswith("/comments.xml")
        for name in names
    ):
        warnings.append(
            _warning(
                "DOCX_AUXILIARY_CONTENT_OMITTED",
                "Headers, footers, or comments were not included in canonical text.",
            )
        )
    document = _secure_xml(package.read("word/document.xml"), "DOCX_DOCUMENT_INVALID")
    if document.xpath(
        "//*[local-name()='altChunk' or local-name()='object' or local-name()='oleObject']"
    ):
        warnings.append(
            _warning(
                "DOCX_UNSUPPORTED_CONTENT_OMITTED",
                "Unsupported linked or embedded Word-document content was omitted.",
            )
        )
    if document.xpath("//*[local-name()='del' or local-name()='moveFrom']"):
        warnings.append(
            _warning(
                "DOCX_TRACKED_DELETIONS_OMITTED",
                "Tracked deletions were omitted from canonical text.",
            )
        )
    if package.has("word/footnotes.xml") or package.has("word/endnotes.xml"):
        warnings.append(
            _warning(
                "DOCX_NOTES_APPENDED",
                "Footnotes or endnotes were appended after the main document text.",
            )
        )
    for name in package.names():
        if not name.casefold().endswith(".rels"):
            continue
        relationships = _secure_xml(
            package.read(name),
            "DOCX_RELATIONSHIPS_INVALID",
        )
        relationships_name = etree.QName(relationships)
        if (
            relationships_name.localname != "Relationships"
            or relationships_name.namespace != _PACKAGE_RELATIONSHIPS_NAMESPACE
        ):
            raise ServiceError(
                422,
                "DOCX_RELATIONSHIPS_INVALID",
                "A Word relationship declaration is invalid.",
            )
        relationship_nodes = relationships.xpath(
            "./relationships:Relationship",
            namespaces={"relationships": _PACKAGE_RELATIONSHIPS_NAMESPACE},
        )
        if any(
            str(node.get("TargetMode", "")).casefold() == "external" for node in relationship_nodes
        ):
            warnings.append(
                _warning(
                    "DOCX_EXTERNAL_RELATIONSHIP_OMITTED",
                    "External Word-document relationships were not followed.",
                )
            )
    return _unique_warnings(warnings)


def _docx_blocks(raw: bytes, member: str) -> list[_Block]:
    root = _secure_xml(raw, "DOCX_DOCUMENT_INVALID")
    root_name = etree.QName(root)
    if root_name.localname != "document" or root_name.namespace not in _WORDPROCESSING_NAMESPACES:
        raise ServiceError(
            422,
            "DOCX_DOCUMENT_INVALID",
            "The Word main-document identity is invalid.",
        )
    bodies = root.xpath("./*[local-name()='body']")
    if len(bodies) != 1:
        raise ServiceError(
            422,
            "DOCX_DOCUMENT_INVALID",
            "The Word document body is invalid.",
        )
    return _docx_container_blocks(bodies[0], member)


def _docx_container_blocks(container: etree._Element, member: str) -> list[_Block]:
    blocks: list[_Block] = []
    for child in container:
        local = etree.QName(child).localname
        if local == "p":
            value = _docx_paragraph(child)
            if value is None:
                continue
            text, kind, title = value
            blocks.append(
                _Block(
                    kind=kind,
                    title=title,
                    text=text,
                    location=SourceLocation(kind="package_part", member=member),
                )
            )
        elif local == "tbl":
            rows: list[str] = []
            for row in child.xpath(".//*[local-name()='tr']"):
                cells: list[str] = []
                for cell in row.xpath("./*[local-name()='tc']"):
                    paragraphs = [
                        value[0]
                        for paragraph in cell.xpath(".//*[local-name()='p']")
                        if (value := _docx_paragraph(paragraph)) is not None
                    ]
                    cells.append("\n".join(paragraphs))
                rows.append("\t".join(cells))
            table_text = "\n".join(rows).strip()
            if table_text:
                blocks.append(
                    _Block(
                        kind="table",
                        title=None,
                        text=table_text,
                        location=SourceLocation(kind="package_part", member=member),
                    )
                )
    return blocks


def _docx_paragraph(node: etree._Element) -> tuple[str, Any, str | None] | None:
    fragments: list[str] = []
    for descendant in node.iter():
        local = etree.QName(descendant).localname
        if local in {"t", "delText", "instrText"} and descendant.text:
            if local == "t":
                fragments.append(descendant.text)
        elif local == "tab":
            fragments.append("\t")
        elif local in {"br", "cr"}:
            fragments.append("\n")
    text = "".join(fragments)
    if not text.strip():
        return None
    styles = node.xpath("./*[local-name()='pPr']/*[local-name()='pStyle']/@*[local-name()='val']")
    style = styles[0] if styles and isinstance(styles[0], str) else ""
    numbered = bool(node.xpath("./*[local-name()='pPr']/*[local-name()='numPr']"))
    if style.casefold().startswith("heading"):
        return text, "heading", text.strip()[:255]
    return text, "list_item" if numbered else "paragraph", None


def _docx_note_blocks(raw: bytes, member: str, kind: str) -> list[_Block]:
    root = _secure_xml(raw, "DOCX_NOTES_INVALID")
    blocks: list[_Block] = []
    for note in root:
        note_id_values = note.xpath("./@*[local-name()='id']")
        if note_id_values and str(note_id_values[0]).startswith("-"):
            continue
        text = "\n".join(
            value[0]
            for paragraph in note.xpath(".//*[local-name()='p']")
            if (value := _docx_paragraph(paragraph)) is not None
        ).strip()
        if text:
            blocks.append(
                _Block(
                    kind=kind,  # type: ignore[arg-type]
                    title=None,
                    text=text,
                    location=SourceLocation(kind="package_part", member=member),
                )
            )
    return blocks


def _resolve_epub_member(package_dir: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise ServiceError(
            422,
            "EPUB_REMOTE_SPINE_REFERENCE",
            "The EPUB spine contains an external reference.",
        )
    decoded = unquote(parsed.path)
    if any(part == ".." for part in PurePosixPath(decoded).parts):
        raise ServiceError(
            422,
            "ARCHIVE_UNSAFE_PATH",
            "The EPUB package contains an unsafe member reference.",
        )
    return _safe_member_name(posixpath.normpath(posixpath.join(package_dir, decoded)))


def _is_external_reference(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(parsed.scheme or parsed.netloc)


def _epub_blocks(
    root: etree._Element,
    member: str,
    warnings: list[ExtractionWarning],
) -> list[_Block]:
    if root.xpath("//*[local-name()='script']"):
        warnings.append(
            _warning(
                "EPUB_SCRIPT_OMITTED",
                "EPUB scripts were disabled and omitted.",
            )
        )
    external = False
    for value in root.xpath("//@href | //@src"):
        if not isinstance(value, str):
            continue
        if _is_external_reference(value):
            external = True
            break
    if external:
        warnings.append(
            _warning(
                "EPUB_EXTERNAL_REFERENCE_OMITTED",
                "External EPUB resources were not fetched.",
            )
        )
    blocks: list[_Block] = []
    block_names = {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
        "blockquote",
        "pre",
        "td",
        "th",
    }
    for node in root.iter():
        local = etree.QName(node).localname.casefold()
        if local not in block_names:
            continue
        if any(
            etree.QName(parent).localname.casefold() in block_names
            for parent in node.iterancestors()
        ):
            continue
        text = "".join(
            str(value)
            for value in node.xpath(
                ".//text()[not(ancestor::*[local-name()='script' or local-name()='style'])]"
            )
        ).strip()
        if not text:
            continue
        kind: Any
        title: str | None
        if local.startswith("h") and len(local) == 2 and local[1].isdigit():
            kind, title = "heading", text[:255]
        elif local == "li":
            kind, title = "list_item", None
        elif local in {"td", "th"}:
            kind, title = "table", None
        else:
            kind, title = "paragraph", None
        blocks.append(
            _Block(
                kind=kind,
                title=title,
                text=text,
                location=SourceLocation(kind="package_part", member=member),
            )
        )
    return blocks


def _inspect_pdf(reader: PdfReader, deadline: _Deadline) -> list[ExtractionWarning]:
    warnings: list[ExtractionWarning] = []
    try:
        root = reader.trailer.get("/Root")
        keys = set(root.keys()) if root is not None else set()
    except Exception:
        keys = set()
        warnings.append(
            _warning(
                "PDF_CATALOG_PARTIAL",
                "Some PDF catalog metadata could not be inspected.",
            )
        )
    active_keys = {"/OpenAction", "/AA", "/AcroForm"}
    if keys & active_keys:
        warnings.append(
            _warning(
                "PDF_ACTIVE_CONTENT_IGNORED",
                "PDF actions or forms were treated as untrusted and ignored.",
            )
        )
    if "/Names" in keys:
        warnings.append(
            _warning(
                "PDF_NAMED_CONTENT_IGNORED",
                "PDF named resources, including possible attachments, were ignored.",
            )
        )
    for page in reader.pages:
        deadline.check()
        if "/Annots" in page:
            warnings.append(
                _warning(
                    "PDF_ANNOTATIONS_IGNORED",
                    "PDF annotations were not included in extracted text.",
                )
            )
            break
    return warnings


def _text_sections(
    text: str,
    document_format: Literal["txt", "markdown"],
) -> tuple[ExtractedSection, ...]:
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        match = (
            re.match(r"^#{1,6}\s+(.+?)\s*#*$", stripped)
            if document_format == "markdown"
            else re.match(r"^(?:chapter|part)\s+.+$", stripped, re.IGNORECASE)
        )
        if match:
            title = match.group(1).strip() if document_format == "markdown" else stripped.strip()
            headings.append((offset, offset + len(stripped), title[:255]))
            if len(headings) > MAX_EXTRACTED_SECTIONS:
                raise ServiceError(
                    422,
                    "EXTRACTED_SECTION_LIMIT",
                    "The extracted document contains too many structural sections.",
                )
        offset += len(line)
    if not headings:
        return (
            ExtractedSection(
                ordinal=0,
                kind="document",
                title=None,
                start=0,
                end=len(text),
                location=SourceLocation(kind="text", start=0, end=len(text)),
            ),
        )
    sections: list[ExtractedSection] = []
    for index, (start, heading_end, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        sections.append(
            ExtractedSection(
                ordinal=index,
                kind="heading" if document_format == "markdown" else "chapter",
                title=title,
                start=start,
                end=end,
                location=SourceLocation(kind="text", start=start, end=heading_end),
            )
        )
    return tuple(sections)


def _join_blocks(blocks: Iterable[_Block]) -> tuple[str, tuple[ExtractedSection, ...]]:
    text_parts: list[str] = []
    sections: list[ExtractedSection] = []
    offset = 0
    for block in blocks:
        if not block.text:
            continue
        if text_parts:
            text_parts.append("\n\n")
            offset += 2
        start = offset
        text_parts.append(block.text)
        offset += len(block.text)
        sections.append(
            ExtractedSection(
                ordinal=len(sections),
                kind=block.kind,
                title=block.title,
                start=start,
                end=offset,
                location=block.location,
            )
        )
        if len(sections) > MAX_EXTRACTED_SECTIONS:
            raise ServiceError(
                422,
                "EXTRACTED_SECTION_LIMIT",
                "The extracted document contains too many structural sections.",
            )
        if offset > MAX_EXTRACTED_CHARACTERS:
            raise ServiceError(
                422,
                "EXTRACTED_TEXT_LIMIT",
                "The extracted document text exceeds the supported limit.",
            )
    return "".join(text_parts), tuple(sections)


def _warning(code: str, message: str) -> ExtractionWarning:
    return ExtractionWarning(
        code=code,
        severity="warning",
        message=message,
        requires_human_review=True,
    )


def _unique_warnings(
    values: Iterable[ExtractionWarning],
) -> list[ExtractionWarning]:
    seen: set[str] = set()
    result: list[ExtractionWarning] = []
    for value in values:
        if value.code not in seen:
            seen.add(value.code)
            result.append(value)
    return result


def _first_title(
    sections: tuple[ExtractedSection, ...],
    text: str,
    display_name: str,
) -> str:
    for section in sections:
        if section.title:
            return section.title[:255]
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return (first_line or Path(display_name).stem or "Imported document")[:255]


def _newline_style(text: str) -> str:
    crlf_count = text.count("\r\n")
    without_crlf = text.replace("\r\n", "")
    lf_count = without_crlf.count("\n")
    cr_count = without_crlf.count("\r")
    styles = sum(count > 0 for count in (crlf_count, lf_count, cr_count))
    if styles == 0:
        return "none"
    if styles > 1:
        return "mixed"
    if crlf_count:
        return "crlf"
    if lf_count:
        return "lf"
    return "cr"
