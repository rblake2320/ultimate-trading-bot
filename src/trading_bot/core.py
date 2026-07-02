"""Core trading engine.

Orchestrates: market data -> prediction engine -> risk engine -> execution
-> portfolio/journal, with layered safety controls:

  - Paper mode is the default. Live mode requires BOTH ``mode: "live"`` in
    the config AND the environment variable ``TRADING_BOT_LIVE=YES`` — a
    two-key launch so a config typo can't trade real money.
  - Kill switch: creating a file named ``KILL`` next to the bot (or the
    path in config ``kill_file``) triggers an emergency shutdown.
  - The risk engine can halt entries at any time (daily loss, drawdown,
    stoploss guard, consecutive losses); exits are never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .data.market_data_manager import MarketDataManager
from .exchanges.exchange_manager import ExchangeManager
from .execution.brokers import LiveBroker, OrderManager, PaperBroker
from .ml.prediction_engine import PredictionEngine
from .models import OrderSide, OrderStatus, OrderType, SignalAction
from .notifications.notification_manager import NotificationManager
from .persistence.journal import TradeJournal
from .portfolio.portfolio_manager import PortfolioManager
from .risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)

LIVE_ENV_VAR = "TRADING_BOT_LIVE"
LIVE_ENV_VALUE = "YES"


class TradingBot:
    def __init__(self, config: Dict):
        self.config = config
        self.mode = config.get("mode", "paper")
        trading = config.get("trading", {})
        self.symbols = list(trading.get("symbols", ["BTC/USD"]))
        self.timeframe = trading.get("timeframe", "1h")
        self.poll_seconds = float(trading.get("poll_seconds", 30))
        self.price_check_seconds = float(trading.get("price_check_seconds", 10))
        self.max_open_positions = int(trading.get("max_open_positions", 4))
        self.trailing_stop_pct = float(trading.get("trailing_stop_pct", 0.0))
        self.quote_currency = trading.get("quote_currency", "USD")
        self.kill_file = Path(config.get("kill_file", "KILL"))

        self.is_running = False
        self._last_candle_ts: Dict[str, Optional[pd.Timestamp]] = {
            s: None for s in self.symbols
        }

        # Components are wired in setup() because live/paper differ.
        self.exchange_manager: Optional[ExchangeManager] = None
        self.data: Optional[MarketDataManager] = None
        self.orders: Optional[OrderManager] = None
        self.portfolio: Optional[PortfolioManager] = None
        self.risk = RiskManager(config.get("risk"))
        self.engine = PredictionEngine(config.get("signals"))
        self.notify = NotificationManager(config.get("notifications"))
        self.journal = TradeJournal(
            config.get("journal", {}).get("db_path", "data/trading_bot.db")
        )

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #

    async def setup(self) -> None:
        exchange_cfg = self.config.get("exchange", {})
        exchange_id = exchange_cfg.get("id", "kraken")

        if self.mode == "live":
            if os.getenv(LIVE_ENV_VAR) != LIVE_ENV_VALUE:
                raise RuntimeError(
                    f"Live mode requires the environment variable "
                    f"{LIVE_ENV_VAR}={LIVE_ENV_VALUE}. Refusing to start. "
                    f"(Run in paper mode first — it uses real market data.)"
                )
            self.exchange_manager = ExchangeManager(
                {exchange_id: {**exchange_cfg, "enabled": True}}
            )
            await self.exchange_manager.initialize()
            exchange = self.exchange_manager.primary
            self.data = MarketDataManager(
                exchange_id=exchange_id,
                timeframe=self.timeframe,
                exchange=exchange,
            )
            await self.data.initialize()
            broker = LiveBroker(exchange)
            balances = await broker.fetch_balances()
            starting_cash = balances.get(self.quote_currency, 0.0)
            logger.warning(
                "LIVE TRADING ENABLED on %s — %s balance: %.2f %s",
                exchange_id,
                self.quote_currency,
                starting_cash,
                self.quote_currency,
            )
        else:
            self.data = MarketDataManager(
                exchange_id=exchange_id, timeframe=self.timeframe
            )
            await self.data.initialize()
            starting_cash = float(
                self.config.get("trading", {}).get("paper_starting_cash", 10_000.0)
            )
            broker = PaperBroker(
                self.data,
                starting_balances={self.quote_currency: starting_cash},
                taker_fee=float(exchange_cfg.get("taker_fee", 0.0026)),
                maker_fee=float(exchange_cfg.get("maker_fee", 0.0016)),
            )
            logger.info(
                "Paper trading on %s live data with %.2f %s virtual balance",
                exchange_id,
                starting_cash,
                self.quote_currency,
            )

        self.orders = OrderManager(
            broker,
            stale_after_seconds=float(
                self.config.get("trading", {}).get("stale_order_seconds", 300)
            ),
        )
        self.portfolio = PortfolioManager(
            quote_currency=self.quote_currency, starting_cash=starting_cash
        )
        self.risk.update_equity(starting_cash)
        self.journal.log_event(
            "info", f"bot started mode={self.mode} symbols={self.symbols}"
        )

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        await self.setup()
        self.is_running = True
        await self.notify.send(
            f"Trading bot started ({self.mode}) — {', '.join(self.symbols)} "
            f"on {self.timeframe} candles. Channels: {self.notify.channels}"
        )
        tasks = [
            asyncio.create_task(self._candle_loop(), name="candles"),
            asyncio.create_task(self._position_loop(), name="positions"),
            asyncio.create_task(self._maintenance_loop(), name="maintenance"),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.critical("Fatal error in trading loop: %s", exc, exc_info=True)
            await self.emergency_shutdown(f"fatal error: {exc}")
            raise
        finally:
            self.is_running = False
            for task in tasks:
                task.cancel()
            await self._cleanup()

    async def stop(self) -> None:
        self.is_running = False

    async def _cleanup(self) -> None:
        try:
            if self.orders:
                await self.orders.cancel_all()
        finally:
            if self.data:
                await self.data.close()
            if self.exchange_manager:
                await self.exchange_manager.close_all()
            self.journal.log_event("info", "bot stopped")
            self.journal.close()

    # ------------------------------------------------------------------ #
    # Loops
    # ------------------------------------------------------------------ #

    async def _candle_loop(self) -> None:
        """Acts once per newly closed candle per symbol."""
        while self.is_running:
            try:
                fresh: Dict[str, pd.DataFrame] = {}
                for symbol in self.symbols:
                    df = await self.data.get_candles(symbol)
                    if df.empty:
                        continue
                    last_ts = df.index[-1]
                    if self._last_candle_ts[symbol] != last_ts:
                        self._last_candle_ts[symbol] = last_ts
                        fresh[symbol] = df
                        self.risk.update_returns(symbol, df["close"])
                if fresh:
                    signals = await self.engine.get_signals(fresh)
                    for signal in signals:
                        await self._handle_signal(signal, fresh[signal.symbol])
            except Exception as exc:  # noqa: BLE001
                logger.error("Candle loop error: %s", exc, exc_info=True)
            await asyncio.sleep(self.poll_seconds)

    async def _position_loop(self) -> None:
        """Stop-loss / take-profit / trailing-stop checks on live prices,
        plus open-order polling."""
        while self.is_running:
            try:
                if self.kill_file.exists():
                    await self.emergency_shutdown("kill file detected")
                    return

                filled = await self.orders.poll_open_orders()
                for order in filled:
                    self._apply_fill(order)

                for symbol in list(self.portfolio.positions):
                    position = self.portfolio.positions[symbol]
                    price = await self.data.get_current_price(symbol)
                    position.high_water_mark = max(
                        position.high_water_mark, price
                    )
                    if self.trailing_stop_pct > 0:
                        trailed = position.high_water_mark * (
                            1 - self.trailing_stop_pct
                        )
                        if position.stop_loss is None or trailed > position.stop_loss:
                            position.stop_loss = trailed
                    if position.stop_loss is not None and price <= position.stop_loss:
                        await self._close_position(symbol, "stop_loss")
                    elif (
                        position.take_profit is not None
                        and price >= position.take_profit
                    ):
                        await self._close_position(symbol, "take_profit")
            except Exception as exc:  # noqa: BLE001
                logger.error("Position loop error: %s", exc, exc_info=True)
            await asyncio.sleep(self.price_check_seconds)

    async def _maintenance_loop(self) -> None:
        """Equity snapshots, health checks, risk-state logging."""
        while self.is_running:
            try:
                prices = {}
                for symbol in list(self.portfolio.positions):
                    prices[symbol] = await self.data.get_current_price(symbol)
                equity = self.portfolio.equity(prices)
                self.risk.update_equity(equity)
                self.journal.snapshot_equity(
                    equity,
                    cash=self.portfolio.cash,
                    open_positions=len(self.portfolio.positions),
                    details=self.risk.metrics(),
                )
                healthy = await self.data.health_check()
                if not healthy:
                    await self.notify.alert("Market data health check failed")
                logger.info(
                    "equity=%.2f cash=%.2f positions=%d daily_pnl=%.2f",
                    equity,
                    self.portfolio.cash,
                    len(self.portfolio.positions),
                    self.risk.state.daily_pnl,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Maintenance loop error: %s", exc)
            await asyncio.sleep(300)

    # ------------------------------------------------------------------ #
    # Trade handling
    # ------------------------------------------------------------------ #

    async def _handle_signal(self, signal, df: pd.DataFrame) -> None:
        if signal.action == SignalAction.BUY:
            prices = {
                s: await self.data.get_current_price(s)
                for s in self.portfolio.positions
            }
            equity = self.portfolio.equity(prices)
            decision = self.risk.evaluate_entry(
                signal,
                equity,
                self.portfolio.open_notionals(prices),
                atr=self.engine.latest_atr(df),
                max_open_positions=self.max_open_positions,
            )
            if not decision.approved:
                logger.info("Entry rejected [%s]: %s", signal.symbol, decision.reason)
                return
            order = await self.orders.submit(
                signal.symbol,
                OrderSide.BUY,
                decision.quantity,
                OrderType.MARKET,
                reason=f"signal:{signal.strategy}",
            )
            self.journal.record_order(order)
            if order.status == OrderStatus.FILLED:
                self._apply_fill(order, stop=decision.stop_loss, target=decision.take_profit)
                await self.notify.trade(
                    f"BUY {signal.symbol} {order.filled:.6f} @ "
                    f"{order.average_price:.2f} "
                    f"(conf {signal.confidence:.2f}, "
                    f"regime {signal.regime.value if signal.regime else '?'}, "
                    f"SL {decision.stop_loss:.2f})"
                )
        elif signal.action in (SignalAction.SELL, SignalAction.CLOSE):
            if signal.symbol in self.portfolio.positions:
                await self._close_position(signal.symbol, f"signal:{signal.strategy}")

    def _apply_fill(self, order, stop=None, target=None) -> None:
        record = self.portfolio.apply_fill(order, strategy=order.reason)
        self.journal.record_order(order)
        if order.side == OrderSide.BUY and order.symbol in self.portfolio.positions:
            position = self.portfolio.positions[order.symbol]
            if stop is not None:
                position.stop_loss = stop
            if target is not None:
                position.take_profit = target
        if record is not None:
            self.journal.record_trade(record)
            self.risk.record_trade_result(
                record, was_stop_loss="stop_loss" in record.exit_reason
            )

    async def _close_position(self, symbol: str, reason: str) -> None:
        position = self.portfolio.positions.get(symbol)
        if position is None:
            return
        order = await self.orders.submit(
            symbol,
            OrderSide.SELL,
            position.size,
            OrderType.MARKET,
            reason=reason,
        )
        if order.status == OrderStatus.FILLED:
            self._apply_fill(order)
            pnl_msg = ""
            if self.portfolio.closed_trades:
                last = self.portfolio.closed_trades[-1]
                if last.symbol == symbol:
                    pnl_msg = f" pnl={last.pnl:+.2f}"
            await self.notify.trade(f"CLOSE {symbol} ({reason}){pnl_msg}")
        else:
            await self.notify.alert(
                f"Failed to close {symbol}: order {order.status.value} — {order.reason}"
            )

    # ------------------------------------------------------------------ #
    # Emergency
    # ------------------------------------------------------------------ #

    async def emergency_shutdown(self, reason: str) -> None:
        logger.critical("EMERGENCY SHUTDOWN: %s", reason)
        self.journal.log_event("critical", f"emergency shutdown: {reason}")
        try:
            await self.orders.cancel_all()
            if self.config.get("trading", {}).get("emergency_close_positions", True):
                for symbol in list(self.portfolio.positions):
                    await self._close_position(symbol, "emergency")
            await self.notify.emergency(f"Bot shut down: {reason}")
        finally:
            self.is_running = False

    # ------------------------------------------------------------------ #

    async def status(self) -> Dict:
        prices = {}
        for symbol in list(self.portfolio.positions):
            prices[symbol] = await self.data.get_current_price(symbol)
        return {
            "mode": self.mode,
            "running": self.is_running,
            "equity": self.portfolio.equity(prices),
            "cash": self.portfolio.cash,
            "positions": {
                s: {
                    "size": p.size,
                    "entry": p.entry_price,
                    "stop": p.stop_loss,
                    "target": p.take_profit,
                    "unrealized": p.unrealized_pnl(prices.get(s, p.entry_price)),
                }
                for s, p in self.portfolio.positions.items()
            },
            "risk": self.risk.metrics(),
            "orders": self.orders.stats(),
            "portfolio": self.portfolio.stats(),
        }
