"""FastAPI dependencies shared by the API routers."""

import logging
from collections.abc import Callable, Iterator

import duckdb

from agent.llm import DeepSeekClient
from agent.nl2sql import FewShot
from agent.retrieval import default_embedder, few_shots_for
from analytics.risk import database_path

LOGGER = logging.getLogger(__name__)
FewShotsProvider = Callable[[str], tuple[FewShot, ...] | None]


def get_db() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield one request-scoped, read-only DuckDB connection."""
    connection = duckdb.connect(str(database_path()), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def get_llm() -> DeepSeekClient:
    """Build the injectable LLM client used by the chat route."""
    return DeepSeekClient()


def get_few_shots_provider() -> FewShotsProvider:
    """Build a read-only production retriever with a second fixed-shot fallback."""

    def provider(question: str) -> tuple[FewShot, ...] | None:
        try:
            connection = duckdb.connect(str(database_path()), read_only=True)
            try:
                return few_shots_for(connection, default_embedder(), question)
            finally:
                connection.close()
        except Exception as error:
            LOGGER.warning("RAG provider failed; Chat will use fixed examples: %s", error)
            return None

    return provider
