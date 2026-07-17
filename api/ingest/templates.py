"""Template preview, deterministic mapping, and single-template persistence."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Protocol

import duckdb

from ingest.database import (
    TARGET_COLUMNS,
    TARGET_TABLE,
    ensure_ingest_tables,
    load_template,
)
from ingest.errors import IngestError
from ingest.workbook import read_first_sheet


class MappingSuggester(Protocol):
    """Optional configuration-path mapper; daily row processing never receives one."""

    def suggest(
        self, sample_columns: list[str], target_columns: list[str]
    ) -> dict[str, str | None]: ...


ALIASES = {
    "po_id": ("采购单号", "采购订单号", "po号", "订单号"),
    "material_pn": ("物料号", "料号", "物料编码", "物料料号"),
    "supplier_id": ("供应商", "供应商编号", "供应商编码"),
    "qty": ("数量", "订单数量", "采购数量"),
    "eta_date": ("到货日", "预计到货", "预计到货日", "eta"),
}


def _normalize(column: str) -> str:
    return re.sub(r"[\s_]", "", column).casefold()


def read_sample(file_bytes: bytes) -> list[str]:
    columns, _ = read_first_sheet(file_bytes)
    return columns


def deterministic_mapping(sample_columns: list[str]) -> dict[str, str | None]:
    normalized_sources = {_normalize(column): column for column in sample_columns}
    result: dict[str, str | None] = {}
    used: set[str] = set()
    for target in TARGET_COLUMNS:
        candidates = (target, *ALIASES[target])
        match = next(
            (
                normalized_sources[_normalize(candidate)]
                for candidate in candidates
                if _normalize(candidate) in normalized_sources
                and normalized_sources[_normalize(candidate)] not in used
            ),
            None,
        )
        result[target] = match
        if match is not None:
            used.add(match)
    return result


def suggest_mapping(
    sample_columns: list[str], suggester: MappingSuggester | None
) -> dict[str, str | None]:
    result = deterministic_mapping(sample_columns)
    remaining = [target for target, source in result.items() if source is None]
    if not remaining or suggester is None:
        return result

    suggested = suggester.suggest(sample_columns, remaining)
    used = {source for source in result.values() if source is not None}
    for target in remaining:
        source = suggested.get(target)
        if source in sample_columns and source not in used:
            result[target] = source
            used.add(source)
        else:
            result[target] = None
    return result


def validate_mapping(mapping: dict[str, str]) -> None:
    if set(mapping) != set(TARGET_COLUMNS):
        raise IngestError("invalid_mapping", "列映射必须完整覆盖 open_po 的 5 个目标列")
    sources = list(mapping.values())
    if any(not isinstance(source, str) or not source.strip() for source in sources):
        raise IngestError("invalid_mapping", "每个目标列都必须选择非空源列")
    if len(set(sources)) != len(sources):
        raise IngestError("invalid_mapping", "同一个源列不能映射到多个目标列")


def save_template(connection: duckdb.DuckDBPyConnection, mapping: dict[str, str]) -> None:
    validate_mapping(mapping)
    ensure_ingest_tables(connection)
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("DELETE FROM ingest_template WHERE target_table = ?", [TARGET_TABLE])
        connection.execute(
            "INSERT INTO ingest_template VALUES (?, ?, ?)",
            [
                TARGET_TABLE,
                json.dumps(mapping, ensure_ascii=False, sort_keys=True),
                datetime.now(timezone.utc).replace(tzinfo=None),
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def get_template(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, str], datetime] | None:
    return load_template(connection)
