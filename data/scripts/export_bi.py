"""Export the Power BI star model from DuckDB to Parquet and UTF-8-SIG CSV.

Star model::

                      dim_date
                         |
    dim_material --- fact_material_risk
          |
    bridge_supply_split --- dim_supplier

Power BI modelling notes:
- Create many-to-one relationships from fact_material_risk to dim_material and dim_date.
- The bridge connects dim_material and dim_supplier through material_pn/supplier_id.
- Enable bidirectional filtering on the bridge relationships only when supplier selections
  must filter material-risk facts; review ambiguity before enabling it elsewhere.
"""

from __future__ import annotations

import argparse
import codecs
import os
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "processed" / "bi"

EXPORT_QUERIES = {
    "fact_material_risk": (
        "SELECT material_pn, eval_date, risk_level, doi_days, lt_coverage, "
        "supplier_concentration, gap_qty, gap_date FROM material_risk "
        "ORDER BY material_pn, eval_date"
    ),
    "dim_material": (
        "SELECT material_pn, material_name, commodity, item_group, unit_cost "
        "FROM materials ORDER BY material_pn"
    ),
    "dim_supplier": (
        "SELECT supplier_id, supplier_name, region FROM suppliers ORDER BY supplier_id"
    ),
    "bridge_supply_split": (
        "SELECT material_pn, supplier_id, split_pct, lead_time_days, moq "
        "FROM supply_split ORDER BY material_pn, supplier_id"
    ),
    "dim_date": (
        "WITH dates AS (SELECT DISTINCT eval_date AS date FROM material_risk), "
        "latest AS (SELECT MAX(date) AS max_date FROM dates) "
        "SELECT date, YEAR(date)::INTEGER AS year, MONTH(date)::INTEGER AS month, "
        "WEEKOFYEAR(date)::INTEGER AS week_of_year, date = max_date AS is_current "
        "FROM dates CROSS JOIN latest ORDER BY date"
    ),
}


def database_path() -> Path:
    """Resolve DUCKDB_PATH relative to the repository root when necessary."""
    configured = Path(os.environ.get("DUCKDB_PATH", "data/chainpilot.duckdb"))
    return configured if configured.is_absolute() else REPO_ROOT / configured


def _resolved_path(path: Path) -> Path:
    """Resolve a CLI path relative to the repository root."""
    return path if path.is_absolute() else REPO_ROOT / path


def _scalar_count(connection: duckdb.DuckDBPyConnection, query: str) -> int:
    """Execute a count query and return its integer value."""
    return int(connection.execute(query).fetchone()[0])


def _validate_star_model(connection: duckdb.DuckDBPyConnection) -> None:
    """Validate required nullability and referential integrity before exporting."""
    errors: list[str] = []
    fact_orphans = _scalar_count(
        connection,
        "SELECT COUNT(*) FROM material_risk f LEFT JOIN materials m USING (material_pn) "
        "WHERE m.material_pn IS NULL",
    )
    if fact_orphans:
        errors.append(f"fact_material_risk.material_pn 存在 {fact_orphans} 条孤儿记录")

    bridge_material_orphans = _scalar_count(
        connection,
        "SELECT COUNT(*) FROM supply_split b LEFT JOIN materials m USING (material_pn) "
        "WHERE m.material_pn IS NULL",
    )
    if bridge_material_orphans:
        errors.append(
            "bridge_supply_split.material_pn 存在 "
            f"{bridge_material_orphans} 条孤儿记录"
        )

    bridge_supplier_orphans = _scalar_count(
        connection,
        "SELECT COUNT(*) FROM supply_split b LEFT JOIN suppliers s USING (supplier_id) "
        "WHERE s.supplier_id IS NULL",
    )
    if bridge_supplier_orphans:
        errors.append(
            "bridge_supply_split.supplier_id 存在 "
            f"{bridge_supplier_orphans} 条孤儿记录"
        )

    fact_nulls = _scalar_count(
        connection,
        "SELECT COUNT(*) FROM material_risk WHERE material_pn IS NULL "
        "OR eval_date IS NULL OR risk_level IS NULL OR doi_days IS NULL "
        "OR lt_coverage IS NULL OR supplier_concentration IS NULL OR gap_qty IS NULL",
    )
    if fact_nulls:
        errors.append(f"fact_material_risk 关键列存在 {fact_nulls} 条 NULL 记录")

    if errors:
        raise ValueError("Power BI 星型模型导出校验失败:\n- " + "\n- ".join(errors))


def _export_parquet(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    destination: Path,
) -> None:
    """Write an ordered query as a deterministic Parquet file."""
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        connection.execute(
            f"COPY ({query}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(temporary)],
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _export_csv(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    destination: Path,
) -> None:
    """Write an ordered query as CSV with a UTF-8 byte-order mark."""
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        connection.execute(
            f"COPY ({query}) TO ? (FORMAT CSV, HEADER)",
            [str(temporary)],
        )
        temporary.write_bytes(codecs.BOM_UTF8 + temporary.read_bytes())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_star_model(
    connection: duckdb.DuckDBPyConnection,
    output_dir: Path,
) -> dict[str, int]:
    """Validate and export the five-table Power BI star model in two formats."""
    _validate_star_model(connection)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for table_name, query in EXPORT_QUERIES.items():
        counts[table_name] = _scalar_count(
            connection, f"SELECT COUNT(*) FROM ({query}) AS exported"
        )
        _export_parquet(connection, query, output_dir / f"{table_name}.parquet")
        _export_csv(connection, query, output_dir / f"{table_name}.csv")
    return counts


def main() -> None:
    """Export the star model from a read-only database and print row counts."""
    parser = argparse.ArgumentParser(description="导出 Power BI 星型模型")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--db-path", type=Path)
    args = parser.parse_args()

    target = database_path() if args.db_path is None else _resolved_path(args.db_path)
    output_dir = _resolved_path(args.output)
    connection = duckdb.connect(str(target), read_only=True)
    try:
        counts = export_star_model(connection, output_dir)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        connection.close()

    print(f"Power BI 星型模型导出成功: {output_dir}")
    for table_name, count in counts.items():
        print(f"  {table_name}: {count}")


if __name__ == "__main__":
    main()
