"""Backtester tests on real 400-day BTC/ETH history.

The two properties that make a backtest trustworthy:
  1. Determinism — same inputs, same result, every time.
  2. No lookahead — truncating the future must not change the past.
"""

import pandas as pd
import pytest

from src.trading_bot.backtest.backtester import (
    Backtester,
    BacktestConfig,
    compute_metrics,
    infer_bars_per_year,
    walk_forward_splits,
)
from src.trading_bot.strategies import EnsembleStrategy, MomentumStrategy


@pytest.fixture(scope="module")
def result(btc_df, eth_df):
    bt = Backtester(EnsembleStrategy(), BacktestConfig(initial_cash=10_000.0))
    return bt.run({"BTC/USDT": btc_df, "ETH/USDT": eth_df})


def test_backtest_runs_and_trades(result):
    assert result.metrics["num_trades"] > 0
    assert result.metrics["bars"] == 9600
    assert result.metrics["fees_paid"] > 0
    assert len(result.equity_curve) == 9600


def test_backtest_is_deterministic(btc_df, eth_df, result):
    bt = Backtester(EnsembleStrategy(), BacktestConfig(initial_cash=10_000.0))
    again = bt.run({"BTC/USDT": btc_df, "ETH/USDT": eth_df})
    assert again.metrics == result.metrics
    pd.testing.assert_series_equal(again.equity_curve, result.equity_curve)


def test_no_lookahead(btc_df):
    """Trades in the first 6000 bars must be identical whether the
    backtest data ends at bar 6000 or continues to 9600."""
    cfg = BacktestConfig(initial_cash=10_000.0)
    short = Backtester(MomentumStrategy(), cfg).run({"BTC/USDT": btc_df.iloc[:6000]})
    full = Backtester(MomentumStrategy(), cfg).run({"BTC/USDT": btc_df})
    cutoff = btc_df.index[5999]
    # Ignore forced end-of-backtest liquidations.
    short_trades = [
        t for t in short.trades if t.exit_reason != "end_of_backtest"
    ]
    full_trades = [
        t
        for t in full.trades
        if t.closed_at <= cutoff and t.exit_reason != "end_of_backtest"
    ]
    assert len(short_trades) == len(full_trades)
    for a, b in zip(short_trades, full_trades):
        assert a.opened_at == b.opened_at
        assert a.entry_price == pytest.approx(b.entry_price)
        assert a.pnl == pytest.approx(b.pnl)


def test_equity_curve_consistency(result):
    """Final equity must equal initial cash plus net PnL of all trades."""
    net_pnl = sum(t.pnl for t in result.trades)
    assert result.metrics["final_equity"] == pytest.approx(
        10_000.0 + net_pnl, rel=1e-6
    )


def test_costs_reduce_returns(btc_df):
    free = BacktestConfig(initial_cash=10_000.0, fee_rate=0.0, slippage_bps=0.0)
    costly = BacktestConfig(initial_cash=10_000.0, fee_rate=0.0026, slippage_bps=10.0)
    r_free = Backtester(MomentumStrategy(), free).run({"BTC/USDT": btc_df})
    r_costly = Backtester(MomentumStrategy(), costly).run({"BTC/USDT": btc_df})
    assert r_costly.metrics["final_equity"] < r_free.metrics["final_equity"]
    assert r_free.metrics["fees_paid"] == 0.0


def test_stops_are_enforced(result):
    for trade in result.trades:
        if trade.exit_reason == "stop_loss":
            # A stopped trade can't have a large positive return.
            assert trade.return_pct < 0.02


def test_metrics_sanity(result):
    m = result.metrics
    assert 0.0 <= m["win_rate"] <= 1.0
    assert 0.0 <= m["max_drawdown"] <= 1.0
    assert 0.0 <= m["exposure"] <= 1.0
    assert m["final_equity"] > 0


def test_infer_bars_per_year_hourly(btc_df):
    bpy = infer_bars_per_year(btc_df.index)
    assert bpy == pytest.approx(365.25 * 24, rel=0.01)


def test_walk_forward_splits(btc_df):
    splits = list(walk_forward_splits(btc_df, train_bars=4000, test_bars=1000))
    assert len(splits) == 5
    for train, test in splits:
        assert len(train) == 4000
        assert len(test) == 1000
        assert train.index[-1] < test.index[0]


def test_compute_metrics_empty():
    assert compute_metrics(pd.Series(dtype=float), [], 10_000.0, 0.0, 0) == {}
