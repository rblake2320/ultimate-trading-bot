"""Live integration tests — real exchange public APIs and real local LLM.

These hit the network on purpose (project rule: no mocks). They are marked
``integration`` so they can be deselected in constrained environments:
``pytest -m "not integration"``.
"""

import pytest

from src.trading_bot.ai.llm_analyst import LLMAnalyst
from src.trading_bot.data.market_data_manager import MarketDataManager
from src.trading_bot.execution.brokers import OrderManager, PaperBroker
from src.trading_bot.models import OrderSide, OrderStatus, OrderType

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def kraken_data():
    data = MarketDataManager(exchange_id="kraken", timeframe="1h")
    await data.initialize()
    yield data
    await data.close()


class TestLiveMarketData:
    async def test_fetch_real_candles(self, kraken_data):
        df = await kraken_data.get_candles("BTC/USD", limit=100)
        assert len(df) >= 90
        assert (df["high"] >= df["low"]).all()
        assert (df["close"] > 0).all()
        # Closed-candle guarantee: the forming candle is excluded.
        import time

        last_open_ms = df.index[-1].value // 1_000_000
        assert last_open_ms + 3_600_000 <= time.time() * 1000

    async def test_current_price_is_live(self, kraken_data):
        price = await kraken_data.get_current_price("BTC/USD")
        assert price > 1_000  # BTC has never been below this in a decade
        # TTL cache returns the same value immediately.
        assert await kraken_data.get_current_price("BTC/USD") == price

    async def test_health_check(self, kraken_data):
        assert await kraken_data.health_check() is True


class TestPaperTradingAgainstLivePrices:
    async def test_market_buy_and_sell_round_trip(self, kraken_data):
        broker = PaperBroker(
            kraken_data, starting_balances={"USD": 100_000.0}, slippage_bps=5.0
        )
        orders = OrderManager(broker)

        live_price = await kraken_data.get_current_price("BTC/USD")
        buy = await orders.submit(
            "BTC/USD", OrderSide.BUY, 0.1, OrderType.MARKET, reason="test"
        )
        assert buy.status == OrderStatus.FILLED
        # Fill must be at the live price plus slippage, not a made-up number.
        assert buy.average_price == pytest.approx(live_price * 1.0005, rel=0.01)
        assert buy.fee > 0

        balances = await broker.fetch_balances()
        assert balances["BTC"] == pytest.approx(0.1)
        assert balances["USD"] < 100_000.0

        sell = await orders.submit(
            "BTC/USD", OrderSide.SELL, 0.1, OrderType.MARKET, reason="test"
        )
        assert sell.status == OrderStatus.FILLED
        balances = await broker.fetch_balances()
        assert balances["BTC"] == pytest.approx(0.0)
        # Round trip costs fees + spread; balance must be slightly below start.
        assert 99_000.0 < balances["USD"] < 100_000.0

    async def test_insufficient_funds_rejected(self, kraken_data):
        broker = PaperBroker(kraken_data, starting_balances={"USD": 10.0})
        order = await OrderManager(broker).submit(
            "BTC/USD", OrderSide.BUY, 1.0, OrderType.MARKET
        )
        assert order.status == OrderStatus.REJECTED
        assert "insufficient" in order.reason

    async def test_far_limit_order_rests_then_cancels(self, kraken_data):
        broker = PaperBroker(kraken_data, starting_balances={"USD": 100_000.0})
        orders = OrderManager(broker, stale_after_seconds=0.0)
        live_price = await kraken_data.get_current_price("BTC/USD")
        order = await orders.submit(
            "BTC/USD",
            OrderSide.BUY,
            0.01,
            OrderType.LIMIT,
            price=live_price * 0.5,  # far below market: must rest
        )
        assert order.status == OrderStatus.OPEN
        # Poll: not crossed, and instantly stale -> cancelled.
        await orders.poll_open_orders()
        assert order.status == OrderStatus.CANCELLED


async def _ollama_chat_model(url: str) -> str | None:
    """Return an installed general chat model, or None if Ollama is down."""
    import aiohttp

    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{url}/api/tags") as resp:
                if resp.status != 200:
                    return None
                names = [m["name"] for m in (await resp.json()).get("models", [])]
    except Exception:
        return None
    for prefix in ("gemma", "llama3", "qwen2.5:7b", "mistral", "qwen"):
        for name in names:
            if name.startswith(prefix) and "embed" not in name:
                return name
    return names[0] if names else None


class TestLLMAnalystLive:
    async def test_real_ollama_verdict(self, btc_df):
        """Runs the analyst against the local Ollama server for real."""
        url = "http://localhost:11434"
        model = await _ollama_chat_model(url)
        if model is None:
            pytest.skip("Ollama not running on localhost:11434")
        analyst = LLMAnalyst(
            {
                "enabled": True,
                "provider": "ollama",
                "model": model,
                "ollama_url": url,
                "timeout_seconds": 120,
            }
        )
        from src.trading_bot.models import Signal, SignalAction

        signal = Signal(
            symbol="BTC/USDT",
            action=SignalAction.BUY,
            confidence=0.7,
            price=float(btc_df["close"].iloc[-1]),
            strategy="ensemble",
            stop_loss=float(btc_df["close"].iloc[-1]) * 0.97,
        )
        verdict = await analyst.review(signal, btc_df)
        assert verdict.verdict in ("approve", "veto")
        assert verdict.reasoning

    def test_parse_verdict_json(self):
        raw = 'Here you go: {"verdict": "veto", "confidence": 0.8, "reasoning": "RSI 92, parabolic"}'
        verdict = LLMAnalyst._parse(raw)
        assert verdict.is_veto
        assert verdict.confidence == pytest.approx(0.8)

    async def test_fails_open_when_unreachable(self, btc_df):
        analyst = LLMAnalyst(
            {
                "enabled": True,
                "provider": "ollama",
                "ollama_url": "http://localhost:59999",  # nothing there
                "timeout_seconds": 3,
            }
        )
        from src.trading_bot.models import Signal, SignalAction

        signal = Signal(
            symbol="BTC/USDT",
            action=SignalAction.BUY,
            confidence=0.7,
            price=100.0,
            strategy="test",
        )
        verdict = await analyst.review(signal, btc_df)
        assert verdict.verdict == "unavailable"
        assert not verdict.is_veto
