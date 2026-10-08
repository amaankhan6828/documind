from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import Document, DocumentVersion, Tenant, User


def test_domain_relationships_and_uuid_keys(db_session: Session) -> None:
    tenant = Tenant(name="Acme", slug="acme")
    user = User(email="reader@example.com", display_name="Reader", tenant=tenant)
    document = Document(title="Annual report", tenant=tenant)
    version = DocumentVersion(tenant=tenant, document=document, version_number=1)
    db_session.add_all([user, document, version])
    db_session.flush()

    assert isinstance(tenant.id, UUID)
    assert user in tenant.users
    assert document in tenant.documents
    assert version in document.versions
    assert version.tenant_id == document.tenant_id == tenant.id
    assert isinstance(tenant.created_at, datetime)
    assert Tenant.__table__.c.created_at.type.timezone is True
    assert DocumentVersion.__table__.c.updated_at.type.timezone is True


def test_slug_is_unique_and_nonempty(db_session: Session) -> None:
    db_session.add(Tenant(name="First", slug="shared"))
    db_session.flush()
    db_session.add(Tenant(name="Second", slug="shared"))

    with pytest.raises(IntegrityError):
        db_session.flush()

    db_session.rollback()
    db_session.add(Tenant(name="Blank slug", slug="  "))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_user_email_is_unique_per_tenant(db_session: Session) -> None:
    first_tenant = Tenant(name="First", slug="first")
    second_tenant = Tenant(name="Second", slug="second")
    db_session.add_all([first_tenant, second_tenant])
    db_session.flush()
    db_session.add_all(
        [
            User(email="member@example.com", display_name="One", tenant=first_tenant),
            User(email="member@example.com", display_name="Two", tenant=second_tenant),
        ]
    )
    db_session.flush()
    db_session.add(
        User(email="member@example.com", display_name="Duplicate", tenant=first_tenant)
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_document_version_requires_same_tenant_and_positive_unique_number(
    db_session: Session,
) -> None:
    owner = Tenant(name="Owner", slug="owner")
    other = Tenant(name="Other", slug="other")
    document = Document(title="Plan", tenant=owner)
    db_session.add_all([owner, other, document])
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                DocumentVersion(
                    tenant_id=other.id,
                    document_id=document.id,
                    version_number=1,
                )
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                DocumentVersion(tenant=owner, document=document, version_number=0)
            )
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add_all(
                [
                    DocumentVersion(tenant=owner, document=document, version_number=1),
                    DocumentVersion(tenant=owner, document=document, version_number=1),
                ]
            )
            db_session.flush()
