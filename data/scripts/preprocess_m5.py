"""Filter and aggregate the M5 inputs into deterministic parquet datasets."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
SALES_FILE = RAW_DIR / "sales_train_evaluation.csv"
CALENDAR_FILE = RAW_DIR / "calendar.csv"
PRICES_FILE = RAW_DIR / "sell_prices.csv"
HISTORY_DAYS = 1095
SKU_COUNT = 100


def write_parquet(frame: pd.DataFrame, path: Path, order_by: str) -> None:
    """Write a dataframe with a stable row order through DuckDB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    try:
        connection.register("frame", frame)
        connection.execute(
            f"COPY (SELECT * FROM frame ORDER BY {order_by}) TO ? "
            "(FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(path)],
        )
    finally:
        connection.close()


def load_filtered_sales() -> pd.DataFrame:
    """Read the wide M5 sales CSV in row chunks and keep the requested segment."""
    required = {"item_id", "dept_id", "cat_id", "store_id"}
    filtered_chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(SALES_FILE, chunksize=1000):
        missing = required.difference(chunk.columns)
        if missing:
            raise ValueError(f"sales_train_evaluation.csv 缺少字段: {sorted(missing)}")
        mask = (
            chunk["cat_id"].eq("FOODS")
            & chunk["dept_id"].eq("FOODS_3")
            & chunk["store_id"].str.startswith("CA_")
        )
        if mask.any():
            filtered_chunks.append(chunk.loc[mask])
    if not filtered_chunks:
        raise ValueError("未找到 FOODS/FOODS_3/CA_ 销量记录")
    return pd.concat(filtered_chunks, ignore_index=True)


def preprocess() -> None:
    """Build sales_daily, calendar, and prices parquet files."""
    missing_files = [path.name for path in (SALES_FILE, CALENDAR_FILE, PRICES_FILE) if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(f"data/raw 缺少: {', '.join(missing_files)}")

    calendar_raw = pd.read_csv(CALENDAR_FILE, parse_dates=["date"])
    sales_filtered = load_filtered_sales()
    day_columns = sorted(
        (column for column in sales_filtered.columns if column.startswith("d_")),
        key=lambda value: int(value[2:]),
    )
    calendar_map = calendar_raw.loc[calendar_raw["d"].isin(day_columns), ["d", "date"]]
    calendar_map = calendar_map.sort_values("date").tail(HISTORY_DAYS)
    selected_days = calendar_map["d"].tolist()

    aggregated = sales_filtered.groupby("item_id", sort=True)[selected_days].sum()
    totals = aggregated.sum(axis=1).rename("total_units").reset_index()
    totals = totals.sort_values(
        ["total_units", "item_id"], ascending=[False, True], kind="mergesort"
    )
    selected_skus = totals.head(SKU_COUNT)["item_id"].tolist()
    if len(selected_skus) != SKU_COUNT:
        raise ValueError(f"可用 SKU 不足 {SKU_COUNT} 个，实际 {len(selected_skus)}")

    sales_wide = aggregated.loc[selected_skus, selected_days].copy()
    sales_daily = sales_wide.reset_index().melt(
        id_vars="item_id", var_name="d", value_name="units_sold"
    )
    sales_daily = sales_daily.merge(calendar_map, on="d", how="left", validate="many_to_one")
    sales_daily = sales_daily.rename(columns={"item_id": "sku_id"})[
        ["sku_id", "date", "units_sold"]
    ]
    sales_daily["units_sold"] = sales_daily["units_sold"].astype("int64")

    event_1 = calendar_raw.get("event_name_1", pd.Series(index=calendar_raw.index, dtype="object"))
    event_2 = calendar_raw.get("event_name_2", pd.Series(index=calendar_raw.index, dtype="object"))
    event_name = event_1.fillna(event_2)
    both_events = event_1.notna() & event_2.notna()
    event_name.loc[both_events] = event_1.loc[both_events] + ";" + event_2.loc[both_events]
    calendar = calendar_raw.assign(event_name=event_name)
    calendar = calendar.loc[calendar["d"].isin(selected_days), ["date", "weekday", "event_name"]]
    calendar["is_weekend"] = calendar["date"].dt.dayofweek >= 5

    week_starts = calendar_raw.groupby("wm_yr_wk", as_index=False)["date"].min()
    week_starts = week_starts.rename(columns={"date": "week_start"})
    selected_weeks = calendar_raw.loc[calendar_raw["d"].isin(selected_days), "wm_yr_wk"].unique()
    prices_raw = pd.read_csv(PRICES_FILE)
    prices_raw = prices_raw.loc[
        prices_raw["item_id"].isin(selected_skus)
        & prices_raw["store_id"].str.startswith("CA_")
        & prices_raw["wm_yr_wk"].isin(selected_weeks)
    ]
    prices = (
        prices_raw.groupby(["item_id", "wm_yr_wk"], as_index=False)["sell_price"]
        .median()
        .merge(week_starts, on="wm_yr_wk", how="left", validate="many_to_one")
        .rename(columns={"item_id": "sku_id"})[["sku_id", "week_start", "sell_price"]]
    )
    prices["sell_price"] = prices["sell_price"].round(2)

    write_parquet(sales_daily, PROCESSED_DIR / "sales_daily.parquet", "sku_id, date")
    write_parquet(calendar, PROCESSED_DIR / "calendar.parquet", "date")
    write_parquet(prices, PROCESSED_DIR / "prices.parquet", "sku_id, week_start")
    print(
        f"预处理完成: sales_daily={len(sales_daily)}, calendar={len(calendar)}, "
        f"prices={len(prices)}, skus={len(selected_skus)}"
    )


def main() -> None:
    """Run M5 preprocessing."""
    preprocess()


if __name__ == "__main__":
    main()
