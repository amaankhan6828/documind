"""Persist a complete normalized document structure in one DB transaction."""

from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.app.document_intelligence.schemas import DocumentStructure
from backend.app.models import DocumentElement, DocumentPage, DocumentSection
from backend.app.models.document_version import DocumentVersion


def replace_document_structure(
    session: Session,
    version: DocumentVersion,
    structure: DocumentStructure,
) -> None:
    """Replace prior derived rows atomically, making retries idempotent."""
    version_id = version.id
    session.execute(
        delete(DocumentElement).where(DocumentElement.document_version_id == version_id)
    )
    session.execute(
        delete(DocumentSection).where(DocumentSection.document_version_id == version_id)
    )
    session.execute(
        delete(DocumentPage).where(DocumentPage.document_version_id == version_id)
    )

    page_ids = {page.page_number: uuid4() for page in structure.pages}
    section_ids = {section.source_ref: uuid4() for section in structure.sections}

    session.add_all(
        DocumentPage(
            id=page_ids[page.page_number],
            tenant_id=version.tenant_id,
            document_id=version.document_id,
            document_version_id=version_id,
            page_number=page.page_number,
            width=page.width,
            height=page.height,
        )
        for page in structure.pages
    )
    session.flush()

    session.add_all(
        DocumentSection(
            id=section_ids[section.source_ref],
            tenant_id=version.tenant_id,
            document_id=version.document_id,
            document_version_id=version_id,
            page_id=_page_id(section.provenance, page_ids),
            parent_section_id=section_ids.get(section.parent_source_ref),
            ordinal=section.ordinal,
            source_ref=section.source_ref,
            title=section.title,
            level=section.level,
            source_provenance=_json_locations(section.provenance),
        )
        for section in structure.sections
    )
    session.flush()

    session.add_all(
        DocumentElement(
            tenant_id=version.tenant_id,
            document_id=version.document_id,
            document_version_id=version_id,
            page_id=_page_id(element.provenance, page_ids),
            section_id=section_ids.get(element.section_source_ref),
            ordinal=element.ordinal,
            source_ref=element.source_ref,
            element_type=element.element_type.value,
            text_content=element.text_content,
            structured_content=element.structured_content,
            source_provenance=_json_locations(element.provenance),
        )
        for element in structure.elements
    )
    version.structure_metadata = structure.metadata.model_dump(mode="json")
    session.flush()


def _page_id(provenance, page_ids):
    for location in provenance:
        if location.page_number in page_ids:
            return page_ids[location.page_number]
    return None


def _json_locations(provenance) -> list[dict]:
    return [location.model_dump(mode="json", exclude_none=True) for location in provenance]
