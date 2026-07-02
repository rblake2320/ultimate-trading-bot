"""Shared domain models used across every subsystem.

All monetary amounts are floats in quote currency unless stated otherwise.
All timestamps are UTC, either epoch-milliseconds (exchange data) or aware
datetimes (internal bookkeeping).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    HOLD = "hold"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Regime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"


@dataclass
class Signal:
    """A trade recommendation produced by a strategy or the ensemble."""

    symbol: str
    action: SignalAction
    confidence: float  # 0.0 - 1.0
    price: float  # reference price when the signal was generated
    strategy: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    regime: Optional[Regime] = None
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence)))


@dataclass
class Order:
    """An order tracked by the execution layer."""

    symbol: str
    side: OrderSide
    type: OrderType
    amount: float  # base-currency quantity
    price: Optional[float] = None  # limit price; None for market orders
    id: str = field(default_factory=new_id)
    exchange_order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled: float = 0.0
    average_price: Optional[float] = None
    fee: float = 0.0  # quote currency
    reason: str = ""  # e.g. "signal:momentum", "stop_loss"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def is_closed(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        )

    @property
    def remaining(self) -> float:
        return max(0.0, self.amount - self.filled)


@dataclass
class Position:
    """An open position (spot long; size is base-currency quantity)."""

    symbol: str
    size: float
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strategy: str = ""
    opened_at: datetime = field(default_factory=utc_now)
    # Running totals for partial exits.
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    # Trailing-stop bookkeeping: highest price seen since entry.
    high_water_mark: float = 0.0

    def __post_init__(self) -> None:
        self.high_water_mark = max(self.high_water_mark, self.entry_price)

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.size

    def notional(self, current_price: float) -> float:
        return current_price * self.size


@dataclass
class Fill:
    """A single execution against an order."""

    order_id: str
    symbol: str
    side: OrderSide
    amount: float
    price: float
    fee: float = 0.0
    timestamp: datetime = field(default_factory=utc_now)


@dataclass
class TradeRecord:
    """A completed round-trip trade, for the journal and analytics."""

    symbol: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    fees: float
    strategy: str
    exit_reason: str
    opened_at: datetime
    closed_at: datetime

    @property
    def return_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price


def epoch_ms() -> int:
    return int(time.time() * 1000)
