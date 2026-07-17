"""DuckDB tables and read helpers owned by the ingestion feature."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import duckdb

TARGET_TABLE = "open_po"
TARGET_COLUMNS = ("po_id", "material_pn", "supplier_id", "qty", "eta_date")


def table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = ?",
        [table_name],
    ).fetchone()
    return row is not None


def ensure_ingest_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Create only ingestion-owned tables; the existing open_po schema is untouched."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS ingest_template ("
        "target_table VARCHAR PRIMARY KEY, column_mapping_json VARCHAR NOT NULL, "
        "created_at TIMESTAMP NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS ingest_batch ("
        "batch_id VARCHAR PRIMARY KEY, filename VARCHAR NOT NULL, "
        "row_count INTEGER NOT NULL, created_at TIMESTAMP NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS ingest_batch_row ("
        "batch_id VARCHAR NOT NULL, po_id VARCHAR NOT NULL, "
        "PRIMARY KEY (batch_id, po_id))"
    )


def load_template(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, str], datetime] | None:
    if not table_exists(connection, "ingest_template"):
        return None
    row = connection.execute(
        "SELECT column_mapping_json, created_at FROM ingest_template WHERE target_table = ?",
        [TARGET_TABLE],
    ).fetchone()
    if row is None:
        return None
    raw_mapping: Any = json.loads(str(row[0]))
    return ({str(key): str(value) for key, value in raw_mapping.items()}, row[1])
