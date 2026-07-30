from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from cinematic_story_service import document_ingest
from cinematic_story_service.document_ingest import (
    INGEST_CONTRACT_VERSION,
    MAX_ARCHIVE_EXPANDED_BYTES,
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ARCHIVE_MEMBER_NAME_CHARACTERS,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_PATH_DEPTH,
    MAX_EXTRACTED_CHARACTERS,
    MAX_EXTRACTED_SECTIONS,
    PARSER_PROCESS_MEMORY_LIMIT_BYTES,
    DocumentExtractionRequest,
    EpubDocumentAdapter,
    PdfDocumentAdapter,
    SupportedFormat,
    adapter_for,
    probe_document,
)
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.util import canonical_json

_FIXTURES = Path(__file__).parents[3] / "fixtures" / "synthetic-story"


def _base64_fixture(name: str) -> bytes:
    encoded = (_FIXTURES / f"sample-story.{name}.base64").read_text("ascii")
    compact = "".join(encoded.splitlines())
    assert compact and len(compact) % 4 == 0
    return base64.b64decode(compact, validate=True)


def _request(
    tmp_path: Path,
    *,
    name: str,
    document_format: SupportedFormat,
    content: bytes,
    deadline_seconds: float = 30,
) -> DocumentExtractionRequest:
    source = tmp_path / name
    source.write_bytes(content)
    return DocumentExtractionRequest(
        contract_version=INGEST_CONTRACT_VERSION,
        source_path=source,
        display_name=name,
        declared_format=document_format,
        source_sha256=hashlib.sha256(content).hexdigest(),
        source_byte_count=len(content),
        deadline_seconds=deadline_seconds,
    )


def _extract(
    request: DocumentExtractionRequest,
    *,
    cancelled: bool = False,
) -> Any:
    progress: list[tuple[str, int]] = []
    result = adapter_for(request.declared_format).extract(
        request,
        cancelled=lambda: cancelled,
        progress=lambda stage, value: progress.append((stage, value)),
    )
    assert progress
    assert all(0 <= value < 1_000_000 for _, value in progress)
    return result


@pytest.mark.parametrize(
    ("document_format", "fixture_name", "file_name"),
    [
        ("txt", "txt", "sample-story.txt"),
        ("markdown", "md", "sample-story.md"),
        ("docx", "docx", "sample-story.docx"),
        ("epub", "epub", "sample-story.epub"),
        ("pdf", "pdf", "sample-story.pdf"),
    ],
)
def test_each_adapter_extracts_bounded_synthetic_document_with_full_manifest(
    tmp_path: Path,
    document_format: SupportedFormat,
    fixture_name: str,
    file_name: str,
) -> None:
    content = (
        (_FIXTURES / file_name).read_bytes()
        if fixture_name in {"txt", "md"}
        else _base64_fixture(fixture_name)
    )
    request = _request(
        tmp_path,
        name=file_name,
        document_format=document_format,
        content=content,
    )

    result = _extract(request)

    assert result.source_sha256 == hashlib.sha256(content).hexdigest()
    assert result.source_byte_count == len(content)
    assert result.declared_format == document_format
    assert result.detected_format == document_format
    assert result.canonical_text
    assert (
        result.extracted_text_sha256
        == hashlib.sha256(result.canonical_text.encode("utf-8")).hexdigest()
    )
    assert result.sections
    assert result.review_required is True
    assert result.manifest.original_preserved is True
    assert result.parser_execution.network_access_permitted is False
    manifest_record = json.loads(result.manifest_json())
    parser_record = json.loads(result.parser_execution_json())
    extraction_record = result.to_wire()
    assert manifest_record["sourceSha256"] == result.source_sha256
    assert "source_sha256" not in manifest_record
    assert parser_record["limitsProfile"]["profileId"] == "secure-ingest-v1"
    assert (
        parser_record["limitsProfile"]["parserProcessMemoryBytes"]
        == PARSER_PROCESS_MEMORY_LIMIT_BYTES
        == 768 * 1024 * 1024
    )
    assert parser_record["limitsFingerprint"] == result.parser_execution.limits_fingerprint
    assert "limits_fingerprint" not in parser_record
    assert extraction_record["sourceSha256"] == result.source_sha256
    assert extraction_record["sections"][0]["location"]["kind"] in {
        "text",
        "package_part",
        "pdf_page",
    }
    assert "source_path" not in canonical_json(extraction_record)
    assert str(tmp_path) not in canonical_json(extraction_record)
    assert result.started_at <= result.completed_at
    assert all(
        section.start <= section.end <= len(result.canonical_text) for section in result.sections
    )
    if document_format in {"docx", "epub"}:
        assert all(
            section.location.kind == "package_part"
            and section.location.member
            and section.location.start is None
            and section.location.end is None
            for section in result.sections
        )
    if document_format in {"txt", "markdown"}:
        assert result.canonical_text == content.decode("utf-8")
        assert result.confidence == 1.0
        assert all(
            section.location.kind == "text"
            and section.location.start is not None
            and section.location.end is not None
            for section in result.sections
        )
    if document_format == "pdf":
        assert result.page_count == 3
        assert all(
            section.location.kind == "pdf_page"
            and section.location.page is not None
            and section.location.start is None
            and section.location.end is None
            for section in result.sections
        )


def test_import_review_decision_wire_record_is_explicit_and_path_free(tmp_path: Path) -> None:
    decision = document_ingest.ImportReviewDecision(
        contract_version=INGEST_CONTRACT_VERSION,
        decision_id="decision-1",
        project_id="project-1",
        source_document_id="source-1",
        extraction_revision=2,
        decision="approved",
        actor_classification="human",
        decided_at="2026-07-30T00:00:00.000Z",
        warning_acknowledgements=("DOCX_MEDIA_OMITTED",),
        reason="Reviewed synthetic evidence.",
        provenance={
            "contractVersion": INGEST_CONTRACT_VERSION,
            "origin": "human",
            "actorId": "local_user",
            "recordedAt": "2026-07-30T00:00:00.000Z",
        },
    )

    record = decision.to_wire()
    assert list(record) == [
        "contractVersion",
        "decisionId",
        "projectId",
        "sourceDocumentId",
        "extractionRevision",
        "decision",
        "actorClassification",
        "decidedAt",
        "warningAcknowledgements",
        "reason",
        "provenance",
    ]
    assert record["actorClassification"] == "human"
    assert record["sourceDocumentId"] == "source-1"
    assert str(tmp_path) not in canonical_json(record)


def test_format_probe_rejects_extension_magic_mismatches_and_docm() -> None:
    digest = "a" * 64
    with pytest.raises(ServiceError) as mismatch:
        probe_document(
            display_name="wrong.docx",
            declared_format="docx",
            prefix=b"plain text",
            source_sha256=digest,
            source_byte_count=10,
        )
    assert mismatch.value.code == "IMPORT_FORMAT_MISMATCH"

    with pytest.raises(ServiceError) as executable:
        probe_document(
            display_name="wrong.txt",
            declared_format="txt",
            prefix=b"MZunsafe",
            source_sha256=digest,
            source_byte_count=8,
        )
    assert executable.value.code == "UNSAFE_FILE_SIGNATURE"

    with pytest.raises(ServiceError) as docm:
        probe_document(
            display_name="macro.docm",
            declared_format="docx",
            prefix=b"PK\x03\x04",
            source_sha256=digest,
            source_byte_count=4,
        )
    assert docm.value.code == "UNSUPPORTED_IMPORT_FORMAT"


def _zip_bytes(
    entries: list[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    value = io.BytesIO()
    with zipfile.ZipFile(value, "w", compression=compression) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return value.getvalue()


def _replace_zip_members(content: bytes, replacements: dict[str, bytes]) -> bytes:
    value = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(value, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                replacements.get(info.filename, source.read(info.filename)),
            )
    return value.getvalue()


def _zip_bytes_with_declared_sizes(
    entries: list[tuple[str, int]],
) -> bytes:
    """Build compact metadata-only ZIP evidence for expansion-limit checks."""

    value = bytearray(
        _zip_bytes(
            [(name, b"") for name, _declared_size in entries],
            compression=zipfile.ZIP_STORED,
        )
    )
    central_offset = value.find(b"PK\x01\x02")
    assert central_offset >= 0
    for name, declared_size in entries:
        assert value[central_offset : central_offset + 4] == b"PK\x01\x02"
        name_length = struct.unpack_from("<H", value, central_offset + 28)[0]
        extra_length = struct.unpack_from("<H", value, central_offset + 30)[0]
        comment_length = struct.unpack_from("<H", value, central_offset + 32)[0]
        encoded_name = bytes(value[central_offset + 46 : central_offset + 46 + name_length])
        assert encoded_name.decode("utf-8") == name
        local_offset = struct.unpack_from("<I", value, central_offset + 42)[0]
        declared_compressed_size = max(1, (declared_size + 99) // 100)
        struct.pack_into(
            "<I",
            value,
            central_offset + 20,
            declared_compressed_size,
        )
        struct.pack_into("<I", value, central_offset + 24, declared_size)
        struct.pack_into(
            "<I",
            value,
            local_offset + 18,
            declared_compressed_size,
        )
        struct.pack_into("<I", value, local_offset + 22, declared_size)
        central_offset += 46 + name_length + extra_length + comment_length
    return bytes(value)


@pytest.mark.parametrize(
    ("entries", "expected_code"),
    [
        (
            [
                ("[Content_Types].xml", b"<Types/>"),
                ("../escape.xml", b"<escape/>"),
            ],
            "ARCHIVE_UNSAFE_PATH",
        ),
        (
            [
                ("[Content_Types].xml", b"<Types/>"),
                ("word/document.xml", b"A" * (1024 * 1024)),
            ],
            "ARCHIVE_COMPRESSION_RATIO_LIMIT",
        ),
        (
            [
                ("[Content_Types].xml", b"<!DOCTYPE x [<!ENTITY y 'unsafe'>]><Types/>"),
                ("_rels/.rels", b"<Relationships/>"),
                ("word/document.xml", b"<w:document/>"),
            ],
            "DOCX_IDENTITY_INVALID",
        ),
    ],
)
def test_docx_archive_and_xml_safety_boundaries(
    tmp_path: Path,
    entries: list[tuple[str, bytes]],
    expected_code: str,
) -> None:
    content = _zip_bytes(entries)
    request = _request(
        tmp_path,
        name="adversarial.docx",
        document_format="docx",
        content=content,
    )
    with pytest.raises(ServiceError) as raised:
        _extract(request)
    assert raised.value.code == expected_code
    assert str(tmp_path) not in raised.value.message


@pytest.mark.parametrize(
    ("member", "replacement", "expected_code"),
    [
        (
            "[Content_Types].xml",
            (
                b"<Types><Override PartName='/word/document.xml' "
                b"ContentType='application/vnd.openxmlformats-officedocument."
                b"wordprocessingml.document.main+xml'/></Types>"
            ),
            "DOCX_IDENTITY_INVALID",
        ),
        (
            "[Content_Types].xml",
            (
                b"<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
                b"<Override PartName='/word/other.xml' "
                b"ContentType='application/vnd.openxmlformats-officedocument."
                b"wordprocessingml.document.main+xml'/></Types>"
            ),
            "DOCX_MACRO_OR_IDENTITY_INVALID",
        ),
        (
            "_rels/.rels",
            (
                b"<Relationships><Relationship Id='rId1' "
                b"Type='http://schemas.openxmlformats.org/officeDocument/2006/"
                b"relationships/officeDocument' Target='word/document.xml'/>"
                b"</Relationships>"
            ),
            "DOCX_RELATIONSHIPS_INVALID",
        ),
        (
            "word/document.xml",
            b"<document><body><p><r><t>Arbitrary XML is not DOCX.</t></r></p></body></document>",
            "DOCX_DOCUMENT_INVALID",
        ),
    ],
)
def test_docx_requires_standard_package_and_main_document_identities(
    tmp_path: Path,
    member: str,
    replacement: bytes,
    expected_code: str,
) -> None:
    content = _replace_zip_members(
        _base64_fixture("docx"),
        {member: replacement},
    )

    with pytest.raises(ServiceError) as raised:
        _extract(
            _request(
                tmp_path,
                name="identity.docx",
                document_format="docx",
                content=content,
            )
        )

    assert raised.value.code == expected_code


def test_archive_member_count_is_bounded_before_extraction(tmp_path: Path) -> None:
    content = _zip_bytes(
        [(f"word/member-{index}.xml", b"<p/>") for index in range(MAX_ARCHIVE_MEMBERS + 1)],
        compression=zipfile.ZIP_STORED,
    )
    request = _request(
        tmp_path,
        name="members.docx",
        document_format="docx",
        content=content,
    )
    with pytest.raises(ServiceError) as raised:
        _extract(request)
    assert raised.value.code == "ARCHIVE_MEMBER_LIMIT"


@pytest.mark.parametrize(
    ("entries", "expected_code"),
    [
        (
            [("word/member.xml", MAX_ARCHIVE_MEMBER_BYTES + 1)],
            "ARCHIVE_MEMBER_SIZE_LIMIT",
        ),
        (
            [
                *[(f"word/expanded-{index}.xml", MAX_ARCHIVE_MEMBER_BYTES) for index in range(6)],
                (
                    "word/expanded-final.xml",
                    MAX_ARCHIVE_EXPANDED_BYTES - (6 * MAX_ARCHIVE_MEMBER_BYTES) + 1,
                ),
            ],
            "ARCHIVE_EXPANDED_SIZE_LIMIT",
        ),
    ],
)
def test_archive_declared_expansion_limits_fail_one_byte_over_without_large_allocations(
    tmp_path: Path,
    entries: list[tuple[str, int]],
    expected_code: str,
) -> None:
    assert MAX_ARCHIVE_MEMBER_BYTES == 32 * 1024 * 1024
    assert MAX_ARCHIVE_EXPANDED_BYTES == 200 * 1024 * 1024
    content = _zip_bytes_with_declared_sizes(entries)
    assert len(content) < 4 * 1024

    with pytest.raises(ServiceError) as raised:
        _extract(
            _request(
                tmp_path,
                name="declared-expansion.docx",
                document_format="docx",
                content=content,
            )
        )

    assert raised.value.code == expected_code
    assert str(tmp_path) not in raised.value.message


def test_archive_path_depth_fails_at_twenty_one_components(
    tmp_path: Path,
) -> None:
    assert MAX_ARCHIVE_PATH_DEPTH == 20
    member_name = "/".join(
        [*[f"level-{index}" for index in range(MAX_ARCHIVE_PATH_DEPTH)], "document.xml"]
    )
    content = _zip_bytes(
        [(member_name, b"<document/>")],
        compression=zipfile.ZIP_STORED,
    )

    with pytest.raises(ServiceError) as raised:
        _extract(
            _request(
                tmp_path,
                name="deep.docx",
                document_format="docx",
                content=content,
            )
        )

    assert raised.value.code == "ARCHIVE_UNSAFE_PATH"


def test_archive_member_name_fails_one_code_point_over() -> None:
    assert MAX_ARCHIVE_MEMBER_NAME_CHARACTERS == 512
    accepted = f"{'a' * (MAX_ARCHIVE_MEMBER_NAME_CHARACTERS - 4)}.xml"
    assert document_ingest._safe_member_name(accepted) == accepted
    with pytest.raises(ServiceError) as raised:
        document_ingest._safe_member_name(f"{'a' * (MAX_ARCHIVE_MEMBER_NAME_CHARACTERS - 3)}.xml")
    assert raised.value.code == "ARCHIVE_UNSAFE_PATH"
    with pytest.raises(ServiceError) as control_character:
        document_ingest._safe_member_name("word/line\nbreak.xml")
    assert control_character.value.code == "ARCHIVE_UNSAFE_PATH"


class _DeclaredLengthText(str):
    def __new__(
        cls,
        value: str,
        declared_length: int,
    ) -> _DeclaredLengthText:
        instance = super().__new__(cls, value)
        instance.declared_length = declared_length
        return instance

    def __len__(self) -> int:
        return self.declared_length


def test_extracted_text_and_section_limits_fail_exactly_one_over() -> None:
    assert MAX_EXTRACTED_CHARACTERS == 10_000_000
    assert MAX_EXTRACTED_SECTIONS == 10_000
    location = document_ingest.SourceLocation(
        kind="text",
        start=0,
        end=1,
    )

    with pytest.raises(ServiceError) as text_error:
        document_ingest._join_blocks(
            [
                document_ingest._Block(
                    kind="paragraph",
                    title=None,
                    text=_DeclaredLengthText(
                        "x",
                        MAX_EXTRACTED_CHARACTERS + 1,
                    ),
                    location=location,
                )
            ]
        )
    assert text_error.value.code == "EXTRACTED_TEXT_LIMIT"

    shared_block = document_ingest._Block(
        kind="paragraph",
        title=None,
        text="x",
        location=location,
    )
    with pytest.raises(ServiceError) as section_error:
        document_ingest._join_blocks(shared_block for _index in range(MAX_EXTRACTED_SECTIONS + 1))
    assert section_error.value.code == "EXTRACTED_SECTION_LIMIT"


def test_markdown_heading_sections_fail_exactly_one_over(tmp_path: Path) -> None:
    content = "\n".join(
        f"# Synthetic {index}" for index in range(MAX_EXTRACTED_SECTIONS + 1)
    ).encode()

    with pytest.raises(ServiceError) as raised:
        _extract(
            _request(
                tmp_path,
                name="too-many-headings.md",
                document_format="markdown",
                content=content,
            )
        )

    assert raised.value.code == "EXTRACTED_SECTION_LIMIT"


def _epub_with_active_content(
    *,
    script: bool,
    remote: bool,
    title: str = "Synthetic",
) -> bytes:
    extra = ""
    if script:
        extra += "<p>Visible<script>never()</script> tail.</p>"
    if remote:
        extra += '<img src="https://example.invalid/never-fetch.png"/>'
    extra += "<ul><li><p>Nested once.</p></li></ul>"
    return _zip_bytes(
        [
            ("mimetype", b"application/epub+zip"),
            (
                "META-INF/container.xml",
                (
                    b"<container version='1.0' "
                    b"xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
                    b"<rootfiles><rootfile full-path='OEBPS/content.opf' "
                    b"media-type='application/oebps-package+xml'/>"
                    b"</rootfiles></container>"
                ),
            ),
            (
                "OEBPS/content.opf",
                (
                    "<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>"
                    f"<metadata><title>{title}</title></metadata>"
                    "<manifest><item id='c' href='chapter.xhtml' "
                    "media-type='application/xhtml+xml'/></manifest>"
                    "<spine><itemref idref='c'/></spine></package>"
                ).encode(),
            ),
            (
                "OEBPS/chapter.xhtml",
                (
                    "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                    f"<h1>Chapter One</h1><p>Safe text.</p>{extra}</body></html>"
                ).encode(),
            ),
        ],
        compression=zipfile.ZIP_STORED,
    )


@pytest.mark.parametrize(
    ("member", "replacement", "expected_code"),
    [
        (
            "META-INF/container.xml",
            (
                b"<container version='1.0'><rootfiles><rootfile "
                b"full-path='OEBPS/content.opf' "
                b"media-type='application/oebps-package+xml'/></rootfiles></container>"
            ),
            "EPUB_CONTAINER_INVALID",
        ),
        (
            "META-INF/container.xml",
            (
                b"<container version='1.0' "
                b"xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
                b"<rootfiles><rootfile full-path='OEBPS/content.opf'/>"
                b"</rootfiles></container>"
            ),
            "EPUB_CONTAINER_INVALID",
        ),
        (
            "OEBPS/content.opf",
            (
                b"<package version='3.0'><metadata/><manifest>"
                b"<item id='c' href='chapter.xhtml' media-type='application/xhtml+xml'/>"
                b"</manifest><spine><itemref idref='c'/></spine></package>"
            ),
            "EPUB_PACKAGE_INVALID",
        ),
        (
            "OEBPS/content.opf",
            (
                b"<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>"
                b"<metadata/><manifest>"
                b"<item id='c' href='chapter.xhtml' media-type='application/xhtml+xml'/>"
                b"<item id='c' href='other.xhtml' media-type='application/xhtml+xml'/>"
                b"</manifest><spine><itemref idref='c'/></spine></package>"
            ),
            "EPUB_PACKAGE_INVALID",
        ),
        (
            "OEBPS/chapter.xhtml",
            b"<html><body><p>Arbitrary XML is not XHTML.</p></body></html>",
            "EPUB_CONTENT_INVALID",
        ),
    ],
)
def test_epub_requires_standard_container_package_and_content_identities(
    tmp_path: Path,
    member: str,
    replacement: bytes,
    expected_code: str,
) -> None:
    content = _replace_zip_members(
        _epub_with_active_content(script=False, remote=False),
        {member: replacement},
    )

    with pytest.raises(ServiceError) as raised:
        _extract(
            _request(
                tmp_path,
                name="identity.epub",
                document_format="epub",
                content=content,
            )
        )

    assert raised.value.code == expected_code


def test_epub_never_fetches_or_executes_active_content(tmp_path: Path) -> None:
    content = _epub_with_active_content(script=True, remote=True)
    request = _request(
        tmp_path,
        name="active.epub",
        document_format="epub",
        content=content,
    )
    result = EpubDocumentAdapter().extract(
        request,
        cancelled=lambda: False,
        progress=lambda _stage, _value: None,
    )
    assert "never()" not in result.canonical_text
    assert "Visible tail." in result.canonical_text
    assert result.canonical_text.count("Nested once.") == 1
    assert {warning.code for warning in result.warnings} == {
        "EPUB_SCRIPT_OMITTED",
        "EPUB_EXTERNAL_REFERENCE_OMITTED",
    }


def test_epub_metadata_title_is_bounded_to_contract(tmp_path: Path) -> None:
    result = _extract(
        _request(
            tmp_path,
            name="long-title.epub",
            document_format="epub",
            content=_epub_with_active_content(
                script=False,
                remote=False,
                title="T" * 256,
            ),
        )
    )
    assert result.title == "T" * 255


def _pdf_bytes(*, encrypted: bool = False, pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("synthetic-password")
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


def test_pdf_rejects_encrypted_image_only_truncated_and_page_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encrypted = _pdf_bytes(encrypted=True)
    with pytest.raises(ServiceError) as encrypted_error:
        _extract(
            _request(
                tmp_path,
                name="encrypted.pdf",
                document_format="pdf",
                content=encrypted,
            )
        )
    assert encrypted_error.value.code == "PDF_ENCRYPTED"

    image_only = _pdf_bytes()
    with pytest.raises(ServiceError) as image_error:
        _extract(
            _request(
                tmp_path,
                name="image-only.pdf",
                document_format="pdf",
                content=image_only,
            )
        )
    assert image_error.value.code == "PDF_NO_EXTRACTABLE_TEXT"

    valid = _base64_fixture("pdf")
    with pytest.raises(ServiceError) as truncated_error:
        _extract(
            _request(
                tmp_path,
                name="truncated.pdf",
                document_format="pdf",
                content=valid[:-24],
            )
        )
    assert truncated_error.value.code == "PDF_MALFORMED"

    class _TooManyPages:
        is_encrypted = False
        pages = [object()] * 2_001

    monkeypatch.setattr(document_ingest, "PdfReader", lambda *_args, **_kwargs: _TooManyPages())
    with pytest.raises(ServiceError) as page_error:
        PdfDocumentAdapter().extract(
            _request(
                tmp_path,
                name="pages.pdf",
                document_format="pdf",
                content=valid,
            ),
            cancelled=lambda: False,
            progress=lambda _stage, _value: None,
        )
    assert page_error.value.code == "PDF_PAGE_LIMIT"


def test_parser_cancellation_timeout_and_source_integrity_fail_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = (_FIXTURES / "sample-story.txt").read_bytes()
    request = _request(
        tmp_path,
        name="sample-story.txt",
        document_format="txt",
        content=content,
    )
    with pytest.raises(ServiceError) as cancelled:
        _extract(request, cancelled=True)
    assert cancelled.value.code == "EXTRACTION_CANCELLED"

    with monkeypatch.context() as timeout_patch:
        ticks = iter([0.0, 0.0, 1.0])
        timeout_patch.setattr(
            document_ingest.time,
            "monotonic",
            lambda: next(ticks),
        )
        with pytest.raises(ServiceError) as timeout:
            _extract(
                _request(
                    tmp_path,
                    name="timeout.txt",
                    document_format="txt",
                    content=content,
                    deadline_seconds=0.1,
                )
            )
    assert timeout.value.code == "PARSER_TIMEOUT"

    with pytest.raises(ServiceError) as invalid_deadline:
        _extract(
            _request(
                tmp_path,
                name="deadline-over-limit.txt",
                document_format="txt",
                content=content,
                deadline_seconds=30.001,
            )
        )
    assert invalid_deadline.value.code == "PARSER_DEADLINE_INVALID"

    integrity_request = _request(
        tmp_path,
        name="integrity.txt",
        document_format="txt",
        content=content,
    )
    integrity_request.source_path.write_bytes(b"changed")
    with pytest.raises(ServiceError) as integrity:
        _extract(integrity_request)
    assert integrity.value.code == "SOURCE_INTEGRITY_FAILED"
