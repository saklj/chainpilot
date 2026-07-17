"""Tests for deterministic, format-only Excel repair."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import date
from io import BytesIO
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.deps import get_db, get_read_write_db
from ingest.pipeline import rollback_batch, validate_file
from ingest.repair import REPAIR_RULES, repair_file
from ingest.templates import read_sample, save_template, suggest_mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"
FIXTURES = REPO_ROOT / "data" / "fixtures" / "ingest"
CANONICAL_MAPPING = {
    "po_id": "po_id",
    "material_pn": "material_pn",
    "supplier_id": "supplier_id",
    "qty": "qty",
    "eta_date": "eta_date",
}


@pytest.fixture
def db_copy(tmp_path: Path) -> Path:
    if not REAL_DB.is_file():
        pytest.skip("real DuckDB fixture is unavailable")
    destination = tmp_path / "chainpilot.duckdb"
    shutil.copyfile(REAL_DB, destination)
    # The real DB evolves as the user registers templates / imports / polls mail;
    # normalize the copy to "no ingest state" so assertions stay deterministic.
    with duckdb.connect(str(destination)) as connection:
        for table in ("ingest_mail_item", "ingest_batch_row", "ingest_batch", "ingest_template"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
    return destination


def workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def register_fixture_template(connection: duckdb.DuckDBPyConnection) -> None:
    sample = (FIXTURES / "样例_历史整理版.xlsx").read_bytes()
    save_template(connection, suggest_mapping(read_sample(sample), None))


def rule(name: str):
    return next(item for item in REPAIR_RULES if item.name == name)


def test_v1_date_and_qty_rules_cover_only_fixed_formats() -> None:
    date_rule = rule("date_format")
    assert date_rule.apply("2016/6/8") == "2016-06-08"
    assert date_rule.apply("2016.7.15") == "2016-07-15"
    assert date_rule.apply("2016年7月1日") == "2016-07-01"
    assert date_rule.apply("20160725") == "2016-07-25"
    assert date_rule.apply("2016/2/30") is None
    assert date_rule.apply("下周三") is None

    qty_rule = rule("qty_format")
    assert qty_rule.apply("1,200") == 1200
    assert qty_rule.apply("６００") == 600
    assert qty_rule.apply("800.0") == 800
    assert qty_rule.apply(" 900 ") == 900
    assert qty_rule.apply("-50") is None


def test_key_normalization_is_kept_only_for_known_keys(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        content = workbook_bytes(
            list(CANONICAL_MAPPING),
            [
                ["PO-REPAIR-KEY-1", "pn-00003", "sup-004", 10, "2026-08-01"],
                ["PO-REPAIR-KEY-2", "pn-99999", "SUP-004", 10, "2026-08-02"],
            ],
        )
        outcome = repair_file(connection, content, "keys.xlsx")
        assert [(item.row, item.field, item.new_value) for item in outcome.repairs] == [
            (2, "material_pn", "PN-00003"),
            (2, "supplier_id", "SUP-004"),
        ]
        assert outcome.report.valid_count == 1
        assert [(error.row, error.field, error.code) for error in outcome.report.errors] == [
            (3, "material_pn", "unknown_material")
        ]
    finally:
        connection.close()


def test_valid_rows_are_never_modified(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        content = workbook_bytes(
            list(CANONICAL_MAPPING),
            [
                ["PO-REPAIR-VALID", "PN-00001", "SUP-001", 321, date(2026, 8, 1)],
                ["PO-REPAIR-DIRTY", "PN-00002", "SUP-002", "1,200", "2016/6/8"],
            ],
        )
        before = validate_file(connection, content, filename="mixed.xlsx")
        outcome = repair_file(connection, content, "mixed.xlsx")
        assert before.valid_rows[0] == outcome.report.valid_rows[0]
        assert all(item.row != 2 for item in outcome.repairs)
        assert outcome.report.valid_rows[0].qty == 321
        assert outcome.report.valid_rows[0].eta_date == date(2026, 8, 1)
    finally:
        connection.close()


def test_repairable_fixture_yields_exact_diffs_and_three_fact_errors(
    db_copy: Path,
) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        register_fixture_template(connection)
        content = (FIXTURES / "导入_可修复.xlsx").read_bytes()
        outcome = repair_file(connection, content, "导入_可修复.xlsx")
        assert outcome.report.valid_count == 5
        assert outcome.report.error_count == 3
        assert [(item.row, item.field, item.rule_name) for item in outcome.repairs] == [
            (2, "qty", "qty_format"),
            (2, "eta_date", "date_format"),
            (3, "eta_date", "date_format"),
            (4, "material_pn", "key_normalize"),
            (4, "qty", "qty_format"),
            (4, "eta_date", "date_format"),
            (5, "supplier_id", "key_normalize"),
            (6, "eta_date", "date_format"),
        ]
        assert [(error.row, error.field, error.code) for error in outcome.report.errors] == [
            (7, "material_pn", "unknown_material"),
            (8, "qty", "invalid_positive_integer"),
            (9, "eta_date", "invalid_date"),
        ]
        assert [row.po_id for row in outcome.report.valid_rows] == [
            "PO-920001",
            "PO-920002",
            "PO-920003",
            "PO-920004",
            "PO-920005",
        ]
    finally:
        connection.close()


def test_full_revalidation_catches_po_id_collision_created_by_repair(
    db_copy: Path,
) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        connection.execute(
            "INSERT INTO open_po VALUES (?, ?, ?, ?, ?)",
            ["po-repair-collision", "PN-00001", "SUP-001", 1, date(2026, 7, 1)],
        )
        content = workbook_bytes(
            list(CANONICAL_MAPPING),
            [
                [" po-repair-collision ", "PN-00001", "SUP-001", 10, "2026-08-01"],
                ["PO-REPAIR-COLLISION", "PN-00002", "SUP-002", 11, "2026-08-02"],
            ],
        )
        outcome = repair_file(connection, content, "collision.xlsx")
        assert [(item.row, item.field, item.new_value) for item in outcome.repairs] == [
            (2, "po_id", "PO-REPAIR-COLLISION")
        ]
        assert [(error.row, error.field, error.code) for error in outcome.report.errors] == [
            (3, "po_id", "duplicate_in_file")
        ]
    finally:
        connection.close()


def test_repair_is_byte_stable_when_serialized(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        register_fixture_template(connection)
        content = (FIXTURES / "导入_可修复.xlsx").read_bytes()
        first = repair_file(connection, content, "导入_可修复.xlsx")
        second = repair_file(connection, content, "导入_可修复.xlsx")
        first_json = json.dumps(asdict(first), ensure_ascii=False, sort_keys=True, default=str)
        second_json = json.dumps(asdict(second), ensure_ascii=False, sort_keys=True, default=str)
        assert first_json.encode() == second_json.encode()
    finally:
        connection.close()


def _connection_dependency(db_path: Path):
    def dependency():
        connection = duckdb.connect(str(db_path))
        try:
            yield connection
        finally:
            connection.close()

    return dependency


def test_repair_route_confirm_imports_repaired_values_and_no_template_is_404(
    db_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INGEST_MAIL_POLL_SECONDS", "0")
    from app.main import app

    connection = duckdb.connect(str(db_copy))
    try:
        register_fixture_template(connection)
        connection.execute("DELETE FROM open_po WHERE po_id LIKE 'PO-92000%'")
        count_before = int(connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0])
    finally:
        connection.close()

    dependency = _connection_dependency(db_copy)
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[get_read_write_db] = dependency
    try:
        content = (FIXTURES / "导入_可修复.xlsx").read_bytes()
        with TestClient(app) as client:
            repaired = client.post(
                "/api/ingest/repair",
                files={
                    "file": (
                        "导入_可修复.xlsx",
                        content,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert repaired.status_code == 200
            payload = repaired.json()
            assert payload["report"]["valid_count"] == 5
            token = payload["report"]["validation_token"]
            confirmed = client.post("/api/ingest/confirm", json={"validation_token": token})
            assert confirmed.status_code == 200
            batch_id = confirmed.json()["batch_id"]

            with duckdb.connect(str(db_copy), read_only=True) as check:
                assert check.execute(
                    "SELECT qty, eta_date FROM open_po WHERE po_id = 'PO-920001'"
                ).fetchone() == (1200, date(2016, 6, 8))
                assert (
                    check.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before + 5
                )

            with duckdb.connect(str(db_copy)) as writable:
                assert rollback_batch(writable, batch_id) == 5
                writable.execute("DROP TABLE ingest_template")

            no_template = client.post(
                "/api/ingest/repair",
                files={"file": ("again.xlsx", content, "application/octet-stream")},
            )
            assert no_template.status_code == 404
            assert no_template.json()["detail"]["code"] == "template_not_found"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_read_write_db, None)


def test_repair_module_has_no_llm_randomness_or_fuzzy_matching() -> None:
    source = (REPO_ROOT / "api" / "ingest" / "repair.py").read_text(encoding="utf-8").casefold()
    forbidden = ("agent.llm", "get_llm", "deepseek", "import random", "difflib", "levenshtein")
    assert all(term not in source for term in forbidden)
