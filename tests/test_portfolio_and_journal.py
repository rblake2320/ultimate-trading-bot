"""Portfolio accounting and SQLite journal round-trips (real database file)."""

from datetime import datetime, timezone

import pytest

from src.trading_bot.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TradeRecord,
)
from src.trading_bot.persistence.journal import TradeJournal
from src.trading_bot.portfolio.portfolio_manager import PortfolioManager


def filled_order(symbol, side, amount, price, fee=1.0, reason=""):
    order = Order(
        symbol=symbol,
        side=side,
        type=OrderType.MARKET,
        amount=amount,
        price=None,
        reason=reason,
    )
    order.status = OrderStatus.FILLED
    order.filled = amount
    order.average_price = price
    order.fee = fee
    return order


class TestPortfolio:
    def test_buy_then_sell_produces_trade_record(self):
        pm = PortfolioManager("USD", starting_cash=10_000.0)
        assert pm.apply_fill(filled_order("BTC/USD", OrderSide.BUY, 0.1, 50_000.0)) is None
        assert pm.cash == pytest.approx(10_000.0 - 5_000.0 - 1.0)
        assert pm.positions["BTC/USD"].size == pytest.approx(0.1)

        record = pm.apply_fill(
            filled_order("BTC/USD", OrderSide.SELL, 0.1, 55_000.0, reason="take_profit")
        )
        assert record is not None
        # Fee-inclusive PnL, matching the backtester: 500 gross - 2 in fees.
        assert record.pnl == pytest.approx(498.0)
        assert record.fees == pytest.approx(2.0)
        assert "BTC/USD" not in pm.positions
        assert pm.cash == pytest.approx(10_000.0 - 5_001.0 + 5_499.0)

    def test_averaging_up(self):
        pm = PortfolioManager("USD", starting_cash=10_000.0)
        pm.apply_fill(filled_order("ETH/USD", OrderSide.BUY, 1.0, 3_000.0))
        pm.apply_fill(filled_order("ETH/USD", OrderSide.BUY, 1.0, 3_100.0))
        pos = pm.positions["ETH/USD"]
        assert pos.size == pytest.approx(2.0)
        assert pos.entry_price == pytest.approx(3_050.0)

    def test_partial_close_keeps_position(self):
        pm = PortfolioManager("USD", starting_cash=10_000.0)
        pm.apply_fill(filled_order("ETH/USD", OrderSide.BUY, 2.0, 3_000.0))
        record = pm.apply_fill(filled_order("ETH/USD", OrderSide.SELL, 1.0, 3_200.0))
        assert record is None  # not fully closed
        assert pm.positions["ETH/USD"].size == pytest.approx(1.0)
        assert pm.positions["ETH/USD"].realized_pnl == pytest.approx(200.0)

    def test_equity_marks_to_market(self):
        pm = PortfolioManager("USD", starting_cash=10_000.0)
        pm.apply_fill(filled_order("BTC/USD", OrderSide.BUY, 0.1, 50_000.0, fee=0.0))
        assert pm.equity({"BTC/USD": 60_000.0}) == pytest.approx(11_000.0)

    def test_stats(self):
        pm = PortfolioManager("USD", starting_cash=10_000.0)
        pm.apply_fill(filled_order("BTC/USD", OrderSide.BUY, 0.1, 50_000.0))
        pm.apply_fill(filled_order("BTC/USD", OrderSide.SELL, 0.1, 55_000.0))
        stats = pm.stats()
        assert stats["closed_trades"] == 1
        assert stats["win_rate"] == 1.0
        assert stats["total_pnl"] == pytest.approx(498.0)  # net of 2.0 fees


class TestJournal:
    def test_round_trip(self, tmp_path):
        journal = TradeJournal(str(tmp_path / "test.db"))
        order = filled_order("BTC/USD", OrderSide.BUY, 0.1, 50_000.0, reason="signal:test")
        journal.record_order(order)
        order.fee = 3.0
        journal.record_order(order)  # upsert path

        trade = TradeRecord(
            symbol="BTC/USD",
            entry_price=50_000.0,
            exit_price=51_000.0,
            size=0.1,
            pnl=100.0,
            fees=2.0,
            strategy="ensemble",
            exit_reason="take_profit",
            opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            closed_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
        journal.record_trade(trade)
        journal.snapshot_equity(10_100.0, cash=5_000.0, open_positions=1)
        journal.log_event("info", "test event")

        recent = journal.recent_trades()
        assert len(recent) == 1
        assert recent[0]["pnl"] == pytest.approx(100.0)

        summary = journal.performance_summary()
        assert summary["trades"] == 1
        assert summary["total_pnl"] == pytest.approx(100.0)
        assert summary["win_rate"] == 1.0
        journal.close()
