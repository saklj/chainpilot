"""Build and validate the ChainPilot DuckDB database from processed parquet files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
SOURCE_TABLES = (
    "sales_daily",
    "calendar",
    "prices",
    "products",
    "materials",
    "bom",
    "suppliers",
    "supply_split",
    "inventory_onhand",
    "open_po",
)
TABLE_SCHEMAS = {
    "sales_daily": "sku_id VARCHAR, date DATE, units_sold INTEGER, PRIMARY KEY (sku_id, date)",
    "calendar": "date DATE PRIMARY KEY, weekday VARCHAR, event_name VARCHAR, is_weekend BOOLEAN",
    "prices": "sku_id VARCHAR, week_start DATE, sell_price DECIMAL(8,2)",
    "products": "sku_id VARCHAR PRIMARY KEY, product_name VARCHAR, product_family VARCHAR",
    "materials": (
        "material_pn VARCHAR PRIMARY KEY, material_name VARCHAR, commodity VARCHAR, "
        "item_group VARCHAR, unit_cost DECIMAL(8,4)"
    ),
    "bom": (
        "sku_id VARCHAR, material_pn VARCHAR, qty_per_unit DECIMAL(8,3), "
        "PRIMARY KEY (sku_id, material_pn)"
    ),
    "suppliers": "supplier_id VARCHAR PRIMARY KEY, supplier_name VARCHAR, region VARCHAR",
    "supply_split": (
        "material_pn VARCHAR, supplier_id VARCHAR, split_pct DECIMAL(5,2), "
        "lead_time_days INTEGER, moq INTEGER, PRIMARY KEY (material_pn, supplier_id)"
    ),
    "inventory_onhand": (
        "material_pn VARCHAR, snapshot_date DATE, qty_onhand INTEGER, "
        "PRIMARY KEY (material_pn, snapshot_date)"
    ),
    "open_po": (
        "po_id VARCHAR PRIMARY KEY, material_pn VARCHAR, supplier_id VARCHAR, qty INTEGER, "
        "eta_date DATE"
    ),
}
ROW_RANGES = {
    "sales_daily": (100_000, 120_000),
    "calendar": (1_000, 1_100),
    "prices": (1, 100_000),
    "products": (90, 110),
    "materials": (280, 320),
    "bom": (300, 800),
    "suppliers": (35, 45),
    "supply_split": (400, 650),
    "inventory_onhand": (45_000, 50_000),
    "open_po": (1_200, 1_800),
}


def database_path() -> Path:
    """Resolve DUCKDB_PATH relative to the repository root when necessary."""
    configured = Path(os.environ.get("DUCKDB_PATH", "data/chainpilot.duckdb"))
    return configured if configured.is_absolute() else REPO_ROOT / configured


def validate_split_totals(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Return details for material supplier shares that do not total 100 percent."""
    rows = connection.execute(
        "SELECT material_pn, SUM(split_pct) AS total_pct "
        "FROM supply_split GROUP BY material_pn "
        "HAVING ABS(SUM(split_pct) - 100.0) > 0.01 ORDER BY material_pn"
    ).fetchall()
    return [f"{material_pn}: total={float(total_pct):.2f}" for material_pn, total_pct in rows]


def subset_errors(
    connection: duckdb.DuckDBPyConnection,
    child_table: str,
    child_column: str,
    parent_table: str,
    parent_column: str,
) -> list[str]:
    """Return orphan values for one documented foreign-key relationship."""
    rows = connection.execute(
        f"SELECT DISTINCT c.{child_column} FROM {child_table} c "
        f"LEFT JOIN {parent_table} p ON c.{child_column} = p.{parent_column} "
        f"WHERE p.{parent_column} IS NULL ORDER BY 1"
    ).fetchall()
    return [str(row[0]) for row in rows]


def validate_database(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Run every data-dictionary validation and raise with full failure details."""
    errors: list[str] = []
    counts: dict[str, int] = {}
    for table_name, (minimum, maximum) in ROW_RANGES.items():
        count = int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
        counts[table_name] = count
        if not minimum <= count <= maximum:
            errors.append(
                f"{table_name} 行数越界: {count}, expected=[{minimum}, {maximum}]"
            )

    relationships = (
        ("bom", "sku_id", "products", "sku_id"),
        ("bom", "material_pn", "materials", "material_pn"),
        ("supply_split", "material_pn", "materials", "material_pn"),
        ("supply_split", "supplier_id", "suppliers", "supplier_id"),
        ("open_po", "material_pn", "materials", "material_pn"),
        ("open_po", "supplier_id", "suppliers", "supplier_id"),
    )
    for child_table, child_column, parent_table, parent_column in relationships:
        orphans = subset_errors(
            connection, child_table, child_column, parent_table, parent_column
        )
        if orphans:
            errors.append(
                f"外键不完整 {child_table}.{child_column} → {parent_table}.{parent_column}: "
                + ", ".join(orphans[:10])
            )

    split_errors = validate_split_totals(connection)
    if split_errors:
        errors.append("split_pct 合计错误: " + ", ".join(split_errors[:10]))

    shared_ratio = float(
        connection.execute(
            "WITH usage AS ("
            "  SELECT material_pn, COUNT(DISTINCT sku_id) AS sku_count FROM bom GROUP BY 1"
            ") SELECT COUNT(*) FILTER (WHERE sku_count > 1)::DOUBLE / COUNT(*) FROM usage"
        ).fetchone()[0]
    )
    if not 0.15 <= shared_ratio <= 0.25:
        errors.append(f"共用料占比越界: {shared_ratio:.2%}, expected=15%~25%")

    duplicate_sales = int(
        connection.execute(
            "SELECT COUNT(*) FROM (SELECT sku_id, date FROM sales_daily "
            "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    )
    negative_sales = int(
        connection.execute("SELECT COUNT(*) FROM sales_daily WHERE units_sold < 0").fetchone()[0]
    )
    if duplicate_sales:
        errors.append(f"sales_daily 重复键数量: {duplicate_sales}")
    if negative_sales:
        errors.append(f"sales_daily 负销量数量: {negative_sales}")

    if errors:
        raise ValueError("数据库校验失败:\n- " + "\n- ".join(errors))
    counts["forecast_daily"] = 0
    counts["forecast_metrics"] = 0
    counts["material_risk"] = 0
    return counts


def create_source_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Create typed source tables and load all parquet inputs."""
    for table_name in SOURCE_TABLES:
        parquet_path = PROCESSED_DIR / f"{table_name}.parquet"
        if not parquet_path.is_file():
            raise FileNotFoundError(f"缺少 {parquet_path}")
        connection.execute(f"CREATE TABLE {table_name} ({TABLE_SCHEMAS[table_name]})")
        connection.execute(
            f"INSERT INTO {table_name} SELECT * FROM read_parquet(?)", [str(parquet_path)]
        )


def create_engine_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the empty M2/M3 output tables with documented schemas."""
    connection.execute(
        "CREATE TABLE forecast_daily ("
        "sku_id VARCHAR, date DATE, model_name VARCHAR, yhat DECIMAL(10,2), "
        "PRIMARY KEY (sku_id, date, model_name))"
    )
    connection.execute(
        "CREATE TABLE forecast_metrics ("
        "model_name VARCHAR, fold INTEGER, mape DECIMAL, wrmsse DECIMAL)"
    )
    connection.execute(
        "CREATE TABLE material_risk ("
        "material_pn VARCHAR, eval_date DATE, doi_days DECIMAL(6,1), "
        "lt_coverage DECIMAL(5,2), supplier_concentration DECIMAL(5,2), "
        "gap_qty INTEGER, gap_date DATE, risk_level VARCHAR, risk_reasons VARCHAR, "
        "PRIMARY KEY (material_pn, eval_date))"
    )
    connection.execute(
        "CREATE VIEW v_material_demand_daily AS "
        "SELECT b.material_pn, f.date, SUM(f.yhat * b.qty_per_unit) AS demand_qty "
        "FROM forecast_daily f JOIN bom b USING (sku_id) "
        "WHERE f.model_name = 'lightgbm' GROUP BY 1, 2"
    )


def build_database() -> None:
    """Rebuild the database, validate it, and print actual table counts."""
    target = database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    connection = duckdb.connect(str(target))
    try:
        create_source_tables(connection)
        create_engine_tables(connection)
        counts = validate_database(connection)
    except Exception:
        connection.close()
        target.unlink(missing_ok=True)
        raise
    else:
        connection.close()
    print(f"DuckDB 构建成功: {target}")
    for table_name, count in counts.items():
        print(f"  {table_name}: {count}")
    print("全部校验通过")


def main() -> None:
    """Build the database and use exit code one for validation failures."""
    try:
        build_database()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
