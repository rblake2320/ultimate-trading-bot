"""Trend-following momentum strategy.

Entry: fast EMA crosses above slow EMA while ADX confirms a trend and price
is above the long-term EMA filter. Exit: fast EMA crosses back below slow
EMA, or the trend filter fails. Stops are ATR-based.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import ta
from .base import Strategy, validate_ohlcv, void_warmup


class MomentumStrategy(Strategy):
    name = "momentum"

    @classmethod
    def default_params(cls) -> dict:
        return {
            "fast_ema": 12,
            "slow_ema": 26,
            "trend_ema": 100,
            "adx_period": 14,
            "adx_threshold": 20.0,
            "atr_period": 14,
            "atr_stop_mult": 2.5,
            "atr_target_mult": 5.0,
        }

    @property
    def warmup(self) -> int:
        return int(self.params["trend_ema"]) + int(self.params["adx_period"]) + 5

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        validate_ohlcv(df)
        p = self.params
        out = self.empty_frame(df.index)

        close = df["close"]
        fast = ta.ema(close, int(p["fast_ema"]))
        slow = ta.ema(close, int(p["slow_ema"]))
        trend = ta.ema(close, int(p["trend_ema"]))
        adx_df = ta.adx(df["high"], df["low"], close, int(p["adx_period"]))
        atr_s = ta.atr(df["high"], df["low"], close, int(p["atr_period"]))

        cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        trending = adx_df["adx"] >= float(p["adx_threshold"])
        bullish = adx_df["plus_di"] > adx_df["minus_di"]
        above_trend = close > trend

        enter = cross_up & trending & bullish & above_trend
        exit_ = cross_down | (close < trend)

        # Confidence scales with trend strength (ADX 20 -> 0.55, 45+ -> 0.95).
        strength = ((adx_df["adx"] - 20.0) / 25.0).clip(0.0, 1.0)
        confidence = 0.55 + 0.40 * strength

        out.loc[enter, "action"] = 1
        out.loc[exit_ & ~enter, "action"] = -1
        out.loc[enter, "confidence"] = confidence[enter]
        out.loc[exit_ & ~enter, "confidence"] = 0.6
        out.loc[enter, "stop_loss"] = (
            close - float(p["atr_stop_mult"]) * atr_s
        )[enter]
        out.loc[enter, "take_profit"] = (
            close + float(p["atr_target_mult"]) * atr_s
        )[enter]

        return void_warmup(out, self.warmup)
