"""Deterministic demand forecasting, rolling backtests, and DuckDB persistence."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoETS

REPO_ROOT = Path(__file__).resolve().parents[2]
HORIZON = 28
MODEL_NAMES = ("seasonal_naive", "ets", "lightgbm")
LGB_FEATURES = (
    "lag_28",
    "lag_29",
    "lag_30",
    "lag_31",
    "lag_32",
    "lag_33",
    "lag_34",
    "lag_35",
    "lag_42",
    "lag_56",
    "lag_364",
    "rolling_mean_7",
    "rolling_mean_28",
    "rolling_std_28",
    "day_of_week",
    "is_weekend",
    "has_event",
    "sell_price",
    "sku_id",
)


@dataclass(frozen=True)
class BacktestFold:
    """One expanding-window split with a contiguous forecast horizon."""

    fold: int
    cutoff: pd.Timestamp
    train: pd.DataFrame
    test: pd.DataFrame


def database_path() -> Path:
    """Resolve DUCKDB_PATH relative to the repository root when necessary."""
    configured = Path(os.environ.get("DUCKDB_PATH", "data/chainpilot.duckdb"))
    return configured if configured.is_absolute() else REPO_ROOT / configured


def load_source_frames(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read only the three forecasting source tables from DuckDB."""
    sales = connection.execute(
        "SELECT sku_id, date, units_sold FROM sales_daily ORDER BY sku_id, date"
    ).fetchdf()
    calendar = connection.execute(
        "SELECT date, event_name, is_weekend FROM calendar ORDER BY date"
    ).fetchdf()
    prices = connection.execute(
        "SELECT sku_id, week_start, sell_price FROM prices ORDER BY sku_id, week_start"
    ).fetchdf()
    for frame, column in ((sales, "date"), (calendar, "date"), (prices, "week_start")):
        frame[column] = pd.to_datetime(frame[column]).astype("datetime64[ns]")
    sales["units_sold"] = sales["units_sold"].astype(float)
    prices["sell_price"] = prices["sell_price"].astype(float)
    return sales, calendar, prices


def rolling_splits(
    sales: pd.DataFrame, horizon: int = HORIZON, n_folds: int = 3
) -> list[BacktestFold]:
    """Create expanding folds ending at T-84, T-56, and T-28 for default settings."""
    ordered = sales.sort_values(["sku_id", "date"]).copy()
    ordered["date"] = pd.to_datetime(ordered["date"])
    final_date = ordered["date"].max()
    folds: list[BacktestFold] = []
    for fold in range(1, n_folds + 1):
        cutoff = final_date - pd.Timedelta(days=horizon * (n_folds - fold + 1))
        test_end = cutoff + pd.Timedelta(days=horizon)
        train = ordered.loc[ordered["date"] <= cutoff].copy()
        test = ordered.loc[
            (ordered["date"] > cutoff) & (ordered["date"] <= test_end)
        ].copy()
        expected_dates = pd.date_range(cutoff + pd.Timedelta(days=1), test_end, freq="D")
        if train.empty or test.empty or not np.array_equal(
            np.sort(test["date"].unique()), expected_dates.to_numpy()
        ):
            raise ValueError(f"Fold {fold} does not contain a complete {horizon}-day test window")
        folds.append(BacktestFold(fold, cutoff, train, test))
    return folds


def mape(y_true: Iterable[float], y_pred: Iterable[float]) -> float:
    """Return percentage MAPE, excluding observations whose actual value is zero."""
    actual = np.asarray(list(y_true), dtype=float)
    predicted = np.asarray(list(y_pred), dtype=float)
    if actual.shape != predicted.shape:
        raise ValueError("y_true and y_pred must have the same shape")
    mask = actual > 0
    if not mask.any():
        raise ValueError("MAPE is undefined when every actual value is zero")
    return float(np.mean(np.abs(actual[mask] - predicted[mask]) / actual[mask]) * 100)


def wmape(y_true: Iterable[float], y_pred: Iterable[float]) -> float:
    """Return sales-volume-weighted absolute percentage error."""
    actual = np.asarray(list(y_true), dtype=float)
    predicted = np.asarray(list(y_pred), dtype=float)
    if actual.shape != predicted.shape:
        raise ValueError("y_true and y_pred must have the same shape")
    denominator = actual.sum()
    if denominator == 0:
        raise ValueError("WMAPE is undefined when total actual demand is zero")
    return float(np.abs(actual - predicted).sum() / denominator * 100)


def attach_prices(dates: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Attach the most recent known weekly price to each SKU/date row."""
    left = dates.copy()
    left["date"] = pd.to_datetime(left["date"]).astype("datetime64[ns]")
    right = prices[["sku_id", "week_start", "sell_price"]].copy()
    right["week_start"] = pd.to_datetime(right["week_start"]).astype("datetime64[ns]")
    result = pd.merge_asof(
        left.sort_values(["date", "sku_id"]),
        right.sort_values(["week_start", "sku_id"]),
        left_on="date",
        right_on="week_start",
        by="sku_id",
        direction="backward",
    )
    # A series may start before its first price record. Backfill only within that SKU.
    result["sell_price"] = result.groupby("sku_id", observed=True)["sell_price"].bfill()
    return result.drop(columns="week_start").sort_values(["sku_id", "date"])


def wrmsse(
    actual: pd.DataFrame,
    predicted: pd.DataFrame,
    train: pd.DataFrame,
    prices: pd.DataFrame,
) -> float:
    """Compute SKU RMSSE weighted by revenue in the training period's final 28 days.

    SKUs with a zero first-difference scale are omitted because their RMSSE is undefined;
    remaining revenue weights are normalized to one. If all revenue is zero, equal weights
    are used among the valid SKU scales.
    """
    observed = actual[["sku_id", "date", "units_sold"]].copy()
    forecast = predicted[["sku_id", "date", "yhat"]].copy()
    scored = observed.merge(forecast, on=["sku_id", "date"], validate="one_to_one")

    ordered_train = train.sort_values(["sku_id", "date"])
    scale = ordered_train.groupby("sku_id", observed=True)["units_sold"].apply(
        lambda values: float(np.mean(np.square(np.diff(values.to_numpy(dtype=float)))))
        if len(values) > 1
        else np.nan
    )
    mse = scored.assign(squared_error=np.square(scored["units_sold"] - scored["yhat"]))
    mse = mse.groupby("sku_id", observed=True)["squared_error"].mean()
    rmsse = np.sqrt(mse / scale)
    rmsse = rmsse[np.isfinite(rmsse) & (scale > 0)]
    if rmsse.empty:
        raise ValueError("WRMSSE is undefined because no SKU has a positive scale")

    train_end = ordered_train["date"].max()
    revenue_window = ordered_train.loc[
        ordered_train["date"] > train_end - pd.Timedelta(days=HORIZON)
    ]
    revenue = attach_prices(revenue_window, prices)
    revenue["revenue"] = revenue["units_sold"] * revenue["sell_price"].fillna(0.0)
    weights = revenue.groupby("sku_id", observed=True)["revenue"].sum().reindex(rmsse.index)
    if float(weights.fillna(0.0).sum()) <= 0:
        weights = pd.Series(1.0, index=rmsse.index)
    weights = weights.fillna(0.0) / weights.fillna(0.0).sum()
    return float((rmsse * weights).sum())


def seasonal_naive_forecast(train: pd.DataFrame, dates: Sequence[pd.Timestamp]) -> pd.DataFrame:
    """Repeat each SKU's final seven observations over the requested horizon."""
    target_dates = pd.DatetimeIndex(dates)
    rows: list[tuple[str, pd.Timestamp, float]] = []
    for sku_id, group in train.sort_values("date").groupby("sku_id", sort=True):
        season = group["units_sold"].tail(7).to_numpy(dtype=float)
        if len(season) != 7:
            raise ValueError(f"{sku_id} has fewer than seven training observations")
        rows.extend(
            (str(sku_id), date, float(season[index % 7]))
            for index, date in enumerate(target_dates)
        )
    return pd.DataFrame(rows, columns=["sku_id", "date", "yhat"])


def ets_forecast(train: pd.DataFrame, dates: Sequence[pd.Timestamp]) -> pd.DataFrame:
    """Fit StatsForecast AutoETS to all SKU series in one deterministic batch."""
    target_dates = pd.DatetimeIndex(dates)
    sf_input = train.rename(columns={"sku_id": "unique_id", "date": "ds", "units_sold": "y"})
    engine = StatsForecast(models=[AutoETS(season_length=7)], freq="D", n_jobs=1)
    result = engine.forecast(df=sf_input[["unique_id", "ds", "y"]], h=len(target_dates))
    result = result.rename(columns={"unique_id": "sku_id", "ds": "date", "AutoETS": "yhat"})
    result["date"] = pd.to_datetime(result["date"])
    return result[["sku_id", "date", "yhat"]].sort_values(["sku_id", "date"])


def _calendar_features(dates: pd.Series, calendar: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame({"date": pd.to_datetime(dates).drop_duplicates()})
    available = calendar[["date", "event_name", "is_weekend"]].copy()
    available["date"] = pd.to_datetime(available["date"])
    frame = frame.merge(available, on="date", how="left")
    frame["day_of_week"] = frame["date"].dt.dayofweek.astype("int8")
    derived_weekend = frame["day_of_week"].isin([5, 6])
    frame["is_weekend"] = np.where(
        frame["is_weekend"].isna(), derived_weekend, frame["is_weekend"]
    ).astype("int8")
    frame["has_event"] = frame["event_name"].notna().astype("int8")
    return frame.drop(columns="event_name")


def make_lightgbm_features(
    train: pd.DataFrame,
    dates: Sequence[pd.Timestamp],
    calendar: pd.DataFrame,
    prices: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build train and forecast features using sales from d-28 or earlier only."""
    history = train[["sku_id", "date", "units_sold"]].copy()
    history["date"] = pd.to_datetime(history["date"])
    target_dates = pd.DatetimeIndex(dates)
    sku_ids = sorted(history["sku_id"].unique())
    future = pd.MultiIndex.from_product(
        [sku_ids, target_dates], names=["sku_id", "date"]
    ).to_frame(index=False)
    future["units_sold"] = np.nan
    panel = pd.concat([history, future], ignore_index=True).sort_values(["sku_id", "date"])
    grouped = panel.groupby("sku_id", observed=True)["units_sold"]
    for lag in (28, 29, 30, 31, 32, 33, 34, 35, 42, 56, 364):
        panel[f"lag_{lag}"] = grouped.shift(lag)
    shifted = grouped.shift(28)
    by_sku = shifted.groupby(panel["sku_id"], observed=True)
    panel["rolling_mean_7"] = by_sku.transform(
        lambda values: values.rolling(7, min_periods=7).mean()
    )
    panel["rolling_mean_28"] = by_sku.transform(
        lambda values: values.rolling(28, min_periods=28).mean()
    )
    panel["rolling_std_28"] = by_sku.transform(
        lambda values: values.rolling(28, min_periods=28).std(ddof=0)
    )
    # Keep the yearly signal on production history while supporting shorter test fixtures.
    panel["lag_364"] = panel["lag_364"].fillna(panel["lag_56"])
    panel = panel.merge(_calendar_features(panel["date"], calendar), on="date", how="left")
    panel = attach_prices(panel, prices)
    panel["sku_id"] = pd.Categorical(panel["sku_id"], categories=sku_ids)
    train_features = panel.loc[panel["units_sold"].notna()].dropna(subset=list(LGB_FEATURES))
    forecast_features = panel.loc[panel["date"].isin(target_dates)].copy()
    if forecast_features[list(LGB_FEATURES)].isna().any().any():
        missing = forecast_features[list(LGB_FEATURES)].columns[
            forecast_features[list(LGB_FEATURES)].isna().any()
        ].tolist()
        raise ValueError(f"Missing LightGBM forecast features: {missing}")
    return train_features, forecast_features


def lightgbm_forecast(
    train: pd.DataFrame,
    dates: Sequence[pd.Timestamp],
    calendar: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """Fit one deterministic global Tweedie model and forecast without recursion."""
    train_features, forecast_features = make_lightgbm_features(train, dates, calendar, prices)
    # Fit recent regimes while retaining older history as leakage-safe lag inputs.
    recent_start = train_features["date"].max() - pd.Timedelta(days=730)
    train_features = train_features.loc[train_features["date"] > recent_start]
    # Mild inverse-sqrt weighting balances relative and revenue-weighted errors; keeping
    # zero-demand rows at 0.05 avoids the previous MAPE-driven exclusion of all zeros.
    demand = train_features["units_sold"].to_numpy(dtype=float)
    sample_weight = np.where(demand > 0, 1.0 / np.sqrt(np.maximum(demand, 1.0)), 0.05)
    dataset = lgb.Dataset(
        train_features[list(LGB_FEATURES)],
        label=train_features["units_sold"],
        weight=sample_weight,
        categorical_feature=["sku_id"],
        free_raw_data=False,
    )
    parameters = {
        "objective": "tweedie",
        "metric": "tweedie",
        "tweedie_variance_power": 1.2,
        "learning_rate": 0.04,
        "num_leaves": 63,
        "min_data_in_leaf": 15,
        "lambda_l1": 0.1,
        "lambda_l2": 0.5,
        "seed": 42,
        "feature_fraction_seed": 42,
        "bagging_seed": 42,
        "data_random_seed": 42,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "verbosity": -1,
    }
    model = lgb.train(
        parameters,
        dataset,
        num_boost_round=800,
        callbacks=[lgb.log_evaluation(period=0)],
    )
    result = forecast_features[["sku_id", "date"]].copy()
    result["sku_id"] = result["sku_id"].astype("string")
    result["yhat"] = model.predict(forecast_features[list(LGB_FEATURES)])
    return result.sort_values(["sku_id", "date"])


def forecast_all_models(
    train: pd.DataFrame,
    dates: Sequence[pd.Timestamp],
    calendar: pd.DataFrame,
    prices: pd.DataFrame,
    models: Sequence[str] = MODEL_NAMES,
) -> pd.DataFrame:
    """Forecast the requested models and return one normalized long-form frame."""
    unknown = set(models) - set(MODEL_NAMES)
    if unknown:
        raise ValueError(f"Unknown models: {sorted(unknown)}")
    functions = {
        "seasonal_naive": lambda: seasonal_naive_forecast(train, dates),
        "ets": lambda: ets_forecast(train, dates),
        "lightgbm": lambda: lightgbm_forecast(train, dates, calendar, prices),
    }
    outputs = []
    for model_name in models:
        forecast = functions[model_name]()
        forecast["model_name"] = model_name
        forecast["yhat"] = forecast["yhat"].clip(lower=0.0)
        outputs.append(forecast)
    return pd.concat(outputs, ignore_index=True)[["sku_id", "date", "model_name", "yhat"]]


def run_backtest(
    sales: pd.DataFrame,
    calendar: pd.DataFrame,
    prices: pd.DataFrame,
    models: Sequence[str] = MODEL_NAMES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run three 28-day expanding-window folds and calculate model metrics."""
    metric_rows: list[dict[str, float | int | str]] = []
    forecasts = []
    for split in rolling_splits(sales):
        dates = pd.date_range(split.cutoff + pd.Timedelta(days=1), periods=HORIZON, freq="D")
        fold_forecasts = forecast_all_models(split.train, dates, calendar, prices, models)
        fold_forecasts["fold"] = split.fold
        forecasts.append(fold_forecasts)
        for model_name in models:
            predicted = fold_forecasts.loc[fold_forecasts["model_name"] == model_name]
            scored = split.test.merge(
                predicted[["sku_id", "date", "yhat"]],
                on=["sku_id", "date"],
                validate="one_to_one",
            )
            metric_rows.append(
                {
                    "model_name": model_name,
                    "fold": split.fold,
                    "mape": mape(scored["units_sold"], scored["yhat"]),
                    "wmape": wmape(scored["units_sold"], scored["yhat"]),
                    "wrmsse": wrmsse(split.test, predicted, split.train, prices),
                }
            )
    metrics = pd.DataFrame(metric_rows).sort_values(["model_name", "fold"])
    return metrics.reset_index(drop=True), pd.concat(forecasts, ignore_index=True)


def predict_future(
    sales: pd.DataFrame,
    calendar: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """Refit all models on full history and predict the next 28 consecutive days."""
    final_date = pd.to_datetime(sales["date"]).max()
    dates = pd.date_range(final_date + pd.Timedelta(days=1), periods=HORIZON, freq="D")
    return forecast_all_models(sales, dates, calendar, prices)


def write_metrics(connection: duckdb.DuckDBPyConnection, metrics: pd.DataFrame) -> None:
    """Replace forecast_metrics atomically within the caller's transaction."""
    connection.execute(
        """
        CREATE OR REPLACE TABLE forecast_metrics (
            model_name VARCHAR,
            fold INTEGER,
            mape DECIMAL(18,3),
            wmape DECIMAL(18,3),
            wrmsse DECIMAL(18,3)
        )
        """
    )
    connection.register("new_forecast_metrics", metrics)
    connection.execute(
        "INSERT INTO forecast_metrics (model_name, fold, mape, wmape, wrmsse) "
        "SELECT model_name, fold, mape, wmape, wrmsse FROM new_forecast_metrics"
    )
    connection.unregister("new_forecast_metrics")


def write_forecasts(connection: duckdb.DuckDBPyConnection, forecasts: pd.DataFrame) -> None:
    """Replace forecast_daily atomically within the caller's transaction."""
    connection.register("new_forecast_daily", forecasts)
    connection.execute("DELETE FROM forecast_daily")
    connection.execute(
        "INSERT INTO forecast_daily "
        "SELECT sku_id, date, model_name, CAST(yhat AS DECIMAL(10, 2)) "
        "FROM new_forecast_daily"
    )
    connection.unregister("new_forecast_daily")


def print_summary(metrics: pd.DataFrame) -> None:
    """Print per-model fold means and LightGBM's relative baseline improvements."""
    if metrics.empty:
        print("No backtest metrics available.")
        return
    summary = metrics.groupby("model_name", as_index=False)[["mape", "wmape", "wrmsse"]].mean()
    summary = summary.sort_values("model_name")
    print("\nBacktest mean metrics (3 folds)")
    print(
        summary.to_string(
            index=False,
            formatters={
                "mape": "{:.4f}".format,
                "wmape": "{:.4f}".format,
                "wrmsse": "{:.4f}".format,
            },
        )
    )
    indexed = summary.set_index("model_name")
    if {"seasonal_naive", "lightgbm"} <= set(indexed.index):
        baseline = float(indexed.loc["seasonal_naive", "mape"])
        improvement = (baseline - float(indexed.loc["lightgbm", "mape"])) / baseline * 100
        print(f"LightGBM relative MAPE improvement vs seasonal_naive: {improvement:.2f}%")
        wmape_baseline = float(indexed.loc["seasonal_naive", "wmape"])
        wmape_improvement = (
            wmape_baseline - float(indexed.loc["lightgbm", "wmape"])
        ) / wmape_baseline * 100
        print(
            "LightGBM relative WMAPE improvement vs seasonal_naive: "
            f"{wmape_improvement:.2f}%"
        )


def main(argv: Sequence[str] | None = None) -> None:
    """Run backtesting, future prediction, or either phase independently."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backtest-only", action="store_true")
    mode.add_argument("--predict-only", action="store_true")
    args = parser.parse_args(argv)

    connection = duckdb.connect(str(database_path()))
    try:
        sales, calendar, prices = load_source_frames(connection)
        metrics = pd.DataFrame()
        connection.execute("BEGIN TRANSACTION")
        try:
            if not args.predict_only:
                metrics, _ = run_backtest(sales, calendar, prices)
                write_metrics(connection, metrics)
            if not args.backtest_only:
                forecasts = predict_future(sales, calendar, prices)
                write_forecasts(connection, forecasts)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        if args.predict_only:
            metrics = connection.execute(
                "SELECT model_name, fold, mape, wmape, wrmsse FROM forecast_metrics"
            ).fetchdf()
    finally:
        connection.close()
    print_summary(metrics)


if __name__ == "__main__":
    main()
