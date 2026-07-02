"""Strategy registry."""

from typing import Dict, Optional, Type

from .base import Strategy, infer_regime, validate_ohlcv
from .breakout import BreakoutStrategy
from .ensemble import EnsembleStrategy, regime_series
from .mean_reversion import MeanReversionStrategy
from .momentum import MomentumStrategy

STRATEGY_REGISTRY: Dict[str, Type[Strategy]] = {
    MomentumStrategy.name: MomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    BreakoutStrategy.name: BreakoutStrategy,
    EnsembleStrategy.name: EnsembleStrategy,
}


def build_strategy(name: str, params: Optional[dict] = None) -> Strategy:
    """Instantiate a strategy by registry name."""
    try:
        cls = STRATEGY_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {sorted(STRATEGY_REGISTRY)}"
        ) from None
    return cls(params=params)


__all__ = [
    "Strategy",
    "MomentumStrategy",
    "MeanReversionStrategy",
    "BreakoutStrategy",
    "EnsembleStrategy",
    "STRATEGY_REGISTRY",
    "build_strategy",
    "infer_regime",
    "regime_series",
    "validate_ohlcv",
]
