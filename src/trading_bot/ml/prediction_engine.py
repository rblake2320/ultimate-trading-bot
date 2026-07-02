"""Prediction engine: turns market data into trade signals.

This is the seam between data and decisions. It runs the configured
strategy (usually the regime-aware ensemble), annotates signals with the
current regime, and — when enabled — passes entries through the LLM
analyst veto. There is deliberately no pretend-ML here: signals come from
transparent, testable strategy logic. A LightGBM meta-filter can be added
behind this same interface once there is enough journaled trade history to
train on honestly.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from ..ai.llm_analyst import LLMAnalyst
from ..indicators import ta
from ..models import Signal, SignalAction
from ..strategies import build_strategy
from ..strategies.base import Strategy, infer_regime

logger = logging.getLogger(__name__)


class PredictionEngine:
    def __init__(self, config: Optional[dict] = None):
        cfg = dict(config or {})
        strategy_name = cfg.get("strategy", "ensemble")
        strategy_params = cfg.get("strategy_params") or {}
        self.strategy: Strategy = build_strategy(strategy_name, strategy_params)
        self.analyst = LLMAnalyst(cfg.get("llm_analyst"))
        self.min_confidence = float(cfg.get("min_confidence", 0.55))

    @property
    def warmup(self) -> int:
        return self.strategy.warmup

    async def get_signals(
        self, candles: Dict[str, pd.DataFrame]
    ) -> List[Signal]:
        """Evaluate every symbol's latest closed candle."""
        signals: List[Signal] = []
        for symbol, df in candles.items():
            if len(df) < self.strategy.warmup:
                logger.debug(
                    "%s: %d bars < warmup %d — skipping",
                    symbol,
                    len(df),
                    self.strategy.warmup,
                )
                continue
            try:
                signal = self.strategy.evaluate(df, symbol)
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not kill the loop
                logger.error("Strategy error on %s: %s", symbol, exc)
                continue
            if signal is None:
                continue
            signal.regime = infer_regime(df)

            if signal.action == SignalAction.BUY:
                if signal.confidence < self.min_confidence:
                    logger.debug(
                        "%s entry below min confidence (%.2f < %.2f)",
                        symbol,
                        signal.confidence,
                        self.min_confidence,
                    )
                    continue
                verdict = await self.analyst.review(signal, df)
                if verdict.is_veto:
                    logger.info(
                        "LLM analyst vetoed %s entry: %s",
                        symbol,
                        verdict.reasoning,
                    )
                    continue
            signals.append(signal)
        return signals

    @staticmethod
    def latest_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        value = ta.atr(df["high"], df["low"], df["close"], period).iloc[-1]
        return None if pd.isna(value) else float(value)
