"""Technical indicators implemented with pandas/numpy only.

Every function is causal: the value at row *i* uses data from rows <= i,
so these are safe for both live signal generation and backtesting.
Inputs are pandas Series/DataFrames with a monotonically increasing index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # When avg_loss == 0 (straight rally) RSI is 100 by definition.
    out = out.where(avg_loss != 0.0, 100.0)
    out[avg_gain.isna() | avg_loss.isna()] = np.nan
    return out


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Wilder-smoothed Average True Range."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def bollinger_bands(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> pd.DataFrame:
    mid = sma(series, period)
    std = series.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    pct_b = (series - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"middle": mid, "upper": upper, "lower": lower, "width": width, "pct_b": pct_b}
    )


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    """Average Directional Index with +DI / -DI (Wilder smoothing)."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )
    tr = true_range(high, low, close)
    alpha = 1.0 / period
    atr_s = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(
        alpha=alpha, adjust=False, min_periods=period
    ).mean() / atr_s.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(
        alpha=alpha, adjust=False, min_periods=period
    ).mean() / atr_s.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_s = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    return pd.DataFrame({"adx": adx_s, "plus_di": plus_di, "minus_di": minus_di})


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> pd.DataFrame:
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    k = 100.0 * (close - lowest) / (highest - lowest).replace(0.0, np.nan)
    d = k.rolling(d_period, min_periods=d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


def donchian_channels(
    high: pd.Series, low: pd.Series, period: int = 20
) -> pd.DataFrame:
    """Donchian channel of the *previous* `period` bars (shifted by one so a
    breakout of the channel is detectable on the current bar without
    self-reference)."""
    upper = high.rolling(period, min_periods=period).max().shift(1)
    lower = low.rolling(period, min_periods=period).min().shift(1)
    middle = (upper + lower) / 2.0
    return pd.DataFrame({"upper": upper, "lower": lower, "middle": middle})


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def vwap(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
) -> pd.Series:
    """Cumulative VWAP over the whole series (use on session-sliced data)."""
    typical = (high + low + close) / 3.0
    cum_vol = volume.cumsum().replace(0.0, np.nan)
    return (typical * volume).cumsum() / cum_vol


def zscore(series: pd.Series, period: int = 20) -> pd.Series:
    mean = series.rolling(period, min_periods=period).mean()
    std = series.rolling(period, min_periods=period).std(ddof=0)
    return (series - mean) / std.replace(0.0, np.nan)


def returns(series: pd.Series, periods: int = 1) -> pd.Series:
    return series.pct_change(periods)


def realized_volatility(
    series: pd.Series, period: int = 20, annualization: float = 1.0
) -> pd.Series:
    """Rolling standard deviation of simple returns.

    `annualization` is the square root of periods-per-year for the bar
    timeframe (e.g. sqrt(365*24) for 1h crypto bars) if annualized vol is
    wanted; leave at 1.0 for per-bar vol.
    """
    return returns(series).rolling(period, min_periods=period).std(ddof=0) * annualization
