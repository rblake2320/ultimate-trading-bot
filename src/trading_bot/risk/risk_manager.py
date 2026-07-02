"""Risk engine: position sizing, protections, and portfolio-level limits.

Sizing model (per trade):
  1. Risk budget = equity * risk_per_trade (default 0.75%).
  2. Quantity = risk budget / stop distance (signal's stop, else k*ATR).
  3. Caps applied in order: max position notional (% of equity), fractional
     Kelly from realized trade history, remaining portfolio heat.

Protections (Freqtrade-style circuit breakers, plus the ones it lacks):
  - Daily loss halt with UTC-day rollover.
  - Max drawdown halt (peak-to-trough on equity).
  - Stoploss guard: N stop-loss exits within a window halts entries.
  - Consecutive-loss cooldown.
  - Per-symbol cooldown after any exit (no instant re-entry).
  - Correlation cap: refuses entries highly correlated with open exposure,
    computed from actual return series, not hardcoded guesses.

All state mutations happen through record_* / update_* methods so the same
engine is reusable in live trading and in the backtester.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..models import Signal, TradeRecord, utc_now

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    quantity: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class _ExitEvent:
    symbol: str
    timestamp: datetime
    was_stop_loss: bool
    pnl: float


@dataclass
class RiskState:
    """Mutable state, separated so it can be snapshotted/inspected."""

    equity: float = 0.0
    peak_equity: float = 0.0
    daily_pnl: float = 0.0
    daily_anchor: Optional[datetime] = None  # start of current UTC day
    consecutive_losses: int = 0
    halted_until: Optional[datetime] = None
    halt_reason: str = ""
    exits: List[_ExitEvent] = field(default_factory=list)


class RiskManager:
    def __init__(self, config: Optional[dict] = None):
        cfg = dict(config or {})
        # Sizing
        self.risk_per_trade = float(cfg.get("risk_per_trade", 0.0075))
        self.max_position_pct = float(cfg.get("max_position_pct", 0.20))
        self.max_portfolio_heat = float(cfg.get("max_portfolio_heat", 0.05))
        self.kelly_fraction = float(cfg.get("kelly_fraction", 0.5))
        self.default_stop_atr_mult = float(cfg.get("default_stop_atr_mult", 2.0))
        self.min_notional = float(cfg.get("min_notional", 10.0))
        # Protections
        self.max_daily_loss_pct = float(cfg.get("max_daily_loss_pct", 0.03))
        self.max_drawdown_pct = float(cfg.get("max_drawdown_pct", 0.15))
        self.stoploss_guard_count = int(cfg.get("stoploss_guard_count", 4))
        self.stoploss_guard_window_h = float(cfg.get("stoploss_guard_window_hours", 24))
        self.max_consecutive_losses = int(cfg.get("max_consecutive_losses", 5))
        self.halt_cooldown_h = float(cfg.get("halt_cooldown_hours", 12))
        self.reentry_cooldown_min = float(cfg.get("reentry_cooldown_minutes", 60))
        # Correlation
        self.correlation_threshold = float(cfg.get("correlation_threshold", 0.85))
        self.correlation_lookback = int(cfg.get("correlation_lookback", 100))

        self.state = RiskState()
        self._returns: Dict[str, pd.Series] = {}

    # ------------------------------------------------------------------ #
    # State feeds
    # ------------------------------------------------------------------ #

    def update_equity(self, equity: float, now: Optional[datetime] = None) -> None:
        now = now or utc_now()
        st = self.state
        if st.daily_anchor is None or now.date() != st.daily_anchor.date():
            # New UTC day: reset the daily loss budget.
            st.daily_anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
            st.daily_pnl = 0.0
        if st.equity > 0:
            st.daily_pnl += equity - st.equity
        st.equity = equity
        st.peak_equity = max(st.peak_equity, equity)

    def update_returns(self, symbol: str, closes: pd.Series) -> None:
        """Feed recent close prices so correlations use real data."""
        tail = closes.tail(self.correlation_lookback + 1)
        self._returns[symbol] = tail.pct_change().dropna()

    def record_trade_result(
        self, record: TradeRecord, was_stop_loss: bool, now: Optional[datetime] = None
    ) -> None:
        now = now or utc_now()
        st = self.state
        st.exits.append(
            _ExitEvent(record.symbol, now, was_stop_loss, record.pnl)
        )
        # Keep the window bounded.
        cutoff = now - timedelta(hours=max(self.stoploss_guard_window_h, 48))
        st.exits = [e for e in st.exits if e.timestamp >= cutoff]

        if record.pnl < 0:
            st.consecutive_losses += 1
        else:
            st.consecutive_losses = 0

        self._check_protections(now)

    # ------------------------------------------------------------------ #
    # Protections
    # ------------------------------------------------------------------ #

    def _halt(self, reason: str, now: datetime) -> None:
        st = self.state
        st.halted_until = now + timedelta(hours=self.halt_cooldown_h)
        st.halt_reason = reason
        logger.critical(
            "TRADING HALTED until %s: %s", st.halted_until.isoformat(), reason
        )

    def _check_protections(self, now: datetime) -> None:
        st = self.state
        window = now - timedelta(hours=self.stoploss_guard_window_h)
        stop_hits = sum(
            1 for e in st.exits if e.was_stop_loss and e.timestamp >= window
        )
        if stop_hits >= self.stoploss_guard_count:
            self._halt(
                f"stoploss guard: {stop_hits} stops in "
                f"{self.stoploss_guard_window_h:.0f}h",
                now,
            )
        if st.consecutive_losses >= self.max_consecutive_losses:
            self._halt(f"{st.consecutive_losses} consecutive losses", now)

    def trading_allowed(self, now: Optional[datetime] = None) -> RiskDecision:
        """Global gate — checked before any new entry."""
        now = now or utc_now()
        st = self.state

        if st.halted_until is not None:
            if now < st.halted_until:
                return RiskDecision(False, f"halted: {st.halt_reason}")
            st.halted_until = None
            st.halt_reason = ""
            st.consecutive_losses = 0
            logger.info("Trading halt expired — entries re-enabled")

        if st.equity > 0 and st.daily_pnl < -self.max_daily_loss_pct * st.equity:
            return RiskDecision(
                False,
                f"daily loss limit: {st.daily_pnl:.2f} "
                f"(limit {-self.max_daily_loss_pct * st.equity:.2f})",
            )

        if st.peak_equity > 0:
            drawdown = (st.peak_equity - st.equity) / st.peak_equity
            if drawdown > self.max_drawdown_pct:
                return RiskDecision(
                    False,
                    f"max drawdown breached: {drawdown:.1%} > "
                    f"{self.max_drawdown_pct:.1%}",
                )

        return RiskDecision(True, "ok")

    def _symbol_in_cooldown(self, symbol: str, now: datetime) -> bool:
        cutoff = now - timedelta(minutes=self.reentry_cooldown_min)
        return any(
            e.symbol == symbol and e.timestamp >= cutoff for e in self.state.exits
        )

    def _max_correlation_with_open(
        self, symbol: str, open_symbols: List[str]
    ) -> Optional[float]:
        base = self._returns.get(symbol)
        if base is None or len(base) < 20:
            return None
        worst = None
        for other in open_symbols:
            if other == symbol:
                continue
            series = self._returns.get(other)
            if series is None or len(series) < 20:
                continue
            joined = pd.concat([base, series], axis=1, join="inner").dropna()
            if len(joined) < 20:
                continue
            corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
            if not np.isnan(corr):
                worst = corr if worst is None else max(worst, corr)
        return worst

    def _kelly_cap(self) -> float:
        """Fractional-Kelly cap on position notional (as fraction of equity),
        estimated from realized trade history. Neutral until enough data."""
        pnls = [e.pnl for e in self.state.exits]
        if len(pnls) < 10:
            return self.max_position_pct
        wins = [p for p in pnls if p > 0]
        losses = [-p for p in pnls if p < 0]
        if not wins or not losses:
            return self.max_position_pct
        win_rate = len(wins) / len(pnls)
        avg_win = float(np.mean(wins))
        avg_loss = float(np.mean(losses))
        if avg_loss <= 0:
            return self.max_position_pct
        b = avg_win / avg_loss
        kelly = win_rate - (1 - win_rate) / b
        kelly = max(0.0, kelly) * self.kelly_fraction
        # Kelly of 0 (negative edge) still allows the floor of one minimum
        # position so the estimate can keep updating.
        return float(np.clip(kelly, 0.02, self.max_position_pct))

    # ------------------------------------------------------------------ #
    # Sizing
    # ------------------------------------------------------------------ #

    def evaluate_entry(
        self,
        signal: Signal,
        equity: float,
        open_positions: Dict[str, float],
        atr: Optional[float] = None,
        max_open_positions: int = 5,
        now: Optional[datetime] = None,
    ) -> RiskDecision:
        """Full entry gate + position size.

        open_positions maps symbol -> current notional (quote currency).
        """
        now = now or utc_now()

        gate = self.trading_allowed(now)
        if not gate.approved:
            return gate

        if signal.symbol in open_positions:
            return RiskDecision(False, f"already holding {signal.symbol}")
        if len(open_positions) >= max_open_positions:
            return RiskDecision(False, f"max open positions ({max_open_positions})")
        if self._symbol_in_cooldown(signal.symbol, now):
            return RiskDecision(
                False, f"{signal.symbol} in re-entry cooldown"
            )

        corr = self._max_correlation_with_open(
            signal.symbol, list(open_positions.keys())
        )
        if corr is not None and corr > self.correlation_threshold:
            return RiskDecision(
                False,
                f"correlation {corr:.2f} with open positions exceeds "
                f"{self.correlation_threshold:.2f}",
            )

        price = signal.price
        if price <= 0 or equity <= 0:
            return RiskDecision(False, "invalid price or equity")

        # Stop distance: prefer the strategy's stop, fall back to ATR.
        if signal.stop_loss and signal.stop_loss < price:
            stop_distance = price - signal.stop_loss
            stop_loss = signal.stop_loss
        elif atr and atr > 0:
            stop_distance = self.default_stop_atr_mult * atr
            stop_loss = price - stop_distance
        else:
            return RiskDecision(False, "no stop level and no ATR available")

        risk_budget = equity * self.risk_per_trade * max(signal.confidence, 0.25)
        quantity = risk_budget / stop_distance

        # Cap 1: max notional per position (Kelly-adjusted).
        notional_cap = equity * min(self.max_position_pct, self._kelly_cap())
        quantity = min(quantity, notional_cap / price)

        # Cap 2: portfolio heat. Each position risks ~risk_per_trade of its
        # notional-implied budget, so total notional is capped at
        # heat / risk-per-trade of equity (e.g. 5% / 0.75% ≈ 6.7x... bounded
        # below by equity itself for spot accounts).
        open_notional = sum(open_positions.values())
        max_total_notional = min(
            equity, equity * self.max_portfolio_heat / self.risk_per_trade
        )
        remaining_notional = max(0.0, max_total_notional - open_notional)
        quantity = min(quantity, remaining_notional / price)

        notional = quantity * price
        if notional < self.min_notional:
            return RiskDecision(
                False,
                f"position too small after caps: {notional:.2f} < "
                f"{self.min_notional:.2f} min notional",
            )

        return RiskDecision(
            True,
            "approved",
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=signal.take_profit,
        )

    def metrics(self) -> Dict[str, float]:
        st = self.state
        drawdown = (
            (st.peak_equity - st.equity) / st.peak_equity if st.peak_equity > 0 else 0.0
        )
        return {
            "equity": st.equity,
            "peak_equity": st.peak_equity,
            "daily_pnl": st.daily_pnl,
            "drawdown": drawdown,
            "consecutive_losses": st.consecutive_losses,
            "halted": st.halted_until is not None,
            "kelly_cap": self._kelly_cap(),
        }
