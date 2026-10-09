"""CLI entry point: python -m backend.app.retrieval.rebuild_opensearch."""

from backend.app.core.config import get_settings
from backend.app.db.session import get_session_factory
from backend.app.retrieval.lexical_indexing import rebuild_opensearch_index
from backend.app.retrieval.opensearch import get_opensearch_client


def main() -> None:
    settings = get_settings()
    count = rebuild_opensearch_index(
        session_factory=get_session_factory(),
        client=get_opensearch_client(),
        settings=settings,
    )
    print(f"Rebuilt OpenSearch index {settings.opensearch_index}: {count} chunks indexed")


if __name__ == "__main__":
    main()
