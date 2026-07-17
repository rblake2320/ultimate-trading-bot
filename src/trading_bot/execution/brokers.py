"""Broker abstraction: one interface, live and paper implementations.

- ``LiveBroker`` sends real orders to an authenticated ccxt exchange, with
  precision/min-notional handling and retry on transient network errors.
- ``PaperBroker`` simulates fills against *live* market prices from the
  MarketDataManager: market orders fill immediately at the current price
  plus slippage; limit orders rest until the live price crosses them.
  Balances and fees are tracked exactly as an exchange would.

The rest of the system only ever sees the ``Broker`` interface, so paper
and live runs exercise identical code paths.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ..data.market_data_manager import MarketDataManager
from ..models import Order, OrderSide, OrderStatus, OrderType, utc_now

logger = logging.getLogger(__name__)


class Broker(ABC):
    @abstractmethod
    async def create_order(self, order: Order) -> Order:
        """Submit an order. Returns the order with updated status/fills."""

    @abstractmethod
    async def cancel_order(self, order: Order) -> bool: ...

    @abstractmethod
    async def sync_order(self, order: Order) -> Order:
        """Refresh order state (fills, status) from the venue."""

    @abstractmethod
    async def fetch_balances(self) -> Dict[str, float]:
        """Free balances by currency code."""

    @property
    @abstractmethod
    def is_paper(self) -> bool: ...


class PaperBroker(Broker):
    """Simulated execution against live market data."""

    def __init__(
        self,
        data_manager: MarketDataManager,
        starting_balances: Optional[Dict[str, float]] = None,
        taker_fee: float = 0.0026,  # Kraken taker default
        maker_fee: float = 0.0016,
        slippage_bps: float = 5.0,
    ):
        self.data = data_manager
        self.balances: Dict[str, float] = dict(starting_balances or {"USD": 10_000.0})
        self.taker_fee = float(taker_fee)
        self.maker_fee = float(maker_fee)
        self.slippage = float(slippage_bps) / 10_000.0
        self.open_orders: Dict[str, Order] = {}
        self._lock = asyncio.Lock()

    @property
    def is_paper(self) -> bool:
        return True

    @staticmethod
    def _split(symbol: str) -> tuple:
        base, _, quote = symbol.partition("/")
        if not quote:
            raise ValueError(f"Symbol must be BASE/QUOTE, got '{symbol}'")
        return base, quote

    async def create_order(self, order: Order) -> Order:
        async with self._lock:
            price = await self.data.get_current_price(order.symbol)
            if order.type == OrderType.MARKET:
                fill_price = (
                    price * (1 + self.slippage)
                    if order.side == OrderSide.BUY
                    else price * (1 - self.slippage)
                )
                self._settle(order, order.amount, fill_price, self.taker_fee)
            else:
                if order.price is None or order.price <= 0:
                    order.status = OrderStatus.REJECTED
                    order.reason += " | limit order without price"
                    return order
                crossing = (
                    order.side == OrderSide.BUY and price <= order.price
                ) or (order.side == OrderSide.SELL and price >= order.price)
                if crossing:
                    # Marketable limit: fills immediately at the limit price.
                    self._settle(order, order.amount, order.price, self.taker_fee)
                else:
                    order.status = OrderStatus.OPEN
                    self.open_orders[order.id] = order
            order.updated_at = utc_now()
            return order

    def _settle(self, order: Order, amount: float, price: float, fee_rate: float):
        """Apply a fill to the balance sheet, rejecting if funds are short."""
        base, quote = self._split(order.symbol)
        cost = amount * price
        fee = cost * fee_rate
        if order.side == OrderSide.BUY:
            if self.balances.get(quote, 0.0) < cost + fee:
                order.status = OrderStatus.REJECTED
                order.reason += (
                    f" | insufficient {quote}: need {cost + fee:.2f},"
                    f" have {self.balances.get(quote, 0.0):.2f}"
                )
                return
            self.balances[quote] = self.balances.get(quote, 0.0) - cost - fee
            self.balances[base] = self.balances.get(base, 0.0) + amount
        else:
            if self.balances.get(base, 0.0) < amount * 0.999999:
                order.status = OrderStatus.REJECTED
                order.reason += (
                    f" | insufficient {base}: need {amount},"
                    f" have {self.balances.get(base, 0.0)}"
                )
                return
            self.balances[base] = self.balances.get(base, 0.0) - amount
            self.balances[quote] = self.balances.get(quote, 0.0) + cost - fee
        order.filled = amount
        order.average_price = price
        order.fee = fee
        order.status = OrderStatus.FILLED
        order.exchange_order_id = f"paper-{order.id[:12]}"

    async def sync_order(self, order: Order) -> Order:
        """Check whether a resting limit order has been crossed by the live
        price. Called by the order manager's polling loop."""
        if order.id not in self.open_orders:
            return order
        async with self._lock:
            price = await self.data.get_current_price(order.symbol)
            crossed = (
                order.side == OrderSide.BUY and price <= order.price
            ) or (order.side == OrderSide.SELL and price >= order.price)
            if crossed:
                # Resting maker order: fills at its limit price, maker fee.
                # _settle may also REJECT (insufficient funds) — either way the
                # order is closed and must leave the book.
                self._settle(order, order.amount, order.price, self.maker_fee)
                if order.is_closed:
                    del self.open_orders[order.id]
                order.updated_at = utc_now()
        return order

    async def cancel_order(self, order: Order) -> bool:
        async with self._lock:
            if order.id in self.open_orders:
                del self.open_orders[order.id]
                order.status = OrderStatus.CANCELLED
                order.updated_at = utc_now()
                return True
        return False

    async def fetch_balances(self) -> Dict[str, float]:
        return dict(self.balances)


class LiveBroker(Broker):
    """Real execution through an authenticated ccxt exchange instance."""

    def __init__(self, exchange, max_retries: int = 3):
        self.exchange = exchange
        self.max_retries = int(max_retries)

    @property
    def is_paper(self) -> bool:
        return False

    def _market(self, symbol: str) -> dict:
        return self.exchange.markets.get(symbol, {})

    def _validate(self, order: Order) -> Optional[str]:
        market = self._market(order.symbol)
        if not market:
            return f"unknown market {order.symbol}"
        limits = market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min")
        if min_amount and order.amount < float(min_amount):
            return f"amount {order.amount} below exchange minimum {min_amount}"
        min_cost = (limits.get("cost") or {}).get("min")
        ref_price = order.price
        if min_cost and ref_price and order.amount * ref_price < float(min_cost):
            return (
                f"notional {order.amount * ref_price:.2f} below exchange"
                f" minimum {min_cost}"
            )
        return None

    async def create_order(self, order: Order) -> Order:
        import ccxt

        problem = self._validate(order)
        if problem:
            order.status = OrderStatus.REJECTED
            order.reason += f" | {problem}"
            logger.warning("Order rejected pre-flight: %s", problem)
            return order

        amount = float(self.exchange.amount_to_precision(order.symbol, order.amount))
        price = (
            float(self.exchange.price_to_precision(order.symbol, order.price))
            if order.price
            else None
        )

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                raw = await self.exchange.create_order(
                    order.symbol,
                    order.type.value,
                    order.side.value,
                    amount,
                    price,
                    # Idempotency key: if a response is lost, the resting order
                    # can be found again instead of placed again.
                    {"clientOrderId": order.id},
                )
                self._apply(order, raw)
                return order
            except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
                last_error = exc
                wait = 2.0**attempt
                logger.warning(
                    "Network error placing order (attempt %d/%d): %s — retrying in %.0fs",
                    attempt,
                    self.max_retries,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
                # A timeout does NOT mean the order failed — the exchange may
                # have accepted it and only the response was lost. Re-sending
                # blindly is how bots double-buy. Look for it first.
                existing = await self._find_by_client_id(order)
                if existing is not None:
                    logger.warning(
                        "Order %s was accepted by the exchange despite the "
                        "network error — recovered, not re-sent",
                        order.id,
                    )
                    self._apply(order, existing)
                    return order
            except ccxt.ExchangeError as exc:
                order.status = OrderStatus.REJECTED
                order.reason += f" | exchange error: {exc}"
                logger.error("Order rejected by exchange: %s", exc)
                return order

        order.status = OrderStatus.REJECTED
        order.reason += f" | network failure after {self.max_retries} retries: {last_error}"
        return order

    async def _find_by_client_id(self, order: Order) -> Optional[dict]:
        """Search the venue for an order carrying our clientOrderId. Returns
        the raw ccxt order dict, or None if it genuinely never arrived."""
        try:
            has = getattr(self.exchange, "has", {}) or {}
            if has.get("fetchOrder"):
                try:
                    raw = await self.exchange.fetch_order(
                        None, order.symbol, {"clientOrderId": order.id}
                    )
                    if raw:
                        return raw
                except Exception:  # noqa: BLE001 - not all venues support lookup by client id
                    pass
            for fetcher in ("fetch_open_orders", "fetch_closed_orders"):
                flag = "fetchOpenOrders" if "open" in fetcher else "fetchClosedOrders"
                if not has.get(flag):
                    continue
                for raw in await getattr(self.exchange, fetcher)(order.symbol):
                    if raw.get("clientOrderId") == order.id:
                        return raw
        except Exception as exc:  # noqa: BLE001 - lookup is best-effort
            logger.warning("clientOrderId lookup failed for %s: %s", order.id, exc)
        return None

    def _apply(self, order: Order, raw: dict) -> None:
        order.exchange_order_id = raw.get("id")
        status_map = {
            "open": OrderStatus.OPEN,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "expired": OrderStatus.CANCELLED,
        }
        order.status = status_map.get(raw.get("status"), OrderStatus.OPEN)
        order.filled = float(raw.get("filled") or 0.0)
        if raw.get("average"):
            order.average_price = float(raw["average"])
        fee = raw.get("fee") or {}
        if fee.get("cost"):
            order.fee = float(fee["cost"])
        if 0 < order.filled < order.amount and order.status == OrderStatus.OPEN:
            order.status = OrderStatus.PARTIALLY_FILLED
        order.updated_at = utc_now()

    async def sync_order(self, order: Order) -> Order:
        if not order.exchange_order_id or order.is_closed:
            return order
        try:
            raw = await self.exchange.fetch_order(
                order.exchange_order_id, order.symbol
            )
            self._apply(order, raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to sync order %s: %s", order.exchange_order_id, exc)
        return order

    async def cancel_order(self, order: Order) -> bool:
        if not order.exchange_order_id:
            return False
        try:
            await self.exchange.cancel_order(order.exchange_order_id, order.symbol)
            order.status = OrderStatus.CANCELLED
            order.updated_at = utc_now()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to cancel order %s: %s", order.exchange_order_id, exc)
            return False

    async def fetch_balances(self) -> Dict[str, float]:
        raw = await self.exchange.fetch_balance()
        free = raw.get("free", {}) or {}
        return {k: float(v) for k, v in free.items() if v}


class OrderManager:
    """Order lifecycle on top of a Broker: submission, polling, staleness."""

    def __init__(self, broker: Broker, stale_after_seconds: float = 300.0):
        self.broker = broker
        self.stale_after = float(stale_after_seconds)
        self.orders: Dict[str, Order] = {}
        self.history: List[Order] = []

    async def submit(
        self,
        symbol: str,
        side: OrderSide,
        amount: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        reason: str = "",
    ) -> Order:
        if amount <= 0:
            raise ValueError(f"Order amount must be positive, got {amount}")
        order = Order(
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            reason=reason,
        )
        order = await self.broker.create_order(order)
        self.orders[order.id] = order
        self.history.append(order)
        logger.info(
            "Order %s: %s %s %.8f %s @ %s (%s)",
            order.status.value,
            order.side.value,
            order.type.value,
            order.amount,
            order.symbol,
            f"{order.average_price:.2f}" if order.average_price else order.price,
            reason or "manual",
        )
        return order

    async def poll_open_orders(self) -> List[Order]:
        """Sync all open orders; cancel any that have gone stale."""
        just_filled: List[Order] = []
        now = utc_now()
        for order in list(self.orders.values()):
            if order.is_closed:
                continue
            was_filled = order.status == OrderStatus.FILLED
            await self.broker.sync_order(order)
            if order.status == OrderStatus.FILLED and not was_filled:
                just_filled.append(order)
            elif (
                order.status in (OrderStatus.OPEN, OrderStatus.PENDING)
                and (now - order.created_at).total_seconds() >= self.stale_after
            ):
                logger.info("Cancelling stale order %s (%s)", order.id, order.symbol)
                await self.broker.cancel_order(order)
        # Drop closed orders from the active map (history keeps them).
        self.orders = {k: v for k, v in self.orders.items() if not v.is_closed}
        return just_filled

    async def cancel_all(self, symbol: Optional[str] = None) -> int:
        count = 0
        for order in list(self.orders.values()):
            if order.is_closed:
                continue
            if symbol is None or order.symbol == symbol:
                if await self.broker.cancel_order(order):
                    count += 1
        self.orders = {k: v for k, v in self.orders.items() if not v.is_closed}
        return count

    def stats(self) -> Dict[str, float]:
        total = len(self.history)
        filled = sum(1 for o in self.history if o.status == OrderStatus.FILLED)
        rejected = sum(1 for o in self.history if o.status == OrderStatus.REJECTED)
        return {
            "total_orders": total,
            "filled": filled,
            "rejected": rejected,
            "fill_rate": filled / total if total else 0.0,
            "open": sum(1 for o in self.orders.values() if not o.is_closed),
        }
