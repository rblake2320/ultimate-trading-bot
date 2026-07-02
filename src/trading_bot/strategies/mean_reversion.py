"""Mean-reversion strategy for ranging markets.

Entry: price closes below the lower Bollinger band with RSI oversold, while
ADX says the market is NOT trending (reversion in a strong downtrend is
knife-catching). Exit: price reverts to the middle band, or RSI overbought.
Stops are ATR-based and tighter than the momentum strategy's.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import ta
from .base import Strategy, validate_ohlcv, void_warmup


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    @classmethod
    def default_params(cls) -> dict:
        return {
            "bb_period": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 30.0,
            "rsi_overbought": 70.0,
            "adx_period": 14,
            "adx_max": 25.0,  # only revert when the market is not trending
            "atr_period": 14,
            "atr_stop_mult": 1.5,
        }

    @property
    def warmup(self) -> int:
        return (
            max(
                int(self.params["bb_period"]),
                int(self.params["rsi_period"]),
                int(self.params["adx_period"]) * 2,
            )
            + 5
        )

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        validate_ohlcv(df)
        p = self.params
        out = self.empty_frame(df.index)

        close = df["close"]
        bb = ta.bollinger_bands(close, int(p["bb_period"]), float(p["bb_std"]))
        rsi_s = ta.rsi(close, int(p["rsi_period"]))
        adx_df = ta.adx(df["high"], df["low"], close, int(p["adx_period"]))
        atr_s = ta.atr(df["high"], df["low"], close, int(p["atr_period"]))

        not_trending = adx_df["adx"] < float(p["adx_max"])
        oversold = (close < bb["lower"]) & (rsi_s < float(p["rsi_oversold"]))
        enter = oversold & not_trending

        reverted = close >= bb["middle"]
        overbought = rsi_s > float(p["rsi_overbought"])
        exit_ = reverted | overbought

        # Deeper oversold -> higher confidence (RSI 30 -> 0.55, RSI 15 -> 0.85).
        depth = ((float(p["rsi_oversold"]) - rsi_s) / 15.0).clip(0.0, 1.0)
        confidence = 0.55 + 0.30 * depth

        out.loc[enter, "action"] = 1
        out.loc[exit_ & ~enter, "action"] = -1
        out.loc[enter, "confidence"] = confidence[enter]
        out.loc[exit_ & ~enter, "confidence"] = 0.6
        out.loc[enter, "stop_loss"] = (
            close - float(p["atr_stop_mult"]) * atr_s
        )[enter]
        # Target the middle band rather than a fixed multiple.
        out.loc[enter, "take_profit"] = bb["middle"][enter]

        return void_warmup(out, self.warmup)
