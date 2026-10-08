import hashlib
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import ConversionStatus
from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    TableCell,
    TableData,
)
from docling_core.types.doc.base import BoundingBox, Size
from docx import Document as WordDocument
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.document_intelligence.docling_adapter import (
    DoclingAdapter,
    DocumentIngestionError,
)
from docling.datamodel.pipeline_options import (
    NativePdfPipelineOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import NativePdfFormatOption
from backend.app.document_intelligence.persistence import replace_document_structure
from backend.app.document_intelligence.schemas import (
    DocumentElementData,
    DocumentElementType,
    DocumentMetadata,
    DocumentPageData,
    DocumentSectionData,
    DocumentStructure,
    ProvenanceLocation,
)
from backend.app.models import (
    Document,
    DocumentElement,
    DocumentPage,
    DocumentSection,
    DocumentVersion,
    Tenant,
)


@pytest.fixture
def docling_document() -> DoclingDocument:
    document = DoclingDocument(name="deterministic-fixture")
    document.add_page(1, Size(width=612, height=792))
    first_page = ProvenanceItem(
        page_no=1,
        bbox=BoundingBox(l=72, t=72, r=540, b=100),
        charspan=(0, 8),
    )
    second_page = ProvenanceItem(
        page_no=2,
        bbox=BoundingBox(l=72, t=110, r=540, b=160),
        charspan=(8, 20),
    )
    document.add_page(2, Size(width=612, height=792))
    document.add_text(
        label=DocItemLabel.SECTION_HEADER,
        text="Overview",
        prov=first_page,
        level=1,
    )
    document.add_text(
        label=DocItemLabel.PARAGRAPH,
        text="Deterministic body text",
        prov=first_page,
    )
    document.add_text(
        label=DocItemLabel.SECTION_HEADER,
        text="Details",
        prov=second_page,
        level=2,
    )
    document.add_table(
        data=TableData(
            num_rows=1,
            num_cols=1,
            table_cells=[
                TableCell(
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                    text="Revenue",
                )
            ],
        ),
        prov=second_page,
    )
    document.add_picture(prov=second_page)
    return document


class FixedConverter:
    def __init__(self, document: DoclingDocument, status=ConversionStatus.SUCCESS):
        self.document = document
        self.status = status
        self.path: Path | None = None
        self.max_file_size: int | None = None

    def convert(self, path: Path, *, max_file_size: int):
        self.path = path
        self.max_file_size = max_file_size
        return SimpleNamespace(status=self.status, document=self.document)


class EmptyPdfConverter(FixedConverter):
    def __init__(self):
        super().__init__(DoclingDocument(name="scanned-fixture"))


def make_selectable_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    ]
    stream = b"BT /F1 18 Tf 72 720 Td (DocuMind native text fixture) Tj ET"
    objects.append(
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(pdf)


def make_image_only_pdf() -> bytes:
    stream = BytesIO()
    make_scanned_image().save(stream, format="PDF", resolution=150)
    return stream.getvalue()


def make_scanned_image() -> Image.Image:
    image = Image.new("RGB", (1600, 500), "white")
    ImageDraw.Draw(image).text(
        (100, 150),
        "SCANNED DOCUMIND FIXTURE",
        fill="black",
        font=ImageFont.load_default(size=72),
    )
    return image


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
def test_docling_adapter_maps_supported_formats_and_provenance(
    tmp_path: Path,
    docling_document: DoclingDocument,
    suffix: str,
) -> None:
    source = tmp_path / f"fixture{suffix}"
    source.write_bytes(b"deterministic parser input fixture")
    converter = FixedConverter(docling_document)

    result = DoclingAdapter(converter).convert(source, source.name, "a" * 64)

    assert converter.path == source
    assert converter.max_file_size and converter.max_file_size > source.stat().st_size
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.metadata.source_filename == source.name
    assert result.metadata.source_sha256 == "a" * 64
    assert result.metadata.page_count == 2
    assert [section.title for section in result.sections] == ["Overview", "Details"]
    assert result.sections[1].parent_source_ref == result.sections[0].source_ref
    assert result.elements[1].text_content == "Deterministic body text"
    assert result.elements[1].provenance[0].page_number == 1
    assert result.elements[1].provenance[0].bounding_box is not None
    assert result.elements[1].provenance[0].character_span == (0, 8)
    assert any(item.element_type is DocumentElementType.TABLE for item in result.elements)
    assert any(item.element_type is DocumentElementType.FIGURE for item in result.elements)


def test_docling_adapter_rejects_unsupported_and_unsuccessful_documents(
    tmp_path: Path,
    docling_document: DoclingDocument,
) -> None:
    unsupported = tmp_path / "fixture.txt"
    unsupported.write_text("not a supported document")
    with pytest.raises(DocumentIngestionError):
        DoclingAdapter(FixedConverter(docling_document)).convert(
            unsupported, unsupported.name, "a" * 64
        )

    source = tmp_path / "invalid.pdf"
    source.write_bytes(b"%PDF-1.4\nmalformed fixture")
    failed = FixedConverter(docling_document, status=ConversionStatus.FAILURE)
    with pytest.raises(DocumentIngestionError, match="not successful"):
        DoclingAdapter(failed).convert(source, source.name, "a" * 64)


def test_docling_converts_small_docx_fixture_without_inventing_pages(
    tmp_path: Path,
) -> None:
    source = tmp_path / "small-fixture.docx"
    word_document = WordDocument()
    word_document.add_heading("Overview", level=1)
    word_document.add_paragraph("Stable DOCX fixture text.")
    word_document.save(source)

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = DoclingAdapter().convert(source, source.name, digest)

    assert [section.title for section in result.sections] == ["Overview"]
    assert any(
        item.text_content == "Stable DOCX fixture text."
        and item.provenance == []
        for item in result.elements
    )
    assert result.pages == []
    assert result.metadata.page_count == 0


def test_default_pdf_converter_uses_model_free_native_pipeline() -> None:
    adapter = DoclingAdapter()
    pdf_option = adapter._converter.format_to_options[InputFormat.PDF]

    assert isinstance(pdf_option, NativePdfFormatOption)
    assert isinstance(pdf_option.pipeline_options, NativePdfPipelineOptions)


def test_docling_converts_selectable_pdf_with_page_provenance_offline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "selectable-text.pdf"
    content = make_selectable_pdf()
    source.write_bytes(content)

    result = DoclingAdapter().convert(
        source,
        source.name,
        hashlib.sha256(content).hexdigest(),
    )

    assert [page.page_number for page in result.pages] == [1]
    text = next(item for item in result.elements if item.text_content)
    assert text.text_content == "DocuMind native text fixture"
    assert text.provenance[0].page_number == 1
    assert text.provenance[0].bounding_box is not None


def test_scanned_pdf_routes_to_tesseract_ocr_converter(tmp_path: Path) -> None:
    source = tmp_path / "scanned.pdf"
    source.write_bytes(make_image_only_pdf())
    output = DoclingDocument(name="ocr-fixture")
    output.add_page(1, Size(width=612, height=792))
    output.add_text(
        label=DocItemLabel.PARAGRAPH,
        text="OCR fixture text",
        prov=ProvenanceItem(
            page_no=1,
            bbox=BoundingBox(l=72, t=72, r=540, b=100),
            charspan=(0, 16),
        ),
    )
    native = EmptyPdfConverter()
    ocr = FixedConverter(output)

    result = DoclingAdapter(native, ocr).convert(source, source.name, "a" * 64)

    assert native.path == source
    assert ocr.path == source
    assert result.elements


def test_ocr_pipeline_is_configured_for_local_tesseract() -> None:
    converter = DoclingAdapter._build_ocr_converter()
    pdf_option = converter.format_to_options[InputFormat.PDF]
    options = pdf_option.pipeline_options

    assert options.do_ocr is True
    assert isinstance(options.ocr_options, TesseractCliOcrOptions)
    assert options.ocr_options.tesseract_cmd == "tesseract"
    assert options.ocr_options.lang == ["eng"]
    assert options.do_table_structure is False


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_tesseract_reads_local_image_only_pdf(tmp_path: Path) -> None:
    source = tmp_path / "scanned.png"
    make_scanned_image().save(source)

    result = subprocess.run(
        ["tesseract", str(source), "stdout"],
        capture_output=True,
        check=True,
        text=True,
        timeout=20,
    )

    assert "DOCUMIND" in result.stdout.upper()


def test_malformed_pdf_fails_safely_without_ocr_fallback(tmp_path: Path) -> None:
    source = tmp_path / "malformed.pdf"
    source.write_bytes(b"%PDF-1.4\nmalformed and truncated")

    with pytest.raises(DocumentIngestionError):
        DoclingAdapter().convert(source, source.name, "a" * 64)


def make_structure(source_ref: str = "#/texts/0") -> DocumentStructure:
    provenance = [
        ProvenanceLocation(
            page_number=1,
            bounding_box={"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "TOPLEFT"},
            character_span=(0, 6),
        )
    ]
    return DocumentStructure(
        metadata=DocumentMetadata(
            source_filename="fixture.pdf",
            source_sha256="b" * 64,
            parser_version="test",
            document_name="fixture",
            page_count=1,
        ),
        pages=[DocumentPageData(page_number=1, width=612, height=792)],
        sections=[
            DocumentSectionData(
                ordinal=0,
                source_ref=source_ref,
                title="Overview",
                level=1,
                provenance=provenance,
            )
        ],
        elements=[
            DocumentElementData(
                ordinal=0,
                source_ref=source_ref,
                element_type=DocumentElementType.TEXT,
                text_content="Body text",
                section_source_ref=source_ref,
                provenance=provenance,
            )
        ],
    )


def create_document_version(
    session: Session,
    suffix: str,
) -> DocumentVersion:
    tenant = Tenant(name=f"Tenant {suffix}", slug=f"docling-tenant-{suffix}")
    session.add(tenant)
    session.flush()
    document = Document(tenant_id=tenant.id, title=f"Document {suffix}")
    session.add(document)
    session.flush()
    version = DocumentVersion(
        tenant_id=tenant.id,
        document_id=document.id,
        version_number=1,
        original_filename="fixture.pdf",
        sha256="b" * 64,
    )
    session.add(version)
    session.flush()
    return version


def test_structure_persistence_is_idempotent_and_links_provenance(
    db_session: Session,
) -> None:
    version = create_document_version(db_session, "a")
    structure = make_structure()

    replace_document_structure(db_session, version, structure)
    db_session.commit()
    replace_document_structure(db_session, version, structure)
    db_session.commit()

    page = db_session.scalar(
        select(DocumentPage).where(DocumentPage.document_version_id == version.id)
    )
    section = db_session.scalar(
        select(DocumentSection).where(DocumentSection.document_version_id == version.id)
    )
    element = db_session.scalar(
        select(DocumentElement).where(DocumentElement.document_version_id == version.id)
    )
    assert page is not None and page.page_number == 1
    assert section is not None and section.page_id == page.id
    assert element is not None and element.page_id == page.id
    assert element.section_id == section.id
    assert element.source_provenance[0]["page_number"] == 1
    assert element.source_provenance[0]["character_span"] == [0, 6]
    assert version.structure_metadata["source_sha256"] == "b" * 64
    assert (
        db_session.scalar(
            select(func.count()).select_from(DocumentElement).where(
                DocumentElement.document_version_id == version.id
            )
        )
        == 1
    )


def test_structure_foreign_keys_prevent_cross_tenant_page_links(
    db_session: Session,
) -> None:
    version_a = create_document_version(db_session, "a")
    version_b = create_document_version(db_session, "b")
    replace_document_structure(db_session, version_a, make_structure())
    db_session.flush()
    page_a = db_session.scalar(
        select(DocumentPage).where(DocumentPage.document_version_id == version_a.id)
    )
    assert page_a is not None

    db_session.add(
        DocumentElement(
            tenant_id=version_b.tenant_id,
            document_id=version_b.document_id,
            document_version_id=version_b.id,
            page_id=page_a.id,
            ordinal=0,
            source_ref="#/texts/99",
            element_type="TEXT",
            text_content="Invalid cross-tenant reference",
            source_provenance=[],
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
