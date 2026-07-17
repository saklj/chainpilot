"""Smoke coverage for template-driven, reversible Excel ingestion."""

from __future__ import annotations

import shutil
from datetime import date
from io import BytesIO
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.deps import get_db, get_read_write_db
from ingest.errors import IngestError
from ingest.pipeline import import_rows, rollback_batch, validate_file
from ingest.templates import get_template, save_template, suggest_mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"
CANONICAL_MAPPING = {
    "po_id": "po_id",
    "material_pn": "material_pn",
    "supplier_id": "supplier_id",
    "qty": "qty",
    "eta_date": "eta_date",
}


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


@pytest.fixture
def db_copy(tmp_path: Path) -> Path:
    if not REAL_DB.is_file():
        pytest.skip("real DuckDB fixture is unavailable")
    destination = tmp_path / "chainpilot.duckdb"
    shutil.copyfile(REAL_DB, destination)
    # The real DB evolves as the user registers templates / imports batches;
    # normalize the copy to "no ingest state" so assertions stay deterministic.
    with duckdb.connect(str(destination)) as connection:
        for table in ("ingest_batch_row", "ingest_batch", "ingest_template"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
    return destination


class RecordingSuggester:
    def __init__(self, response: dict[str, str | None]) -> None:
        self.response = response
        self.calls: list[tuple[list[str], list[str]]] = []

    def suggest(
        self, sample_columns: list[str], target_columns: list[str]
    ) -> dict[str, str | None]:
        self.calls.append((sample_columns, target_columns))
        return self.response


def test_deterministic_aliases_run_before_optional_suggester() -> None:
    columns = ["采购单号", "物料号", "合作方代码", "数量", "预计到货"]
    suggester = RecordingSuggester({"supplier_id": "合作方代码"})
    mapping = suggest_mapping(columns, suggester)

    assert mapping == {
        "po_id": "采购单号",
        "material_pn": "物料号",
        "supplier_id": "合作方代码",
        "qty": "数量",
        "eta_date": "预计到货",
    }
    assert suggester.calls == [(columns, ["supplier_id"])]


def test_template_save_load_overwrite_and_incomplete_mapping(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        current = get_template(connection)
        assert current is not None
        assert current[0] == CANONICAL_MAPPING

        chinese_mapping = {
            "po_id": "采购单号",
            "material_pn": "物料号",
            "supplier_id": "供应商",
            "qty": "数量",
            "eta_date": "到货日",
        }
        save_template(connection, chinese_mapping)
        assert get_template(connection)[0] == chinese_mapping  # type: ignore[index]
        assert connection.execute("SELECT COUNT(*) FROM ingest_template").fetchone()[0] == 1

        with pytest.raises(IngestError, match="完整覆盖") as exc_info:
            save_template(connection, {"po_id": "采购单号"})
        assert exc_info.value.code == "invalid_mapping"
    finally:
        connection.close()


def test_validation_reports_four_bad_row_types_and_one_valid_row(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        material_pn = str(
            connection.execute("SELECT material_pn FROM materials LIMIT 1").fetchone()[0]
        )
        supplier_id = str(
            connection.execute("SELECT supplier_id FROM suppliers LIMIT 1").fetchone()[0]
        )
        payload = workbook_bytes(
            list(CANONICAL_MAPPING),
            [
                ["PO-NEW-VALID", material_pn, supplier_id, 10, date(2026, 8, 1)],
                ["PO-NEW-VALID", material_pn, supplier_id, 10, "2026-08-02"],
                ["PO-NEW-MATERIAL", "PN-UNKNOWN", supplier_id, 10, "2026-08-03"],
                ["PO-NEW-QTY", material_pn, supplier_id, -1, "2026-08-04"],
                ["PO-NEW-DATE", material_pn, supplier_id, 10, "08/05/2026"],
            ],
        )
        report = validate_file(connection, payload, filename="bad-rows.xlsx")
        assert report.total_rows == 5
        assert report.valid_count == 1
        assert report.error_count == 4
        assert [(error.row, error.field, error.code) for error in report.errors] == [
            (3, "po_id", "duplicate_in_file"),
            (4, "material_pn", "unknown_material"),
            (5, "qty", "invalid_positive_integer"),
            (6, "eta_date", "invalid_date"),
        ]
    finally:
        connection.close()


def test_import_tracks_only_valid_rows_and_rollback_restores_counts(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        material_pn = str(
            connection.execute("SELECT material_pn FROM materials LIMIT 1").fetchone()[0]
        )
        supplier_id = str(
            connection.execute("SELECT supplier_id FROM suppliers LIMIT 1").fetchone()[0]
        )
        existing_po = str(connection.execute("SELECT po_id FROM open_po LIMIT 1").fetchone()[0])
        count_before = int(connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0])
        payload = workbook_bytes(
            list(CANONICAL_MAPPING),
            [
                ["PO-NEW-IMPORT", material_pn, supplier_id, 20, "2026-09-01"],
                [existing_po, material_pn, supplier_id, 20, "2026-09-01"],
            ],
        )
        report = validate_file(connection, payload, filename="mixed.xlsx")
        assert report.valid_count == 1
        assert [(error.field, error.code) for error in report.errors] == [
            ("po_id", "already_exists")
        ]

        batch_id = import_rows(connection, report)
        assert connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before + 1
        assert connection.execute(
            "SELECT filename, row_count FROM ingest_batch WHERE batch_id = ?", [batch_id]
        ).fetchone() == ("mixed.xlsx", 1)
        assert connection.execute(
            "SELECT po_id FROM ingest_batch_row WHERE batch_id = ?", [batch_id]
        ).fetchone() == ("PO-NEW-IMPORT",)

        assert rollback_batch(connection, batch_id) == 1
        assert connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM ingest_batch WHERE batch_id = ?", [batch_id]
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM ingest_batch_row WHERE batch_id = ?", [batch_id]
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def _override_connection(db_path: Path):
    def dependency():
        connection = duckdb.connect(str(db_path))
        try:
            yield connection
        finally:
            connection.close()

    return dependency


def test_routes_full_two_step_flow_and_structured_errors(
    db_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    from app.main import app
    from app.routers.ingest import get_mapping_suggester

    dependency = _override_connection(db_copy)
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[get_read_write_db] = dependency
    app.dependency_overrides[get_mapping_suggester] = lambda: None
    try:
        with TestClient(app) as client:
            direct = client.post(
                "/api/ingest/confirm", json={"validation_token": "never-validated"}
            )
            assert direct.status_code == 400
            assert direct.json()["detail"]["code"] == "validation_token_invalid"

            no_template_file = workbook_bytes(list(CANONICAL_MAPPING), [])
            no_template = client.post(
                "/api/ingest/validate",
                files={
                    "file": (
                        "daily.xlsx",
                        no_template_file,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert no_template.status_code == 404
            assert no_template.json()["detail"]["code"] == "template_not_found"

            sample = workbook_bytes(["采购单号", "物料号", "供应商", "数量", "到货日"], [])
            preview = client.post(
                "/api/ingest/template/preview",
                files={
                    "file": (
                        "sample.xlsx",
                        sample,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert preview.status_code == 200
            mapping = preview.json()["suggested_mapping"]
            assert all(
                source == "deterministic"
                for source in preview.json()["suggestion_sources"].values()
            )
            registered = client.post("/api/ingest/template", json={"mapping": mapping})
            assert registered.status_code == 200
            assert client.get("/api/ingest/template").json()["exists"] is True

            with duckdb.connect(str(db_copy), read_only=True) as connection:
                material_pn = str(
                    connection.execute("SELECT material_pn FROM materials LIMIT 1").fetchone()[0]
                )
                supplier_id = str(
                    connection.execute("SELECT supplier_id FROM suppliers LIMIT 1").fetchone()[0]
                )
                count_before = int(connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0])
            daily = workbook_bytes(
                list(mapping.values()),
                [["PO-ROUTE-NEW", material_pn, supplier_id, 33, "2026-10-01"]],
            )
            checked = client.post(
                "/api/ingest/validate",
                files={
                    "file": (
                        "daily.xlsx",
                        daily,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert checked.status_code == 200
            assert checked.json()["valid_count"] == 1
            token = checked.json()["validation_token"]
            confirmed = client.post("/api/ingest/confirm", json={"validation_token": token})
            assert confirmed.status_code == 200
            batch_id = confirmed.json()["batch_id"]
            assert client.get("/api/ingest/batches").json()[0]["batch_id"] == batch_id

            rolled_back = client.post("/api/ingest/rollback", json={"batch_id": batch_id})
            assert rolled_back.json()["deleted_count"] == 1
            with duckdb.connect(str(db_copy), read_only=True) as connection:
                assert (
                    connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before
                )

            bad_type = client.post(
                "/api/ingest/template/preview",
                files={"file": ("sample.csv", b"a,b", "text/csv")},
            )
            assert bad_type.status_code == 400
            assert bad_type.json()["detail"]["code"] == "unsupported_file_type"

            oversized = client.post(
                "/api/ingest/template/preview",
                files={
                    "file": ("huge.xlsx", b"x" * (20 * 1024 * 1024 + 1), "application/octet-stream")
                },
            )
            assert oversized.status_code == 413
            assert oversized.json()["detail"]["code"] == "file_too_large"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_read_write_db, None)
        app.dependency_overrides.pop(get_mapping_suggester, None)


def test_preview_rejects_rows_beyond_limit(db_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("multipart")
    from app.main import app
    from app.routers.ingest import get_mapping_suggester

    # Shrink the limit so the trip-wire is exercised without building a 50k-row file.
    monkeypatch.setattr("ingest.workbook.MAX_DATA_ROWS", 50)
    dependency = _override_connection(db_copy)
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[get_read_write_db] = dependency
    app.dependency_overrides[get_mapping_suggester] = lambda: None
    try:
        payload = workbook_bytes(["po_id"], [[f"PO-{index}"] for index in range(51)])
        with TestClient(app) as client:
            response = client.post(
                "/api/ingest/template/preview",
                files={
                    "file": (
                        "too-many.xlsx",
                        payload,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "too_many_rows"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_read_write_db, None)
        app.dependency_overrides.pop(get_mapping_suggester, None)
