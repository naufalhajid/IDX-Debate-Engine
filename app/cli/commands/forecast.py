"""CLI commands for the IDX forecasting layer."""
from __future__ import annotations

from datetime import date
from typing import Annotated

import typer

from app.cli.ui.console import console

app = typer.Typer(help="Forecasting model commands.", no_args_is_help=True)


def _get_service():
    from core.forecasting import ForecastingService
    return ForecastingService()


def build_dataset_command(
    tickers: Annotated[
        str,
        typer.Option("--tickers", help="Comma-separated IDX tickers, e.g. BBCA,BBRI"),
    ] = "BBCA,BBRI,TLKM",
    start: Annotated[
        str,
        typer.Option("--start", help="Start date (YYYY-MM-DD)"),
    ] = "2023-01-01",
    horizon: Annotated[
        int,
        typer.Option("--horizon", help="Forward horizon in trading days"),
    ] = 10,
) -> None:
    """Build feature dataset and print summary statistics."""
    from core.forecasting.dataset import DatasetBuilder

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    start_date = date.fromisoformat(start)
    end_date = date.today()

    console.print(f"[idx.header]Building dataset[/idx.header] tickers={ticker_list} start={start_date} horizon={horizon}")
    with console.status("[idx.muted]Downloading OHLCV from yfinance...[/idx.muted]"):
        df = DatasetBuilder().build(ticker_list, start_date, end_date, horizons=(horizon,))

    if df.empty:
        console.print("[idx.warn]No data returned — check tickers and network connection.[/idx.warn]")
        raise typer.Exit(1)

    console.print(f"[idx.ok]Dataset built:[/idx.ok] {len(df)} rows × {len(df.columns)} cols")
    console.print(f"[idx.muted]Tickers: {df.index.get_level_values('ticker').unique().tolist()}[/idx.muted]")
    console.print(f"[idx.muted]NaN rates: {df.isna().mean().round(3).to_dict()}[/idx.muted]")


def validate_command(
    tickers: Annotated[
        str,
        typer.Option("--tickers", help="Comma-separated IDX tickers"),
    ] = "BBCA",
    horizon: Annotated[
        int,
        typer.Option("--horizon", help="Forward horizon in trading days"),
    ] = 10,
    walk_forward: Annotated[
        bool,
        typer.Option("--walk-forward/--no-walk-forward", help="Run walk-forward validation"),
    ] = True,
) -> None:
    """Walk-forward validation report for the naive baseline model."""
    from core.forecasting.dataset import DatasetBuilder
    from core.forecasting.labels import build_labels
    from core.forecasting.models.naive import NaiveModel
    from core.forecasting.validation import validate_model, walk_forward_splits

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    end = date.today()
    start = date(end.year - 2, end.month, end.day)

    with console.status("[idx.muted]Fetching data...[/idx.muted]"):
        df = DatasetBuilder().build(ticker_list, start, end, horizons=(horizon,))

    if df.empty or len(df) < 60:
        console.print("[idx.warn]Insufficient data for validation.[/idx.warn]")
        raise typer.Exit(1)

    try:
        labeled = build_labels(df.reset_index(level="ticker", drop=True), horizon).dropna(subset=["r_net_h"])
    except Exception as e:
        console.print(f"[idx.warn]Label build failed: {e}[/idx.warn]")
        raise typer.Exit(1)

    splits = walk_forward_splits(labeled, n_splits=5, test_size_days=60) if walk_forward else []
    if not splits:
        console.print("[idx.warn]Not enough data for walk-forward splits.[/idx.warn]")
        raise typer.Exit(1)

    result = validate_model(NaiveModel(), splits, horizon)
    console.print(f"[idx.header]Validation result[/idx.header] horizon={horizon}d")
    console.print(f"  status: [bold]{result.status}[/bold]")
    console.print(f"  IC mean: {result.ic_mean}")
    console.print(f"  IC t-stat: {result.ic_t_stat}")
    console.print(f"  Brier: {result.brier}")
    console.print(f"  RMSE: {result.rmse}")
    console.print(f"  DSR: {result.dsr}")
    console.print(f"  BH q-value passed: {result.bh_q_value_passed}")
    console.print(f"  n_observations: {result.n_observations}")


def predict_command(
    ticker: Annotated[
        str,
        typer.Argument(help="IDX ticker symbol"),
    ],
    horizon: Annotated[
        int,
        typer.Option("--horizon", help="Forward horizon in trading days"),
    ] = 10,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Forecast mode: ensemble | tgarch | naive"),
    ] = "ensemble",
) -> None:
    """Generate a ForecastReport for one ticker."""
    service = _get_service()

    with console.status(f"[idx.muted]Forecasting {ticker}...[/idx.muted]"):
        report = service.predict(
            ticker.upper(),
            as_of=date.today(),
            horizons=(horizon,),
            mode=mode,  # type: ignore[arg-type]
        )

    console.print(f"[idx.header]Forecast: {report.ticker}[/idx.header]  as_of={report.as_of}  h={report.horizon_days}d")
    console.print(f"  decision: [bold]{report.decision}[/bold]  confidence: {report.confidence}")
    console.print(f"  EV: {report.expected_value}  p_target: {report.p_target}  p_stop: {report.p_stop}")
    console.print(f"  sigma forecast (annualized): {report.volatility_forecast}")
    console.print(f"  r_hat_net: {report.expected_return_net}")
    if report.data_quality_flags:
        console.print(f"  [idx.warn]flags:[/idx.warn] {report.data_quality_flags}")
    if report.validation_summary:
        vs = report.validation_summary
        console.print(f"  validation: status={vs.status} IC={vs.ic_mean} n={vs.n_observations}")


app.command(name="build-dataset")(build_dataset_command)
app.command(name="validate")(validate_command)
app.command(name="predict")(predict_command)


__all__ = ["app", "build_dataset_command", "predict_command", "validate_command"]
