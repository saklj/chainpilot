"""Smoke tests for the Power BI star-model export."""

from __future__ import annotations

import codecs
import hashlib
import shutil
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"
sys.path.insert(0, str(REPO_ROOT))

from data.scripts.export_bi import EXPORT_QUERIES, export_star_model  # noqa: E402


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of an exported file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(not REAL_DB.is_file(), reason="real DuckDB fixture is unavailable")
def test_export_star_model_smoke_is_complete_and_idempotent(tmp_path: Path) -> None:
    """Export a disposable DB copy and verify both formats and star relationships."""
    db_copy = tmp_path / "chainpilot.duckdb"
    output_dir = tmp_path / "bi"
    shutil.copyfile(REAL_DB, db_copy)

    source = duckdb.connect(str(db_copy), read_only=True)
    try:
        source_counts = {
            "fact_material_risk": source.execute(
                "SELECT COUNT(*) FROM material_risk"
            ).fetchone()[0],
            "dim_material": source.execute("SELECT COUNT(*) FROM materials").fetchone()[0],
        }
        first_counts = export_star_model(source, output_dir)
    finally:
        source.close()

    assert first_counts["fact_material_risk"] == source_counts["fact_material_risk"] == 2700
    assert first_counts["dim_material"] == source_counts["dim_material"] == 300

    for table_name in EXPORT_QUERIES:
        for suffix in ("parquet", "csv"):
            artifact = output_dir / f"{table_name}.{suffix}"
            assert artifact.is_file()
            assert artifact.stat().st_size > 0
        assert (output_dir / f"{table_name}.csv").read_bytes().startswith(codecs.BOM_UTF8)

    check = duckdb.connect()
    try:
        fact_path = str(output_dir / "fact_material_risk.parquet")
        material_path = str(output_dir / "dim_material.parquet")
        supplier_path = str(output_dir / "dim_supplier.parquet")
        bridge_path = str(output_dir / "bridge_supply_split.parquet")
        date_path = str(output_dir / "dim_date.parquet")

        fact_orphans = check.execute(
            "SELECT COUNT(*) FROM read_parquet(?) f LEFT JOIN read_parquet(?) m "
            "USING (material_pn) WHERE m.material_pn IS NULL",
            [fact_path, material_path],
        ).fetchone()[0]
        bridge_material_orphans = check.execute(
            "SELECT COUNT(*) FROM read_parquet(?) b LEFT JOIN read_parquet(?) m "
            "USING (material_pn) WHERE m.material_pn IS NULL",
            [bridge_path, material_path],
        ).fetchone()[0]
        bridge_supplier_orphans = check.execute(
            "SELECT COUNT(*) FROM read_parquet(?) b LEFT JOIN read_parquet(?) s "
            "USING (supplier_id) WHERE s.supplier_id IS NULL",
            [bridge_path, supplier_path],
        ).fetchone()[0]
        assert fact_orphans == bridge_material_orphans == bridge_supplier_orphans == 0

        date_summary = check.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE is_current) FROM read_parquet(?)",
            [date_path],
        ).fetchone()
        assert date_summary == (9, 1)
    finally:
        check.close()

    first_hashes = {
        table_name: _sha256(output_dir / f"{table_name}.parquet")
        for table_name in EXPORT_QUERIES
    }
    source = duckdb.connect(str(db_copy), read_only=True)
    try:
        second_counts = export_star_model(source, output_dir)
    finally:
        source.close()
    second_hashes = {
        table_name: _sha256(output_dir / f"{table_name}.parquet")
        for table_name in EXPORT_QUERIES
    }

    assert second_counts == first_counts
    assert second_hashes == first_hashes
