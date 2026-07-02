"""Dashboard tests — real aiohttp server, real SQLite journal, real HTTP."""

from datetime import datetime, timezone

import pytest

from src.trading_bot.models import TradeRecord
from src.trading_bot.persistence.journal import TradeJournal
from src.trading_bot.web.dashboard import Dashboard


@pytest.fixture
async def dash(tmp_path):
    journal = TradeJournal(str(tmp_path / "dash.db"))
    journal.snapshot_equity(10_000.0, cash=10_000.0, open_positions=0)
    journal.snapshot_equity(10_150.0, cash=5_000.0, open_positions=1)
    journal.record_trade(
        TradeRecord(
            symbol="BTC/USD",
            entry_price=50_000.0,
            exit_price=51_500.0,
            size=0.1,
            pnl=150.0,
            fees=2.6,
            strategy="ensemble",
            exit_reason="take_profit",
            opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            closed_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
    )
    journal.log_event("info", "test event line")
    dashboard = Dashboard(journal=journal, port=18765)
    await dashboard.start()
    yield dashboard
    await dashboard.stop()
    journal.close()


async def _get(path, as_json=True):
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:18765{path}") as resp:
            assert resp.status == 200, path
            return await (resp.json() if as_json else resp.text())


async def test_index_serves_html(dash):
    html = await _get("/", as_json=False)
    assert "ULTIMATE TRADING BOT" in html
    assert "equityChart" in html


async def test_status_journal_mode(dash):
    status = await _get("/api/status")
    assert status["source"] == "journal"
    assert status["portfolio"]["trades"] == 1
    assert status["portfolio"]["win_rate"] == 1.0


async def test_equity_endpoint(dash):
    points = await _get("/api/equity")
    assert len(points) == 2
    assert points[0]["equity"] == pytest.approx(10_000.0)
    assert points[-1]["equity"] == pytest.approx(10_150.0)
    # Oldest-first ordering for charting.
    assert points[0]["timestamp"] <= points[-1]["timestamp"]


async def test_trades_endpoint(dash):
    trades = await _get("/api/trades")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "BTC/USD"
    assert trades[0]["pnl"] == pytest.approx(150.0)


async def test_events_endpoint(dash):
    events = await _get("/api/events")
    assert any("test event line" in e["message"] for e in events)
