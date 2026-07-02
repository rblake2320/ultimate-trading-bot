"""Strategy framework tests on real market data.

The critical property is causality (no lookahead): a strategy's decision
on bar i must be identical whether or not future bars exist in the frame.
This is what makes the backtests honest.
"""

import pandas as pd
import pytest

from src.trading_bot.models import Regime, SignalAction
from src.trading_bot.strategies import (
    STRATEGY_REGISTRY,
    BreakoutStrategy,
    EnsembleStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    build_strategy,
    regime_series,
)

ALL_STRATEGIES = [
    MomentumStrategy,
    MeanReversionStrategy,
    BreakoutStrategy,
    EnsembleStrategy,
]


@pytest.mark.parametrize("cls", ALL_STRATEGIES)
def test_signals_frame_contract(cls, btc_df):
    strategy = cls()
    out = strategy.signals(btc_df)
    assert list(out.columns) == ["action", "confidence", "stop_loss", "take_profit"]
    assert len(out) == len(btc_df)
    assert set(out["action"].unique()) <= {-1, 0, 1}
    assert (out["confidence"] >= 0).all() and (out["confidence"] <= 1).all()
    # Warmup region must be all-hold.
    assert (out["action"].iloc[: strategy.warmup] == 0).all()


@pytest.mark.parametrize("cls", ALL_STRATEGIES)
def test_strategies_produce_entries_on_real_data(cls, btc_df):
    """400 days of 1h bars must trigger at least a few entries; a strategy
    that never fires is dead weight."""
    out = cls().signals(btc_df)
    assert (out["action"] == 1).sum() > 0, f"{cls.name} never entered in 400d"
    assert (out["action"] == -1).sum() > 0, f"{cls.name} never exited in 400d"


@pytest.mark.parametrize("cls", ALL_STRATEGIES)
def test_strategies_are_causal(cls, btc_df):
    """Signals over a prefix must equal the same rows of the full run."""
    strategy = cls()
    n = 4000
    on_prefix = strategy.signals(btc_df.iloc[:n])
    on_full = strategy.signals(btc_df).iloc[:n]
    pd.testing.assert_frame_equal(on_prefix, on_full)


def test_entry_rows_carry_stops(btc_df):
    for cls in (MomentumStrategy, MeanReversionStrategy, BreakoutStrategy):
        out = cls().signals(btc_df)
        entries = out[out["action"] == 1]
        assert entries["stop_loss"].notna().all(), cls.name
        # Stops must be below the close they were computed from.
        closes = btc_df["close"].loc[entries.index]
        assert (entries["stop_loss"] < closes).all(), cls.name


def test_evaluate_returns_signal_or_none(btc_df):
    strategy = EnsembleStrategy()
    signal = strategy.evaluate(btc_df, "BTC/USDT")
    assert signal is None or signal.action in (SignalAction.BUY, SignalAction.CLOSE)
    if signal is not None:
        assert signal.symbol == "BTC/USDT"
        assert 0 <= signal.confidence <= 1


def test_regime_series_covers_all_bars(btc_df):
    regimes = regime_series(btc_df)
    assert len(regimes) == len(btc_df)
    seen = set(regimes.unique())
    assert seen <= set(Regime)
    # Real 400-day data must show both trending and ranging periods.
    assert Regime.RANGING in seen
    assert Regime.TRENDING_UP in seen or Regime.TRENDING_DOWN in seen


def test_registry_builds_every_strategy():
    for name in STRATEGY_REGISTRY:
        s = build_strategy(name)
        assert s.name == name
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategy("does_not_exist")


def test_param_override():
    s = build_strategy("momentum", {"fast_ema": 5})
    assert s.params["fast_ema"] == 5
    assert s.params["slow_ema"] == 26  # untouched default
