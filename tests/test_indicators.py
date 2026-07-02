"""Indicator correctness on real market data.

Checks are mathematical invariants and hand-computable references, so they
hold for any real dataset.
"""

import numpy as np
import pandas as pd
import pytest

from src.trading_bot.indicators import ta


def test_sma_matches_hand_computation():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ta.sma(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_ema_converges_to_constant():
    s = pd.Series([100.0] * 50)
    out = ta.ema(s, 10)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_bounds_and_extremes(btc_df):
    rsi = ta.rsi(btc_df["close"], 14).dropna()
    assert len(rsi) > 9000
    assert (rsi >= 0).all() and (rsi <= 100).all()
    rising = pd.Series(np.linspace(1, 200, 60))
    assert ta.rsi(rising, 14).iloc[-1] == pytest.approx(100.0)
    falling = pd.Series(np.linspace(200, 1, 60))
    assert ta.rsi(falling, 14).iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_atr_positive_and_scales_with_range(btc_df):
    atr = ta.atr(btc_df["high"], btc_df["low"], btc_df["close"], 14).dropna()
    assert (atr > 0).all()
    # ATR can't exceed the largest true range in its history.
    tr = ta.true_range(btc_df["high"], btc_df["low"], btc_df["close"])
    assert atr.max() <= tr.max()


def test_bollinger_bands_contain_most_prices(btc_df):
    bb = ta.bollinger_bands(btc_df["close"], 20, 2.0)
    valid = bb.dropna()
    close = btc_df["close"].loc[valid.index]
    inside = ((close >= valid["lower"]) & (close <= valid["upper"])).mean()
    # 2-sigma bands should contain the large majority of closes.
    assert inside > 0.85
    assert (valid["upper"] >= valid["lower"]).all()


def test_adx_bounds(btc_df):
    adx = ta.adx(btc_df["high"], btc_df["low"], btc_df["close"], 14).dropna()
    for col in ("adx", "plus_di", "minus_di"):
        assert (adx[col] >= 0).all(), col
        assert (adx[col] <= 100).all(), col


def test_donchian_is_shifted_so_breakouts_are_detectable(btc_df):
    ch = ta.donchian_channels(btc_df["high"], btc_df["low"], 20).dropna()
    highs = btc_df["high"].loc[ch.index]
    # Because the channel is built from the PREVIOUS 20 bars, the current
    # high can exceed the upper band (a breakout). With self-referencing
    # (unshifted) channels this would be impossible.
    assert (highs > ch["upper"]).any()


def test_indicators_are_causal(btc_df):
    """Appending future data must not change past indicator values."""
    full = btc_df
    cut = full.iloc[:5000]
    for fn in (
        lambda df: ta.ema(df["close"], 20),
        lambda df: ta.rsi(df["close"], 14),
        lambda df: ta.atr(df["high"], df["low"], df["close"], 14),
        lambda df: ta.adx(df["high"], df["low"], df["close"], 14)["adx"],
        lambda df: ta.bollinger_bands(df["close"], 20)["upper"],
    ):
        on_cut = fn(cut)
        on_full = fn(full).iloc[:5000]
        pd.testing.assert_series_equal(on_cut, on_full, check_names=False)
