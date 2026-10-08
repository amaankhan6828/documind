"""Docling-specific conversion isolated behind a stable DocuMind schema."""

from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    NativePdfPipelineOptions,
    PdfPipelineOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import (
    ConversionStatus,
    DocumentConverter,
    NativePdfFormatOption,
    PdfFormatOption,
)

from backend.app.core.config import get_settings
from backend.app.document_intelligence.schemas import (
    BoundingBoxData,
    DocumentElementData,
    DocumentElementType,
    DocumentMetadata,
    DocumentPageData,
    DocumentSectionData,
    DocumentStructure,
    ProvenanceLocation,
)


class DocumentIngestionError(Exception):
    """Raised when an uploaded file cannot be converted safely."""


class DoclingAdapter:
    """Convert only locally stored PDF/DOCX files into internal structures."""

    def __init__(
        self,
        converter: Any | None = None,
        ocr_converter: Any | None = None,
    ) -> None:
        self._converter = converter or DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
            format_options={
                InputFormat.PDF: NativePdfFormatOption(
                    pipeline_options=NativePdfPipelineOptions()
                )
            },
        )
        self._ocr_converter = ocr_converter

    def convert(
        self,
        source_path: Path,
        source_filename: str,
        source_sha256: str,
    ) -> DocumentStructure:
        """Run Docling and discard provider-specific objects at this boundary."""
        suffix = source_path.suffix.lower()
        if suffix not in {".pdf", ".docx"}:
            raise DocumentIngestionError("Unsupported document type")

        try:
            max_file_size = get_settings().max_upload_size_mb * 1024 * 1024
            result = self._converter.convert(
                source_path,
                max_file_size=max_file_size,
            )
            if (
                suffix == ".pdf"
                and result.status is ConversionStatus.SUCCESS
                and result.document is not None
                and not self._contains_text(result.document)
            ):
                result = self._convert_with_ocr(source_path, max_file_size)
            if result.status is not ConversionStatus.SUCCESS or result.document is None:
                raise DocumentIngestionError("Document conversion was not successful")
            return self._to_internal(
                result.document,
                source_filename=source_filename,
                source_sha256=source_sha256,
            )
        except DocumentIngestionError:
            raise
        except Exception as exc:
            # Do not expose parser messages, which may contain hostile input or paths.
            raise DocumentIngestionError("Document conversion failed") from exc

    def _convert_with_ocr(self, source_path: Path, max_file_size: int) -> Any:
        """Use Docling's Tesseract-backed pipeline only when PDF text is absent."""
        if self._ocr_converter is None:
            self._ocr_converter = self._build_ocr_converter()
        return self._ocr_converter.convert(
            source_path,
            max_file_size=max_file_size,
        )

    @staticmethod
    def _build_ocr_converter() -> DocumentConverter:
        """Build Docling's PDF OCR pipeline with the local Tesseract CLI engine."""
        pipeline_options = PdfPipelineOptions(
            ocr_options=TesseractCliOcrOptions(lang=["eng"]),
            do_ocr=True,
            do_table_structure=False,
        )
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            },
        )

    @staticmethod
    def _contains_text(document: Any) -> bool:
        """Check whether native PDF parsing found selectable text."""
        for item, _depth in document.iterate_items(traverse_pictures=False):
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                return True
        return False

    @classmethod
    def _to_internal(
        cls,
        document: Any,
        *,
        source_filename: str,
        source_sha256: str,
    ) -> DocumentStructure:
        page_data: dict[int, DocumentPageData] = {}
        for key, page in getattr(document, "pages", {}).items():
            page_number = getattr(page, "page_no", key)
            try:
                number = int(page_number)
            except (TypeError, ValueError):
                continue
            if number <= 0:
                continue
            size = getattr(page, "size", None)
            page_data[number] = DocumentPageData(
                page_number=number,
                width=getattr(size, "width", None),
                height=getattr(size, "height", None),
            )

        sections: list[DocumentSectionData] = []
        elements: list[DocumentElementData] = []
        section_stack: list[tuple[int, str]] = []
        section_refs: set[str] = set()

        for item, _depth in document.iterate_items(traverse_pictures=True):
            label = getattr(getattr(item, "label", None), "value", None)
            source_ref = str(getattr(item, "self_ref", ""))
            if not source_ref:
                continue
            provenance = cls._provenance(item)
            for location in provenance:
                if location.page_number is not None:
                    page_data.setdefault(
                        location.page_number,
                        DocumentPageData(page_number=location.page_number),
                    )

            current_section_ref = section_stack[-1][1] if section_stack else None
            if label == "section_header":
                level = max(1, int(getattr(item, "level", 1)))
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                parent_ref = section_stack[-1][1] if section_stack else None
                section = DocumentSectionData(
                    ordinal=len(sections),
                    source_ref=source_ref,
                    title=str(getattr(item, "text", "")),
                    level=level,
                    parent_source_ref=parent_ref,
                    provenance=provenance,
                )
                sections.append(section)
                section_refs.add(source_ref)
                section_stack.append((level, source_ref))
                current_section_ref = source_ref
                elements.append(
                    DocumentElementData(
                        ordinal=len(elements),
                        source_ref=source_ref,
                        element_type=DocumentElementType.TEXT,
                        text_content=str(getattr(item, "text", "")),
                        section_source_ref=source_ref,
                        provenance=provenance,
                    )
                )
                continue

            if label == "table":
                data = getattr(item, "data", None)
                structured = (
                    data.model_dump(mode="json")
                    if data is not None and hasattr(data, "model_dump")
                    else None
                )
                element_type = DocumentElementType.TABLE
                text_content = None
            elif label == "picture":
                # Avoid persisting embedded image bytes; keep parser references only.
                structured = {
                    "captions": [str(ref.cref) for ref in getattr(item, "captions", [])],
                    "references": [
                        str(ref.cref) for ref in getattr(item, "references", [])
                    ],
                }
                element_type = DocumentElementType.FIGURE
                text_content = None
            elif hasattr(item, "text"):
                structured = None
                element_type = DocumentElementType.TEXT
                text_content = str(item.text)
            else:
                continue

            section_ref = current_section_ref if current_section_ref in section_refs else None
            elements.append(
                DocumentElementData(
                    ordinal=len(elements),
                    source_ref=source_ref,
                    element_type=element_type,
                    text_content=text_content,
                    structured_content=structured,
                    section_source_ref=section_ref,
                    provenance=provenance,
                )
            )

        pages = sorted(page_data.values(), key=lambda page: page.page_number)
        return DocumentStructure(
            metadata=DocumentMetadata(
                source_filename=source_filename,
                source_sha256=source_sha256,
                parser_version=package_version("docling"),
                document_name=getattr(document, "name", None),
                page_count=len(pages),
            ),
            pages=pages,
            sections=sections,
            elements=elements,
        )

    @staticmethod
    def _provenance(item: Any) -> list[ProvenanceLocation]:
        locations: list[ProvenanceLocation] = []
        for provenance in getattr(item, "prov", []) or []:
            bbox = getattr(provenance, "bbox", None)
            locations.append(
                ProvenanceLocation(
                    page_number=getattr(provenance, "page_no", None),
                    bounding_box=(
                        BoundingBoxData.model_validate(bbox.model_dump(mode="json"))
                        if bbox is not None and hasattr(bbox, "model_dump")
                        else None
                    ),
                    character_span=getattr(provenance, "charspan", None),
                )
            )
        return locations
