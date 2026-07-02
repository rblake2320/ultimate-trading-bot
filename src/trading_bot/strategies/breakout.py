"""Donchian-channel breakout strategy with volume confirmation.

Entry: close breaks above the prior N-bar high on above-average volume.
Exit: close falls below the prior M-bar low (classic turtle-style trailing
channel exit). Initial stop is ATR-based.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import ta
from .base import Strategy, validate_ohlcv, void_warmup


class BreakoutStrategy(Strategy):
    name = "breakout"

    @classmethod
    def default_params(cls) -> dict:
        return {
            "entry_period": 20,
            "exit_period": 10,
            "volume_period": 20,
            "volume_mult": 1.5,
            "atr_period": 14,
            "atr_stop_mult": 2.0,
        }

    @property
    def warmup(self) -> int:
        return (
            max(
                int(self.params["entry_period"]),
                int(self.params["volume_period"]),
                int(self.params["atr_period"]),
            )
            + 5
        )

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        validate_ohlcv(df)
        p = self.params
        out = self.empty_frame(df.index)

        close = df["close"]
        entry_ch = ta.donchian_channels(df["high"], df["low"], int(p["entry_period"]))
        exit_ch = ta.donchian_channels(df["high"], df["low"], int(p["exit_period"]))
        vol_avg = ta.sma(df["volume"], int(p["volume_period"]))
        atr_s = ta.atr(df["high"], df["low"], close, int(p["atr_period"]))

        volume_ok = df["volume"] > float(p["volume_mult"]) * vol_avg
        enter = (close > entry_ch["upper"]) & volume_ok
        exit_ = close < exit_ch["lower"]

        # Confidence scales with breakout strength in ATR units.
        strength = ((close - entry_ch["upper"]) / atr_s).clip(0.0, 2.0) / 2.0
        confidence = 0.55 + 0.35 * strength

        out.loc[enter, "action"] = 1
        out.loc[exit_ & ~enter, "action"] = -1
        out.loc[enter, "confidence"] = confidence[enter]
        out.loc[exit_ & ~enter, "confidence"] = 0.6
        out.loc[enter, "stop_loss"] = (
            close - float(p["atr_stop_mult"]) * atr_s
        )[enter]
        # No fixed take-profit: breakouts ride until the channel exit.

        return void_warmup(out, self.warmup)
