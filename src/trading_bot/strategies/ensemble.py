"""Regime-aware ensemble of strategies.

Each bar is classified into a market regime (vectorized, causal). Every
sub-strategy's vote is weighted by how well suited it is to the current
regime, and entries fire only when the combined weighted confidence clears a
threshold. In downtrends and volatility spikes new entries are suppressed
entirely (this is a long-only spot bot) while exit votes still pass through.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..indicators import ta
from ..models import Regime
from .base import Strategy, validate_ohlcv, void_warmup
from .breakout import BreakoutStrategy
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy

logger = logging.getLogger(__name__)

DEFAULT_REGIME_WEIGHTS: Dict[Regime, Dict[str, float]] = {
    Regime.TRENDING_UP: {"momentum": 1.0, "breakout": 0.8, "mean_reversion": 0.2},
    Regime.RANGING: {"momentum": 0.2, "breakout": 0.4, "mean_reversion": 1.0},
    Regime.TRENDING_DOWN: {"momentum": 0.0, "breakout": 0.0, "mean_reversion": 0.0},
    Regime.HIGH_VOLATILITY: {"momentum": 0.0, "breakout": 0.0, "mean_reversion": 0.0},
}


def regime_series(df: pd.DataFrame, adx_period: int = 14) -> pd.Series:
    """Causal per-bar regime classification."""
    adx_df = ta.adx(df["high"], df["low"], df["close"], adx_period)
    vol = ta.realized_volatility(df["close"], period=20)
    vol_threshold = vol.rolling(200, min_periods=50).quantile(0.95)

    regimes = pd.Series(Regime.RANGING, index=df.index, dtype=object)
    trending = adx_df["adx"] >= 25.0
    up = adx_df["plus_di"] >= adx_df["minus_di"]
    regimes[trending & up] = Regime.TRENDING_UP
    regimes[trending & ~up] = Regime.TRENDING_DOWN
    high_vol = vol > vol_threshold
    regimes[high_vol.fillna(False)] = Regime.HIGH_VOLATILITY
    return regimes


class EnsembleStrategy(Strategy):
    name = "ensemble"

    def __init__(
        self,
        strategies: Optional[Sequence[Strategy]] = None,
        params: Optional[dict] = None,
        regime_weights: Optional[Dict[Regime, Dict[str, float]]] = None,
    ):
        super().__init__(params)
        self.strategies: List[Strategy] = list(strategies) if strategies else [
            MomentumStrategy(),
            MeanReversionStrategy(),
            BreakoutStrategy(),
        ]
        self.regime_weights = regime_weights or DEFAULT_REGIME_WEIGHTS

    @classmethod
    def default_params(cls) -> dict:
        return {
            "enter_threshold": 0.55,  # weighted confidence needed to open
            "exit_threshold": 0.30,  # weighted confidence needed to close
            "adx_period": 14,
        }

    @property
    def warmup(self) -> int:
        return max(s.warmup for s in self.strategies) + 5

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        validate_ohlcv(df)
        p = self.params
        out = self.empty_frame(df.index)

        regimes = regime_series(df, int(p["adx_period"]))
        sub_frames = {s.name: s.signals(df) for s in self.strategies}

        # Per-bar weights for each strategy, derived from the regime.
        weights = {
            name: regimes.map(
                lambda r, _n=name: self.regime_weights.get(r, {}).get(_n, 0.0)
            ).astype(float)
            for name in sub_frames
        }

        buy_score = pd.Series(0.0, index=df.index)
        sell_score = pd.Series(0.0, index=df.index)
        for name, frame in sub_frames.items():
            w = weights[name]
            buy_score += w * frame["confidence"].where(frame["action"] == 1, 0.0)
            # Exit votes always carry full weight: refusing to exit because
            # the regime changed is how positions turn into bag-holding.
            sell_score += frame["confidence"].where(frame["action"] == -1, 0.0)

        enter = buy_score >= float(p["enter_threshold"])
        exit_ = (sell_score >= float(p["exit_threshold"])) & ~enter

        out.loc[enter, "action"] = 1
        out.loc[exit_, "action"] = -1
        out.loc[enter, "confidence"] = buy_score[enter].clip(0.0, 1.0)
        out.loc[exit_, "confidence"] = sell_score[exit_].clip(0.0, 1.0)

        # Stops/targets: take them from the strongest weighted entering
        # strategy on each entry bar.
        entry_rows = np.flatnonzero(enter.to_numpy())
        for i in entry_rows:
            best_name, best_score = None, -1.0
            for name, frame in sub_frames.items():
                if frame["action"].iloc[i] == 1:
                    score = weights[name].iloc[i] * frame["confidence"].iloc[i]
                    if score > best_score:
                        best_name, best_score = name, score
            if best_name is not None:
                out.iloc[i, out.columns.get_loc("stop_loss")] = sub_frames[
                    best_name
                ]["stop_loss"].iloc[i]
                out.iloc[i, out.columns.get_loc("take_profit")] = sub_frames[
                    best_name
                ]["take_profit"].iloc[i]

        return void_warmup(out, self.warmup)

    def current_regime(self, df: pd.DataFrame) -> Regime:
        return regime_series(df, int(self.params["adx_period"])).iloc[-1]
