"""LiveBroker must never blind-retry an order after a lost response.

A real venue cannot be asked to drop responses on demand, so this uses a
minimal scripted exchange — the rare unavoidable stand-in under the repo's
no-mocks policy (flagged per policy; tests/test_live_integration.py covers
the real network path). The scripted venue ACCEPTS the order but loses the
response, which is exactly the double-buy scenario.
"""

import ccxt

from src.trading_bot.execution.brokers import LiveBroker
from src.trading_bot.models import Order, OrderSide, OrderStatus, OrderType


class LostResponseExchange:
    """Accepts create_order but always raises RequestTimeout afterward."""

    has = {"fetchOrder": False, "fetchOpenOrders": True, "fetchClosedOrders": False}
    markets = {"BTC/USD": {"limits": {}}}

    def __init__(self):
        self.accepted = []

    def amount_to_precision(self, symbol, amount):
        return amount

    def price_to_precision(self, symbol, price):
        return price

    async def create_order(self, symbol, type_, side, amount, price, params=None):
        self.accepted.append(
            {
                "id": f"EX-{len(self.accepted) + 1}",
                "clientOrderId": (params or {}).get("clientOrderId"),
                "symbol": symbol,
                "status": "open",
                "filled": 0.0,
            }
        )
        raise ccxt.RequestTimeout("response lost in transit")

    async def fetch_open_orders(self, symbol):
        return list(self.accepted)


async def test_lost_response_recovers_order_instead_of_resending():
    exchange = LostResponseExchange()
    broker = LiveBroker(exchange, max_retries=3)
    order = Order(
        symbol="BTC/USD", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1
    )
    result = await broker.create_order(order)

    # THE invariant: the venue received exactly one order, not three.
    assert len(exchange.accepted) == 1
    assert exchange.accepted[0]["clientOrderId"] == order.id
    assert result.exchange_order_id == "EX-1"
    assert result.status == OrderStatus.OPEN  # recovered, tracked, not re-sent


async def test_order_never_accepted_is_rejected_after_retries():
    class BlackholeExchange(LostResponseExchange):
        async def create_order(self, symbol, type_, side, amount, price, params=None):
            raise ccxt.RequestTimeout("network down")  # nothing ever accepted

        async def fetch_open_orders(self, symbol):
            return []

    broker = LiveBroker(BlackholeExchange(), max_retries=2)
    order = Order(
        symbol="BTC/USD", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1
    )
    result = await broker.create_order(order)
    assert result.status == OrderStatus.REJECTED
    assert "network failure" in result.reason


async def test_exchange_error_rejects_without_retry():
    class RefusingExchange(LostResponseExchange):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def create_order(self, symbol, type_, side, amount, price, params=None):
            self.calls += 1
            raise ccxt.ExchangeError("insufficient funds")

    exchange = RefusingExchange()
    broker = LiveBroker(exchange, max_retries=3)
    order = Order(
        symbol="BTC/USD", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1
    )
    result = await broker.create_order(order)
    assert result.status == OrderStatus.REJECTED
    assert exchange.calls == 1  # ExchangeError is terminal, never retried
    assert "exchange error" in result.reason
