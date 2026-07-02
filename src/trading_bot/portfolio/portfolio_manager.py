"""Portfolio manager: positions, cash, equity, and performance stats.

The portfolio is the single source of truth for open positions. It is fed
by filled orders from the execution layer and priced with live data. All
values are in the account's quote currency (e.g. USD/USDT).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..models import (
    Order,
    OrderSide,
    Position,
    TradeRecord,
    utc_now,
)

logger = logging.getLogger(__name__)


class PortfolioManager:
    def __init__(self, quote_currency: str = "USD", starting_cash: float = 0.0):
        self.quote_currency = quote_currency
        self.cash = float(starting_cash)
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[TradeRecord] = []
        self.total_fees = 0.0

    # ------------------------------------------------------------------ #
    # Order application
    # ------------------------------------------------------------------ #

    def apply_fill(self, order: Order, strategy: str = "") -> Optional[TradeRecord]:
        """Apply a filled order. Returns a TradeRecord when a position
        closes (fully), else None."""
        if order.average_price is None or order.filled <= 0:
            logger.warning("apply_fill called with unfilled order %s", order.id)
            return None

        price = order.average_price
        amount = order.filled
        fee = order.fee
        self.total_fees += fee

        if order.side == OrderSide.BUY:
            self.cash -= amount * price + fee
            existing = self.positions.get(order.symbol)
            if existing:
                total = existing.size + amount
                existing.entry_price = (
                    existing.size * existing.entry_price + amount * price
                ) / total
                existing.size = total
                existing.fees_paid += fee
            else:
                self.positions[order.symbol] = Position(
                    symbol=order.symbol,
                    size=amount,
                    entry_price=price,
                    strategy=strategy,
                    fees_paid=fee,
                )
            return None

        # SELL
        position = self.positions.get(order.symbol)
        self.cash += amount * price - fee
        if position is None:
            logger.warning(
                "Sell fill for %s with no tracked position — cash updated only",
                order.symbol,
            )
            return None

        closed_size = min(amount, position.size)
        pnl = (price - position.entry_price) * closed_size
        position.realized_pnl += pnl
        position.fees_paid += fee
        position.size -= closed_size

        if position.size <= 1e-12:
            record = TradeRecord(
                symbol=order.symbol,
                entry_price=position.entry_price,
                exit_price=price,
                size=closed_size,
                pnl=position.realized_pnl,
                fees=position.fees_paid,
                strategy=position.strategy or strategy,
                exit_reason=order.reason,
                opened_at=position.opened_at,
                closed_at=utc_now(),
            )
            self.closed_trades.append(record)
            del self.positions[order.symbol]
            logger.info(
                "Closed %s: pnl=%.2f fees=%.2f (%s)",
                order.symbol,
                record.pnl,
                record.fees,
                order.reason,
            )
            return record
        return None

    # ------------------------------------------------------------------ #
    # Valuation
    # ------------------------------------------------------------------ #

    def equity(self, prices: Dict[str, float]) -> float:
        value = self.cash
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, pos.entry_price)
            value += pos.size * price
        return value

    def exposure(self, prices: Dict[str, float]) -> float:
        return sum(
            pos.size * prices.get(symbol, pos.entry_price)
            for symbol, pos in self.positions.items()
        )

    def open_notionals(self, prices: Dict[str, float]) -> Dict[str, float]:
        return {
            symbol: pos.size * prices.get(symbol, pos.entry_price)
            for symbol, pos in self.positions.items()
        }

    # ------------------------------------------------------------------ #
    # Stats
    # ------------------------------------------------------------------ #

    def stats(self) -> Dict[str, float]:
        trades = self.closed_trades
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        return {
            "cash": self.cash,
            "open_positions": len(self.positions),
            "closed_trades": len(trades),
            "win_rate": len(wins) / len(trades) if trades else 0.0,
            "total_pnl": sum(t.pnl for t in trades),
            "profit_factor": (
                gross_profit / gross_loss if gross_loss > 0 else float("inf")
            )
            if trades
            else 0.0,
            "total_fees": self.total_fees,
        }
