"""CLI entry point: python -m backend.app.retrieval.rebuild."""

from backend.app.core.config import get_settings
from backend.app.db.session import get_session_factory
from backend.app.retrieval.embeddings import get_embedding_provider
from backend.app.retrieval.indexing import rebuild_index
from backend.app.retrieval.qdrant import get_qdrant_client


def main() -> None:
    settings = get_settings()
    count = rebuild_index(
        session_factory=get_session_factory(),
        client=get_qdrant_client(),
        embedder=get_embedding_provider(),
        settings=settings,
    )
    print(f"Rebuilt Qdrant collection {settings.qdrant_collection}: {count} chunks indexed")


if __name__ == "__main__":
    main()
