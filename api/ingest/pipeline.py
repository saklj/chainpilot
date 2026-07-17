"""Pure-code row validation, transactional import, and batch rollback."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import duckdb

from ingest.database import ensure_ingest_tables, load_template, table_exists
from ingest.errors import IngestError
from ingest.models import ValidationError, ValidationReport, ValidatedRow
from ingest.workbook import read_first_sheet


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, (float, Decimal)):
        integer = int(value)
        return integer if value == integer and integer > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        integer = int(value.strip())
        return integer if integer > 0 else None
    return None


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.isoformat() == text else None
    return None


def _known_values(connection: duckdb.DuckDBPyConnection, table: str, column: str) -> set[str]:
    return {str(row[0]) for row in connection.execute(f"SELECT {column} FROM {table}").fetchall()}


def validate_file(
    connection: duckdb.DuckDBPyConnection,
    file_bytes: bytes,
    *,
    filename: str = "upload.xlsx",
) -> ValidationReport:
    """Validate every row with code only; material_risk is not recalculated here."""
    template = load_template(connection)
    if template is None:
        raise IngestError("template_not_found", "请先注册 Excel 列映射模板", status_code=404)
    mapping, _ = template
    columns, rows = read_first_sheet(file_bytes)
    missing = sorted(set(mapping.values()) - set(columns))
    if missing:
        raise IngestError("template_columns_missing", f"上传文件缺少模板列：{', '.join(missing)}")

    indexes = {target: columns.index(source) for target, source in mapping.items()}
    materials = _known_values(connection, "materials", "material_pn")
    suppliers = _known_values(connection, "suppliers", "supplier_id")
    existing_pos = _known_values(connection, "open_po", "po_id")
    seen_pos: set[str] = set()
    valid_rows: list[ValidatedRow] = []
    errors: list[ValidationError] = []

    for row_number, row in enumerate(rows, start=2):
        po_id = _text(row[indexes["po_id"]])
        material_pn = _text(row[indexes["material_pn"]])
        supplier_id = _text(row[indexes["supplier_id"]])
        qty = _positive_integer(row[indexes["qty"]])
        eta_date = _date(row[indexes["eta_date"]])
        row_errors: list[ValidationError] = []

        if not po_id:
            row_errors.append(ValidationError(row_number, "po_id", "required", "采购单号不能为空"))
        elif po_id in seen_pos:
            row_errors.append(
                ValidationError(row_number, "po_id", "duplicate_in_file", "采购单号在文件内重复")
            )
        elif po_id in existing_pos:
            row_errors.append(
                ValidationError(row_number, "po_id", "already_exists", "采购单号已存在于 open_po")
            )
        if po_id:
            seen_pos.add(po_id)

        if material_pn not in materials:
            row_errors.append(
                ValidationError(
                    row_number, "material_pn", "unknown_material", "物料号不存在于 materials"
                )
            )
        if supplier_id not in suppliers:
            row_errors.append(
                ValidationError(
                    row_number, "supplier_id", "unknown_supplier", "供应商不存在于 suppliers"
                )
            )
        if qty is None:
            row_errors.append(
                ValidationError(row_number, "qty", "invalid_positive_integer", "数量必须为正整数")
            )
        if eta_date is None:
            row_errors.append(
                ValidationError(
                    row_number,
                    "eta_date",
                    "invalid_date",
                    "到货日必须是 ISO 日期或 Excel 日期单元格",
                )
            )

        if row_errors:
            errors.extend(row_errors)
        else:
            valid_rows.append(
                ValidatedRow(
                    po_id=po_id,
                    material_pn=material_pn,
                    supplier_id=supplier_id,
                    qty=qty,
                    eta_date=eta_date,
                )
            )

    return ValidationReport(
        filename=filename,
        total_rows=len(rows),
        valid_rows=tuple(valid_rows),
        errors=tuple(errors),
    )


def import_rows(connection: duckdb.DuckDBPyConnection, report: ValidationReport) -> str:
    """Import validated rows atomically; risk snapshots update only on the next engine run."""
    if not report.valid_rows:
        raise IngestError("no_valid_rows", "没有可导入的合法行")
    ensure_ingest_tables(connection)
    batch_id = f"ING-{uuid4().hex}"
    created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.executemany(
            "INSERT INTO open_po (po_id, material_pn, supplier_id, qty, eta_date) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (row.po_id, row.material_pn, row.supplier_id, row.qty, row.eta_date)
                for row in report.valid_rows
            ],
        )
        connection.execute(
            "INSERT INTO ingest_batch VALUES (?, ?, ?, ?)",
            [batch_id, report.filename, report.valid_count, created_at],
        )
        connection.executemany(
            "INSERT INTO ingest_batch_row VALUES (?, ?)",
            [(batch_id, row.po_id) for row in report.valid_rows],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return batch_id


def rollback_batch(connection: duckdb.DuckDBPyConnection, batch_id: str) -> int:
    if not table_exists(connection, "ingest_batch"):
        raise IngestError("batch_not_found", "导入批次不存在", status_code=404)
    row = connection.execute(
        "SELECT row_count FROM ingest_batch WHERE batch_id = ?", [batch_id]
    ).fetchone()
    if row is None:
        raise IngestError("batch_not_found", "导入批次不存在", status_code=404)
    connection.execute("BEGIN TRANSACTION")
    try:
        deleted_rows = connection.execute(
            "DELETE FROM open_po WHERE po_id IN ("
            "SELECT po_id FROM ingest_batch_row WHERE batch_id = ?) RETURNING po_id",
            [batch_id],
        ).fetchall()
        deleted_count = len(deleted_rows)
        connection.execute("DELETE FROM ingest_batch_row WHERE batch_id = ?", [batch_id])
        connection.execute("DELETE FROM ingest_batch WHERE batch_id = ?", [batch_id])
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return deleted_count
