"""FastAPI dependencies shared by the API routers."""

from collections.abc import Iterator

import duckdb

from agent.llm import DeepSeekClient
from analytics.risk import database_path


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
