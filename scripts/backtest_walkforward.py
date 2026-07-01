"""Walk-forward backtest automation — outputs metrics CSV per ticker per horizon.

Usage:
    uv run python scripts/backtest_walkforward.py
    uv run python scripts/backtest_walkforward.py --tickers BBCA.JK BBRI.JK --horizons 5 10

NOTE: Fundamental feature coverage is ~42 trading days (Apr-Jul 2026). Signal
quality in this backtest primarily reflects technical + regime features, not
pure fundamental. Interpret fundamental-attribution claims with caution.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np  # noqa: E402

from core.forecasting.dataset import DatasetBuilder  # noqa: E402
from core.forecasting.labels import build_labels  # noqa: E402
from core.forecasting.models.xgboost_model import XGBoostForecaster  # noqa: E402
from core.forecasting.validation import validate_model, walk_forward_splits  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_TICKERS: list[str] = [
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "TLKM.JK", "ASII.JK",
    "ICBP.JK", "UNVR.JK", "ADRO.JK", "INCO.JK", "SMGR.JK",
]
_HORIZONS: tuple[int, ...] = (5, 10, 20)
_HISTORY_DAYS: int = 756
_N_SPLITS: int = 5
_TEST_SIZE_DAYS: int = 60

_CSV_FIELDS = [
    "ticker", "horizon_d", "n_obs", "ic_mean", "ic_t_stat",
    "dir_acc", "win_rate_long", "dsr", "rmse", "mae", "status",
]


def _fmt(v: float | None, decimals: int = 4) -> str:
    return f"{v:.{decimals}f}" if v is not None else ""


def _date_ago(days: int) -> datetime.date:
    return (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).date()


def _compute_win_rate_long(
    splits: list[tuple],
    target_col: str = "r_net_h",
) -> float | None:
    """% of long signals (y_pred > 0) where the actual return (y_true) is also > 0."""
    feature_cols = [
        c for c in (splits[0][0].columns if splits else [])
        if not c.startswith(("y_", "r_net", "sigma"))
    ]
    total_wins = 0
    total_signals = 0
    for train_df, test_df in splits:
        if target_col not in train_df.columns or target_col not in test_df.columns:
            continue
        X_train = train_df[feature_cols].select_dtypes(include=[np.number]).fillna(0)
        y_train = train_df[target_col].fillna(0)
        X_test = test_df[feature_cols].select_dtypes(include=[np.number]).fillna(0)
        y_test = test_df[target_col].fillna(0)
        X_train, X_test = X_train.align(X_test, join="left", axis=1, fill_value=0)
        model = XGBoostForecaster()
        try:
            model.fit(X_train, y_train)
            y_pred = np.asarray(model.predict(X_test), dtype=float)
        except Exception:
            continue
        y_true = np.asarray(y_test.values, dtype=float)
        long_mask = np.isfinite(y_pred) & np.isfinite(y_true) & (y_pred > 0)
        n_signals = int(long_mask.sum())
        if n_signals == 0:
            continue
        total_wins += int((y_true[long_mask] > 0).sum())
        total_signals += n_signals
    return total_wins / total_signals if total_signals > 0 else None


def run_backtest(
    tickers: list[str] | None = None,
    horizons: tuple[int, ...] = _HORIZONS,
) -> list[dict]:
    tickers = tickers or _DEFAULT_TICKERS
    end = datetime.date.today()
    start = _date_ago(_HISTORY_DAYS)

    logger.info("Building dataset for %d ticker(s), %s → %s", len(tickers), start, end)
    builder = DatasetBuilder()
    df_all = builder.build(tickers=tickers, start=start, end=end, horizons=horizons)

    if df_all is None or df_all.empty:
        logger.error("Dataset build returned empty — aborting.")
        return []

    rows: list[dict] = []

    for ticker in tickers:
        try:
            df_ticker = (
                df_all.xs(ticker, level=0)
                if hasattr(df_all.index, "levels")
                else df_all
            )
        except KeyError:
            logger.warning("[%s] Not found in dataset — skipping.", ticker)
            continue

        if df_ticker.empty:
            logger.warning("[%s] Empty slice — skipping.", ticker)
            continue

        for h in horizons:
            # build_labels adds 'r_net_h' column for this specific horizon
            try:
                labeled = build_labels(df_ticker.copy(), h)
                labeled = labeled.dropna(subset=["r_net_h"])
            except Exception as exc:
                logger.warning("[%s] h=%d  build_labels failed: %s", ticker, h, exc)
                continue

            if len(labeled) < _N_SPLITS * (_TEST_SIZE_DAYS + 20) + 1:
                logger.warning("[%s] h=%d  too few rows (%d) for walk-forward.", ticker, h, len(labeled))
                continue

            splits = walk_forward_splits(labeled, n_splits=_N_SPLITS, test_size_days=_TEST_SIZE_DAYS)
            if not splits:
                logger.warning("[%s] h=%d  walk_forward_splits returned empty.", ticker, h)
                continue

            model = XGBoostForecaster()
            try:
                vs = validate_model(model, splits, horizon=h, target_col="r_net_h")
            except Exception as exc:
                logger.warning("[%s] h=%d  validate_model failed: %s", ticker, h, exc)
                continue

            wrl = _compute_win_rate_long(splits, target_col="r_net_h")

            rows.append({
                "ticker": ticker,
                "horizon_d": h,
                "n_obs": vs.n_observations,
                "ic_mean": _fmt(vs.ic_mean),
                "ic_t_stat": _fmt(vs.ic_t_stat),
                "dir_acc": _fmt(vs.directional_accuracy),
                "win_rate_long": _fmt(wrl),
                "dsr": _fmt(vs.dsr),
                "rmse": _fmt(vs.rmse),
                "mae": _fmt(vs.mae),
                "status": vs.status,
            })
            logger.info(
                "[%s] h=%2dd  IC=%.3f  t=%.2f  dir=%.2f  wrl=%.2f  status=%s",
                ticker, h,
                vs.ic_mean or 0.0,
                vs.ic_t_stat or 0.0,
                vs.directional_accuracy or 0.0,
                wrl or 0.0,
                vs.status,
            )

    return rows


def _write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as f:
        f.write(
            "# CAVEAT: fundamental coverage ~42 trading days (Apr-Jul 2026); "
            "signal primarily reflects technical+regime features.\n"
        )
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tickers", nargs="*", metavar="TICK", help="Tickers to backtest (default: 10 LQ45 stocks)")
    parser.add_argument("--horizons", nargs="*", type=int, default=list(_HORIZONS), metavar="D", help="Forecast horizons in days")
    parser.add_argument("--output", metavar="PATH", help="Override output CSV path")
    args = parser.parse_args()

    rows = run_backtest(
        tickers=args.tickers or None,
        horizons=tuple(args.horizons),
    )

    if not rows:
        logger.error("No results produced.")
        sys.exit(1)

    today = datetime.date.today().strftime("%Y%m%d")
    output_dir = _PROJECT_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = Path(args.output) if args.output else output_dir / f"backtest_walkforward_{today}.csv"

    _write_csv(rows, out_path)
    logger.info("Results: %d rows → %s", len(rows), out_path)

    print(f"\n{'TICKER':<12} {'H':>3} {'N_OBS':>6} {'IC':>7} {'T-STAT':>7} {'DIR':>6} {'WRL':>6} {'STATUS'}")
    print("-" * 68)
    for r in rows:
        print(
            f"{r['ticker']:<12} {r['horizon_d']:>3} {r['n_obs']:>6} "
            f"{r['ic_mean']:>7} {r['ic_t_stat']:>7} {r['dir_acc']:>6} "
            f"{r['win_rate_long']:>6} {r['status']}"
        )


if __name__ == "__main__":
    main()
