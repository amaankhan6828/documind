"""Authenticated tenant-scoped search endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.auth.dependencies import get_current_user
from backend.app.models import User
from backend.app.retrieval.hybrid import search_hybrid
from backend.app.retrieval.opensearch import OpenSearchIndexError
from backend.app.retrieval.qdrant import QdrantIndexError
from backend.app.schemas.hybrid_search import (
    HybridSearchRequest,
    HybridSearchResponse,
)

router = APIRouter(prefix="/api/v1/search", tags=["search"])
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/hybrid", response_model=HybridSearchResponse)
def hybrid_search(
    request: HybridSearchRequest,
    current_user: CurrentUser,
) -> HybridSearchResponse:
    """Search the authenticated tenant's chunks using dense and lexical retrieval."""
    try:
        results = search_hybrid(
            current_user.tenant_id,
            request.query,
            dense_candidate_limit=request.dense_candidate_limit,
            lexical_candidate_limit=request.lexical_candidate_limit,
            top_k=request.top_k,
            rrf_k=request.rrf_k,
        )
    except (QdrantIndexError, OpenSearchIndexError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hybrid retrieval service is unavailable",
        ) from exc
    return HybridSearchResponse(results=results)
