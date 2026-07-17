"""Risk engine tests: sizing math, protections, and correlation caps —
exercised with real return series from the committed market data."""

from datetime import datetime, timedelta, timezone

import pytest

from src.trading_bot.models import Signal, SignalAction, TradeRecord
from src.trading_bot.risk.risk_manager import RiskManager

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def make_signal(symbol="BTC/USDT", price=100_000.0, stop=98_000.0, conf=0.8):
    return Signal(
        symbol=symbol,
        action=SignalAction.BUY,
        confidence=conf,
        price=price,
        strategy="test",
        stop_loss=stop,
    )


def make_trade(pnl, symbol="BTC/USDT"):
    return TradeRecord(
        symbol=symbol,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        size=1.0,
        pnl=pnl,
        fees=0.1,
        strategy="test",
        exit_reason="stop_loss" if pnl < 0 else "take_profit",
        opened_at=NOW - timedelta(hours=2),
        closed_at=NOW,
    )


def test_position_sizing_respects_risk_budget():
    rm = RiskManager({"risk_per_trade": 0.01})
    rm.update_equity(10_000.0, now=NOW)
    decision = rm.evaluate_entry(
        make_signal(price=100.0, stop=98.0, conf=1.0), 10_000.0, {}, now=NOW
    )
    assert decision.approved
    # Risk budget = 10_000 * 1% = 100; stop distance = 2 -> 50 units,
    # but notional cap (20% = 2000) limits it to 20 units.
    assert decision.quantity == pytest.approx(20.0)
    assert decision.stop_loss == pytest.approx(98.0)


def test_sizing_uses_atr_when_no_stop():
    rm = RiskManager({"risk_per_trade": 0.01, "max_position_pct": 1.0})
    rm.update_equity(10_000.0, now=NOW)
    signal = make_signal(price=100.0, stop=None, conf=1.0)
    decision = rm.evaluate_entry(signal, 10_000.0, {}, atr=1.0, now=NOW)
    assert decision.approved
    # Stop = price - 2*ATR = 98 -> same 50-unit budget, capped by heat:
    # max total notional = min(equity, 5%/1% * equity) = 10_000 -> 100 units.
    assert decision.quantity == pytest.approx(50.0)
    assert decision.stop_loss == pytest.approx(98.0)


def test_rejects_without_stop_or_atr():
    rm = RiskManager()
    rm.update_equity(10_000.0, now=NOW)
    decision = rm.evaluate_entry(
        make_signal(stop=None), 10_000.0, {}, atr=None, now=NOW
    )
    assert not decision.approved
    assert "no stop" in decision.reason


def test_daily_loss_halt_and_utc_rollover():
    rm = RiskManager({"max_daily_loss_pct": 0.03})
    rm.update_equity(10_000.0, now=NOW)
    rm.update_equity(9_600.0, now=NOW + timedelta(hours=1))  # -4% today
    gate = rm.trading_allowed(now=NOW + timedelta(hours=2))
    assert not gate.approved
    assert "daily loss" in gate.reason
    # Next UTC day the budget resets.
    next_day = NOW + timedelta(days=1)
    rm.update_equity(9_600.0, now=next_day)
    assert rm.trading_allowed(now=next_day).approved


def test_max_drawdown_halt():
    rm = RiskManager({"max_drawdown_pct": 0.15, "max_daily_loss_pct": 0.5})
    rm.update_equity(10_000.0, now=NOW)
    rm.update_equity(8_000.0, now=NOW + timedelta(days=2))  # -20% from peak
    gate = rm.trading_allowed(now=NOW + timedelta(days=2))
    assert not gate.approved
    assert "drawdown" in gate.reason


def test_stoploss_guard_halts_after_repeated_stops():
    rm = RiskManager({"stoploss_guard_count": 3, "max_consecutive_losses": 99})
    rm.update_equity(10_000.0, now=NOW)
    for i in range(3):
        rm.record_trade_result(
            make_trade(-50.0, symbol=f"S{i}/USD"),
            was_stop_loss=True,
            now=NOW + timedelta(minutes=i),
        )
    gate = rm.trading_allowed(now=NOW + timedelta(minutes=5))
    assert not gate.approved
    assert "stoploss guard" in gate.reason
    # Halt expires after the cooldown.
    later = NOW + timedelta(hours=13)
    assert rm.trading_allowed(now=later).approved


def test_consecutive_loss_halt():
    rm = RiskManager({"max_consecutive_losses": 3, "stoploss_guard_count": 99})
    rm.update_equity(10_000.0, now=NOW)
    for i in range(3):
        rm.record_trade_result(
            make_trade(-10.0, symbol=f"S{i}/USD"),
            was_stop_loss=False,
            now=NOW + timedelta(minutes=i),
        )
    assert not rm.trading_allowed(now=NOW + timedelta(minutes=5)).approved


def test_win_resets_consecutive_losses():
    rm = RiskManager({"max_consecutive_losses": 3, "stoploss_guard_count": 99})
    rm.update_equity(10_000.0, now=NOW)
    rm.record_trade_result(make_trade(-10.0), False, now=NOW)
    rm.record_trade_result(make_trade(-10.0), False, now=NOW)
    rm.record_trade_result(make_trade(+20.0), False, now=NOW)
    assert rm.state.consecutive_losses == 0
    assert rm.trading_allowed(now=NOW).approved


def test_reentry_cooldown_blocks_same_symbol():
    rm = RiskManager({"reentry_cooldown_minutes": 60})
    rm.update_equity(10_000.0, now=NOW)
    rm.record_trade_result(make_trade(+5.0), False, now=NOW)
    blocked = rm.evaluate_entry(make_signal(), 10_000.0, {}, now=NOW + timedelta(minutes=10))
    assert not blocked.approved
    assert "cooldown" in blocked.reason
    allowed = rm.evaluate_entry(make_signal(), 10_000.0, {}, now=NOW + timedelta(minutes=61))
    assert allowed.approved


def test_correlation_cap_with_real_returns(btc_df, eth_df):
    """BTC and ETH hourly returns are highly correlated in reality — the
    engine must detect that from the actual data and refuse the second
    entry when the threshold is strict."""
    rm = RiskManager({"correlation_threshold": 0.5})
    rm.update_equity(100_000.0, now=NOW)
    rm.update_returns("BTC/USDT", btc_df["close"])
    rm.update_returns("ETH/USDT", eth_df["close"])
    decision = rm.evaluate_entry(
        make_signal(symbol="ETH/USDT", price=3000.0, stop=2900.0),
        100_000.0,
        {"BTC/USDT": 5_000.0},
        now=NOW,
    )
    assert not decision.approved
    assert "correlation" in decision.reason
    # With a permissive threshold it passes.
    rm2 = RiskManager({"correlation_threshold": 0.999})
    rm2.update_equity(100_000.0, now=NOW)
    rm2.update_returns("BTC/USDT", btc_df["close"])
    rm2.update_returns("ETH/USDT", eth_df["close"])
    assert rm2.evaluate_entry(
        make_signal(symbol="ETH/USDT", price=3000.0, stop=2900.0),
        100_000.0,
        {"BTC/USDT": 5_000.0},
        now=NOW,
    ).approved


def test_max_open_positions():
    rm = RiskManager()
    rm.update_equity(10_000.0, now=NOW)
    open_positions = {"A/USD": 100.0, "B/USD": 100.0}
    decision = rm.evaluate_entry(
        make_signal(symbol="C/USD"), 10_000.0, open_positions,
        max_open_positions=2, now=NOW,
    )
    assert not decision.approved
    assert "max open positions" in decision.reason


def test_kelly_cap_shrinks_after_losing_history():
    rm = RiskManager({"kelly_fraction": 0.5})
    rm.update_equity(10_000.0, now=NOW)
    # 12 trades, mostly losers -> negative edge -> cap floors at 2%.
    for i in range(10):
        rm.record_trade_result(make_trade(-20.0, f"L{i}/USD"), False, now=NOW)
    for i in range(2):
        rm.record_trade_result(make_trade(+10.0, f"W{i}/USD"), False, now=NOW)
    assert rm._kelly_cap() == pytest.approx(0.02)


def test_already_holding_rejected():
    rm = RiskManager()
    rm.update_equity(10_000.0, now=NOW)
    decision = rm.evaluate_entry(
        make_signal(symbol="BTC/USDT"), 10_000.0, {"BTC/USDT": 500.0}, now=NOW
    )
    assert not decision.approved
    assert "already holding" in decision.reason


def test_invalid_price_or_equity_rejected():
    rm = RiskManager()
    rm.update_equity(10_000.0, now=NOW)
    assert not rm.evaluate_entry(
        make_signal(price=0.0, stop=None), 10_000.0, {}, now=NOW
    ).approved
    assert not rm.evaluate_entry(make_signal(), 0.0, {}, now=NOW).approved


def test_min_notional_rejects_dust_after_caps():
    """The dust-order gate: caps can shrink a valid signal below the
    exchange minimum — that order must never reach the venue."""
    rm = RiskManager()  # min_notional default 10.0
    rm.update_equity(40.0, now=NOW)
    decision = rm.evaluate_entry(
        make_signal(price=100.0, stop=98.0, conf=1.0), 40.0, {}, now=NOW
    )
    assert not decision.approved
    assert "too small after caps" in decision.reason


def test_kelly_cap_maxes_on_all_win_history():
    rm = RiskManager({"max_position_pct": 0.20})
    rm.update_equity(10_000.0, now=NOW)
    for i in range(10):
        rm.record_trade_result(make_trade(+20.0, f"W{i}/USD"), False, now=NOW)
    assert rm._kelly_cap() == pytest.approx(0.20)


def test_metrics_reports_live_state():
    """core.py calls metrics() every maintenance cycle and the dashboard
    renders it — it was never executed by any test."""
    rm = RiskManager()
    rm.update_equity(10_000.0, now=NOW)
    rm.update_equity(9_000.0, now=NOW)
    rm.record_trade_result(make_trade(-100.0), True, now=NOW)
    m = rm.metrics()
    assert m["equity"] == pytest.approx(9_000.0)
    assert m["peak_equity"] == pytest.approx(10_000.0)
    assert m["drawdown"] == pytest.approx(0.1)
    assert m["consecutive_losses"] == 1
    assert m["halted"] is False
    assert 0.0 < m["kelly_cap"] <= 1.0
