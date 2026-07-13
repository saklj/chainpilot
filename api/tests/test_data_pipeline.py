"""Tests for deterministic simulation data and pipeline validations."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from data.scripts.build_db import validate_split_totals  # noqa: E402
from data.scripts.generate_sim import SIM_TABLES, generate_sim  # noqa: E402


def write_sales_fixture(path: Path) -> None:
    """Create a small deterministic sales parquet without requiring Kaggle."""
    rows = []
    dates = pd.date_range("2023-01-01", periods=28, freq="D")
    for sku_index in range(1, 101):
        for day_index, sales_date in enumerate(dates):
            rows.append(
                (f"FOODS_3_{sku_index:03d}", sales_date, 5 + sku_index % 11 + day_index % 7)
            )
    frame = pd.DataFrame(rows, columns=["sku_id", "date", "units_sold"])
    path.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    try:
        connection.register("sales", frame)
        connection.execute(
            "COPY (SELECT * FROM sales ORDER BY sku_id, date) TO ? "
            "(FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(path / "sales_daily.parquet")],
        )
    finally:
        connection.close()


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of one generated artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generate_sim_is_byte_deterministic(tmp_path: Path) -> None:
    """Two independent processes overwrite outputs with byte-identical content."""
    processed = tmp_path / "processed"
    truth_path = tmp_path / "ground_truth.json"
    write_sales_fixture(processed)
    command = [
        sys.executable,
        str(REPO_ROOT / "data" / "scripts" / "generate_sim.py"),
        "--processed-dir",
        str(processed),
        "--ground-truth-path",
        str(truth_path),
    ]

    subprocess.run(command, check=True)
    first_hashes = {
        table_name: sha256(processed / f"{table_name}.parquet") for table_name in SIM_TABLES
    }
    first_truth_hash = sha256(truth_path)
    subprocess.run(command, check=True)

    assert first_hashes == {
        table_name: sha256(processed / f"{table_name}.parquet") for table_name in SIM_TABLES
    }
    assert first_truth_hash == sha256(truth_path)


def test_validate_split_totals() -> None:
    """The split validator accepts 100% totals and reports invalid totals."""
    connection = duckdb.connect()
    try:
        connection.execute("CREATE TABLE supply_split (material_pn VARCHAR, split_pct DOUBLE)")
        connection.execute("INSERT INTO supply_split VALUES ('PN-GOOD', 60), ('PN-GOOD', 40)")
        assert validate_split_totals(connection) == []
        connection.execute("INSERT INTO supply_split VALUES ('PN-BAD', 55), ('PN-BAD', 35)")
        assert validate_split_totals(connection) == ["PN-BAD: total=90.00"]
    finally:
        connection.close()


def test_ground_truth_has_ten_valid_materials(tmp_path: Path) -> None:
    """Ground truth contains ten scenarios and every PN exists in materials."""
    processed = tmp_path / "processed"
    truth_path = tmp_path / "ground_truth_scenarios.json"
    write_sales_fixture(processed)
    generate_sim(processed, truth_path)

    scenarios = json.loads(truth_path.read_text(encoding="utf-8"))
    assert len(scenarios) == 10
    scenario_materials = {scenario["material_pn"] for scenario in scenarios}
    assert len(scenario_materials) == 10

    connection = duckdb.connect()
    try:
        material_rows = connection.execute(
            "SELECT material_pn FROM read_parquet(?)",
            [str(processed / "materials.parquet")],
        ).fetchall()
    finally:
        connection.close()
    assert scenario_materials <= {row[0] for row in material_rows}
