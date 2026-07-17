"""Core engine safety gates — offline, driven by committed real market data.

Covers the headline safety features that previously had zero coverage in
the CI-gating suite: the two-key live gate, protective-stop propagation to
asynchronously filled orders, and the single-shot close guard.
"""

import pytest

from src.trading_bot.core import TradingBot
from src.trading_bot.data.market_data_manager import MarketDataManager
from src.trading_bot.execution.brokers import OrderManager, PaperBroker
from src.trading_bot.models import OrderSide, OrderStatus, OrderType
from src.trading_bot.portfolio.portfolio_manager import PortfolioManager


class ReplayData(MarketDataManager):
    """Replays committed real BTC closes in sequence — no network, no mocks
    (the prices are recorded market data, not invented numbers)."""

    def __init__(self, closes):
        super().__init__(exchange_id="kraken", timeframe="1h")
        self._closes = [float(c) for c in closes]
        self._i = 0

    def advance(self) -> None:
        self._i = min(self._i + 1, len(self._closes) - 1)

    async def get_current_price(self, symbol: str) -> float:
        return self._closes[self._i]

    async def get_current_prices(self, symbols):
        return {s: self._closes[self._i] for s in symbols}


def make_bot(tmp_path, config=None) -> TradingBot:
    cfg = {"journal": {"db_path": str(tmp_path / "journal.db")}}
    cfg.update(config or {})
    return TradingBot(cfg)


class TestLiveTwoKeyGate:
    async def test_live_mode_refused_without_env(self, tmp_path, monkeypatch):
        """mode:'live' alone must never be enough to start live trading."""
        monkeypatch.delenv("TRADING_BOT_LIVE", raising=False)
        bot = make_bot(tmp_path, {"mode": "live"})
        with pytest.raises(RuntimeError, match="TRADING_BOT_LIVE"):
            await bot.setup()

    async def test_wrong_env_value_also_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADING_BOT_LIVE", "yes")  # exact-match: 'YES'
        bot = make_bot(tmp_path, {"mode": "live"})
        with pytest.raises(RuntimeError, match="TRADING_BOT_LIVE"):
            await bot.setup()


class TestProtectionPropagation:
    async def test_poll_fill_attaches_stop_and_target(self, tmp_path, btc_df):
        """An entry that fills asynchronously (via the poll loop) must carry
        the risk decision's stop/target — this is the stopless-position bug."""
        closes = btc_df["close"].iloc[-200:].tolist()
        data = ReplayData(closes)
        bot = make_bot(tmp_path)
        bot.portfolio = PortfolioManager("USD", starting_cash=1_000_000.0)
        broker = PaperBroker(data, starting_balances={"USD": 1_000_000.0})
        bot.orders = OrderManager(broker, stale_after_seconds=3600.0)

        # A limit just above the series minimum: rests at the first price,
        # crosses later. Both facts hold in this committed data window.
        first, low = closes[0], min(closes)
        limit = low * 1.0005
        assert first > limit, "test data window must start above the limit"

        order = await bot.orders.submit(
            "BTC/USD", OrderSide.BUY, 0.1, OrderType.LIMIT, price=limit
        )
        assert order.status == OrderStatus.OPEN
        bot._pending_protection[order.id] = (limit * 0.97, limit * 1.05)

        while await data.get_current_price("BTC/USD") > limit:
            data.advance()
        await bot._poll_and_apply_fills()

        assert order.status == OrderStatus.FILLED
        position = bot.portfolio.positions["BTC/USD"]
        assert position.stop_loss == pytest.approx(limit * 0.97)
        assert position.take_profit == pytest.approx(limit * 1.05)
        assert order.id not in bot._pending_protection

    async def test_dead_order_protection_is_pruned(self, tmp_path, btc_df):
        """Stale-cancelled orders must not leak protection entries."""
        closes = btc_df["close"].iloc[-50:].tolist()
        data = ReplayData(closes)
        bot = make_bot(tmp_path)
        bot.portfolio = PortfolioManager("USD", starting_cash=1_000_000.0)
        broker = PaperBroker(data, starting_balances={"USD": 1_000_000.0})
        bot.orders = OrderManager(broker, stale_after_seconds=0.0)  # instant stale

        order = await bot.orders.submit(
            "BTC/USD", OrderSide.BUY, 0.1, OrderType.LIMIT, price=min(closes) * 0.5
        )
        assert order.status == OrderStatus.OPEN
        bot._pending_protection[order.id] = (1.0, 2.0)
        await bot._poll_and_apply_fills()  # cancels the stale order
        assert order.status == OrderStatus.CANCELLED
        assert bot._pending_protection == {}


class TestSingleShotClose:
    async def test_close_not_refired_while_in_flight(self, tmp_path, btc_df):
        """The 10s stop check must not stack a second full-size sell while
        the first close order is still working."""
        closes = btc_df["close"].iloc[-50:].tolist()
        data = ReplayData(closes)
        bot = make_bot(tmp_path)
        bot.portfolio = PortfolioManager("USD", starting_cash=1_000_000.0)
        broker = PaperBroker(data, starting_balances={"USD": 1_000_000.0})
        bot.orders = OrderManager(broker, stale_after_seconds=3600.0)

        buy = await bot.orders.submit("BTC/USD", OrderSide.BUY, 0.1, OrderType.MARKET)
        bot._apply_fill(buy)
        assert "BTC/USD" in bot.portfolio.positions

        bot._closing["BTC/USD"] = "in-flight-close-order"
        submitted_before = len(bot.orders.history)
        await bot._close_position("BTC/USD", "stop_loss")
        assert len(bot.orders.history) == submitted_before  # no second sell
