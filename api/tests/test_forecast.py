"""Unit tests for deterministic forecasting and leakage-safe backtests."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from analytics.forecast import (
    HORIZON,
    mape,
    run_backtest,
    rolling_splits,
    seasonal_naive_forecast,
    wmape,
    wrmsse,
)


@pytest.fixture
def synthetic_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Create 5 SKU x 200 days through in-memory DuckDB, never the real database."""
    dates = pd.date_range("2024-01-01", periods=200, freq="D")
    rows = []
    for sku_index in range(5):
        rng = np.random.default_rng(42 + sku_index)
        signal = 20 + sku_index * 3 + 5 * np.sin(2 * np.pi * np.arange(200) / 7)
        units = np.maximum(1, np.rint(signal + rng.normal(0, 0.5, 200)))
        rows.extend((f"SKU-{sku_index}", date, value) for date, value in zip(dates, units))
    source_sales = pd.DataFrame(rows, columns=["sku_id", "date", "units_sold"])
    source_calendar = pd.DataFrame(
        {
            "date": dates,
            "event_name": ["Launch" if index == 100 else None for index in range(200)],
            "is_weekend": dates.dayofweek >= 5,
        }
    )
    source_prices = pd.DataFrame(
        [
            (f"SKU-{sku_index}", date, 2.0 + sku_index)
            for sku_index in range(5)
            for date in pd.date_range(dates.min(), dates.max(), freq="7D")
        ],
        columns=["sku_id", "week_start", "sell_price"],
    )
    connection = duckdb.connect(":memory:")
    try:
        connection.register("source_sales", source_sales)
        connection.register("source_calendar", source_calendar)
        connection.register("source_prices", source_prices)
        sales = connection.execute("SELECT * FROM source_sales ORDER BY sku_id, date").fetchdf()
        calendar = connection.execute("SELECT * FROM source_calendar ORDER BY date").fetchdf()
        prices = connection.execute(
            "SELECT * FROM source_prices ORDER BY sku_id, week_start"
        ).fetchdf()
    finally:
        connection.close()
    return sales, calendar, prices


def test_mape_and_wrmsse_match_hand_calculation() -> None:
    """Metric helpers match small examples calculable by hand."""
    assert mape([10, 0, 20], [8, 999, 25]) == pytest.approx(22.5)
    assert wmape([10, 0, 20], [8, 9, 25]) == pytest.approx(53.3333333333)

    dates = pd.date_range("2024-01-01", periods=4, freq="D")
    train = pd.DataFrame(
        {
            "sku_id": ["A"] * 4 + ["B"] * 4,
            "date": list(dates) * 2,
            "units_sold": [1, 2, 3, 4, 2, 4, 6, 8],
        }
    )
    actual = pd.DataFrame(
        {
            "sku_id": ["A", "A", "B", "B"],
            "date": [dates[-1] + pd.Timedelta(days=1), dates[-1] + pd.Timedelta(days=2)] * 2,
            "units_sold": [5, 5, 10, 10],
        }
    )
    predicted = actual[["sku_id", "date"]].copy()
    predicted["yhat"] = [4, 6, 8, 12]
    prices = pd.DataFrame(
        {"sku_id": ["A", "B"], "week_start": [dates[0], dates[0]], "sell_price": [1, 1]}
    )
    # RMSSE is 1 for both SKUs: RMSE=(1,2), scales=(1,2). Any valid weights give 1.
    assert wrmsse(actual, predicted, train, prices) == pytest.approx(1.0)


def test_wmape_rejects_zero_total_actual_demand() -> None:
    with pytest.raises(ValueError, match="total actual demand is zero"):
        wmape([0, 0], [1, 2])


def test_rolling_splits_are_contiguous_and_leakage_free(synthetic_frames) -> None:
    """Every training window ends at cutoff and every test has the next 28 dates."""
    sales, _, _ = synthetic_frames
    for split in rolling_splits(sales):
        assert split.train["date"].max() <= split.cutoff
        assert split.test["date"].min() == split.cutoff + pd.Timedelta(days=1)
        assert split.test["date"].max() == split.cutoff + pd.Timedelta(days=HORIZON)
        assert split.test["date"].nunique() == HORIZON


def test_lightgbm_forecast_is_exactly_deterministic(synthetic_frames) -> None:
    """Two LightGBM rolling backtests produce bit-identical fold forecasts."""
    sales, calendar, prices = synthetic_frames
    _, first = run_backtest(sales, calendar, prices, models=("lightgbm",))
    _, second = run_backtest(sales, calendar, prices, models=("lightgbm",))
    np.testing.assert_array_equal(first["yhat"].to_numpy(), second["yhat"].to_numpy())


def test_seasonal_naive_is_perfect_for_a_seven_day_cycle() -> None:
    """A repeated seven-day pattern has zero seasonal-naive MAPE."""
    dates = pd.date_range("2024-01-01", periods=70, freq="D")
    values = np.tile(np.arange(1, 8, dtype=float), 10)
    train = pd.DataFrame({"sku_id": "A", "date": dates[:42], "units_sold": values[:42]})
    target_dates = dates[42:70]
    prediction = seasonal_naive_forecast(train, target_dates)
    assert mape(values[42:70], prediction["yhat"]) == pytest.approx(0.0)
