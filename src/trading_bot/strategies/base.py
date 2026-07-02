"""Strategy plugin framework.

A strategy implements one vectorized method, ``signals(df)``, which returns a
frame of causal per-bar decisions. The backtester consumes that frame
directly; live trading uses :meth:`Strategy.evaluate`, which runs the same
code on the latest window and converts the final row into a
:class:`~trading_bot.models.Signal`. One code path, no live/backtest drift.

``df`` is an OHLCV DataFrame with columns open/high/low/close/volume and a
UTC DatetimeIndex of *closed* candles, oldest first.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np
import pandas as pd

from ..models import Regime, Signal, SignalAction

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")

# Column contract for Strategy.signals() output.
SIGNAL_COLUMNS = ("action", "confidence", "stop_loss", "take_profit")


class Strategy(ABC):
    """Base class for all trading strategies."""

    #: Unique short name, used in logs, signals, and config.
    name: str = "base"

    def __init__(self, params: Optional[dict] = None):
        self.params = dict(self.default_params())
        if params:
            self.params.update(params)

    @classmethod
    def default_params(cls) -> dict:
        return {}

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Minimum number of candles required before signals are valid."""

    @abstractmethod
    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized, causal decisions for every bar of ``df``.

        Returns a DataFrame aligned to ``df.index`` with columns:
          - action: 1 (enter long), -1 (exit long), 0 (hold)
          - confidence: float 0..1
          - stop_loss / take_profit: absolute price levels or NaN

        Values at row *i* may only depend on rows <= i.
        """

    def evaluate(self, df: pd.DataFrame, symbol: str) -> Optional[Signal]:
        """Produce a Signal for the most recent closed candle, or None."""
        validate_ohlcv(df)
        if len(df) < self.warmup:
            return None
        out = self.signals(df)
        last = out.iloc[-1]
        action = int(last["action"])
        if action == 0:
            return None
        price = float(df["close"].iloc[-1])
        sl = last.get("stop_loss")
        tp = last.get("take_profit")
        return Signal(
            symbol=symbol,
            action=SignalAction.BUY if action > 0 else SignalAction.CLOSE,
            confidence=float(last["confidence"]),
            price=price,
            strategy=self.name,
            stop_loss=None if pd.isna(sl) else float(sl),
            take_profit=None if pd.isna(tp) else float(tp),
            reason=f"{self.name} bar={df.index[-1].isoformat()}",
        )

    def empty_frame(self, index: pd.Index) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "action": np.zeros(len(index), dtype=int),
                "confidence": np.zeros(len(index)),
                "stop_loss": np.full(len(index), np.nan),
                "take_profit": np.full(len(index), np.nan),
            },
            index=index,
        )


def void_warmup(out: pd.DataFrame, warmup: int) -> pd.DataFrame:
    """Zero out decisions inside the indicator warmup window."""
    n = min(warmup, len(out))
    if n > 0:
        out.iloc[:n, out.columns.get_loc("action")] = 0
        out.iloc[:n, out.columns.get_loc("confidence")] = 0.0
        out.iloc[:n, out.columns.get_loc("stop_loss")] = np.nan
        out.iloc[:n, out.columns.get_loc("take_profit")] = np.nan
    out["action"] = out["action"].astype(int)
    return out


def validate_ohlcv(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    if len(df) > 1 and not df.index.is_monotonic_increasing:
        raise ValueError("OHLCV index must be sorted ascending")


def infer_regime(df: pd.DataFrame, adx_period: int = 14) -> Regime:
    """Classify current market regime from the tail of an OHLCV frame."""
    from ..indicators import ta

    if len(df) < adx_period * 3:
        return Regime.RANGING
    a = ta.adx(df["high"], df["low"], df["close"], adx_period)
    vol = ta.realized_volatility(df["close"], period=20)
    last_adx = a["adx"].iloc[-1]
    last_vol = vol.iloc[-1]
    vol_ref = vol.dropna()
    # Extreme volatility overrides directional classification.
    if len(vol_ref) >= 50 and last_vol > vol_ref.quantile(0.95):
        return Regime.HIGH_VOLATILITY
    if pd.notna(last_adx) and last_adx >= 25:
        if a["plus_di"].iloc[-1] >= a["minus_di"].iloc[-1]:
            return Regime.TRENDING_UP
        return Regime.TRENDING_DOWN
    return Regime.RANGING
