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
from typing import Dict, Optional, Tuple

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
        self._market_cache: list = []
        # Protective levels for entry orders that fill asynchronously (via the
        # poll loop) — without this, poll-filled positions would run stopless.
        self._pending_protection: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        # symbol -> order id of an in-flight close, so the stop check can't
        # re-fire a second full-size sell while the first is still working.
        self._closing: Dict[str, str] = {}

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
        self.dashboard = None

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
            if exchange_id == "robinhood":
                # Robinhood Crypto: native adapter (not in ccxt). It has no
                # candle API, so market data streams from a ccxt venue.
                from .exchanges.robinhood_crypto import RobinhoodCryptoBroker

                broker = RobinhoodCryptoBroker(
                    api_key=os.getenv("ROBINHOOD_API_KEY", ""),
                    private_key_b64=os.getenv("ROBINHOOD_PRIVATE_KEY", ""),
                )
                data_id = exchange_cfg.get("data_id", "kraken")
                self.data = MarketDataManager(
                    exchange_id=data_id, timeframe=self.timeframe
                )
                await self.data.initialize()
            else:
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

        dash_cfg = self.config.get("dashboard", {})
        if dash_cfg.get("enabled", True):
            from .web.dashboard import Dashboard

            self.dashboard = Dashboard(
                journal=self.journal,
                bot=self,
                host=dash_cfg.get("host", "127.0.0.1"),
                port=int(dash_cfg.get("port", 8899)),
            )
            try:
                url = await self.dashboard.start()
                logger.info("Dashboard: %s", url)
            except OSError as exc:
                logger.warning("Dashboard failed to start (port busy?): %s", exc)
                self.dashboard = None

        await self.notify.send(
            f"Trading bot started ({self.mode}) — {', '.join(self.symbols)} "
            f"on {self.timeframe} candles. Channels: {self.notify.channels}"
        )
        tasks = [
            asyncio.create_task(self._candle_loop(), name="candles"),
            asyncio.create_task(self._position_loop(), name="positions"),
            asyncio.create_task(self._maintenance_loop(), name="maintenance"),
            asyncio.create_task(self._market_loop(), name="market"),
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
            if self.dashboard:
                await self.dashboard.stop()
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

    async def _poll_and_apply_fills(self) -> None:
        """Sync open orders and book any fills, attaching the protective
        levels remembered at submission time."""
        filled = await self.orders.poll_open_orders()
        for order in filled:
            stop, target = self._pending_protection.pop(order.id, (None, None))
            if self._closing.get(order.symbol) == order.id:
                del self._closing[order.symbol]
            self._apply_fill(order, stop=stop, target=target)
        # Orders that died without filling (cancelled/rejected/stale)
        # take their bookkeeping with them.
        active = set(self.orders.orders)
        self._pending_protection = {
            k: v for k, v in self._pending_protection.items() if k in active
        }
        self._closing = {s: oid for s, oid in self._closing.items() if oid in active}

    async def _position_loop(self) -> None:
        """Stop-loss / take-profit / trailing-stop checks on live prices,
        plus open-order polling."""
        while self.is_running:
            try:
                if self.kill_file.exists():
                    await self.emergency_shutdown("kill file detected")
                    return

                await self._poll_and_apply_fills()

                prices = await self.data.get_current_prices(
                    list(self.portfolio.positions)
                )
                for symbol in list(self.portfolio.positions):
                    try:
                        position = self.portfolio.positions.get(symbol)
                        if position is None or symbol in self._closing:
                            continue
                        price = prices.get(symbol)
                        if price is None:
                            continue  # isolated fetch failure — next cycle retries
                        position.high_water_mark = max(
                            position.high_water_mark, price
                        )
                        if self.trailing_stop_pct > 0:
                            trailed = position.high_water_mark * (
                                1 - self.trailing_stop_pct
                            )
                            if (
                                position.stop_loss is None
                                or trailed > position.stop_loss
                            ):
                                position.stop_loss = trailed
                        if (
                            position.stop_loss is not None
                            and price <= position.stop_loss
                        ):
                            await self._close_position(symbol, "stop_loss")
                        elif (
                            position.take_profit is not None
                            and price >= position.take_profit
                        ):
                            await self._close_position(symbol, "take_profit")
                    except Exception as exc:  # noqa: BLE001 - one symbol must not starve the rest
                        logger.error(
                            "Position check failed for %s: %s",
                            symbol,
                            exc,
                            exc_info=True,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.error("Position loop error: %s", exc, exc_info=True)
            await asyncio.sleep(self.price_check_seconds)

    async def _maintenance_loop(self) -> None:
        """Equity snapshots, health checks, risk-state logging."""
        while self.is_running:
            try:
                # equity() marks symbols missing from the dict at entry price,
                # so an isolated ticker failure degrades gracefully.
                prices = await self.data.get_current_prices(
                    list(self.portfolio.positions)
                )
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
            prices = await self.data.get_current_prices(
                list(self.portfolio.positions)
            )
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
            if order.status == OrderStatus.FILLED:
                self._apply_fill(order, stop=decision.stop_loss, target=decision.take_profit)
                await self.notify.trade(
                    f"BUY {signal.symbol} {order.filled:.6f} @ "
                    f"{order.average_price:.2f} "
                    f"(conf {signal.confidence:.2f}, "
                    f"regime {signal.regime.value if signal.regime else '?'}, "
                    f"SL {decision.stop_loss:.2f})"
                )
            else:
                self.journal.record_order(order)
                if not order.is_closed:
                    # Fill will arrive via the poll loop — keep the protective
                    # levels so the position never runs stopless.
                    self._pending_protection[order.id] = (
                        decision.stop_loss,
                        decision.take_profit,
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
        if symbol in self._closing:
            return  # a close order is already working — never sell twice
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
        elif order.is_closed:  # rejected or cancelled — position still open
            await self.notify.alert(
                f"Failed to close {symbol}: order {order.status.value} — {order.reason}"
            )
        else:
            # Working asynchronously: the poll loop applies the fill and
            # clears this guard.
            self._closing[symbol] = order.id
            self.journal.record_order(order)

    # ------------------------------------------------------------------ #
    # Emergency
    # ------------------------------------------------------------------ #

    async def emergency_shutdown(self, reason: str) -> None:
        logger.critical("EMERGENCY SHUTDOWN: %s", reason)
        self.journal.log_event("critical", f"emergency shutdown: {reason}")
        try:
            await self.orders.cancel_all()
            self._closing.clear()  # cancelled close orders will never fill
            if self.config.get("trading", {}).get("emergency_close_positions", True):
                for symbol in list(self.portfolio.positions):
                    try:
                        await self._close_position(symbol, "emergency")
                    except Exception as exc:  # noqa: BLE001 - close the REST even if one fails
                        logger.critical(
                            "Emergency close failed for %s: %s", symbol, exc
                        )
            await self.notify.emergency(f"Bot shut down: {reason}")
        finally:
            self.is_running = False

    # ------------------------------------------------------------------ #

    async def _market_loop(self) -> None:
        """Continuously refresh the dashboard's market snapshot in the
        background so /api/market answers instantly from cache instead of
        queueing behind the exchange rate limiter."""
        from .strategies.base import infer_regime

        while self.is_running:
            try:
                summaries = await self.data.get_ticker_summaries(self.symbols)
                for summary in summaries:
                    cached = self.data.cached_candles(summary["symbol"])
                    if cached is not None and len(cached) > 50:
                        try:
                            summary["regime"] = infer_regime(cached).value
                        except Exception:  # noqa: BLE001
                            summary["regime"] = None
                    summary["exchange"] = self.data.exchange_id
                    summary["timeframe"] = self.timeframe
                self._market_cache = summaries
            except Exception as exc:  # noqa: BLE001
                logger.warning("Market snapshot refresh failed: %s", exc)
            await asyncio.sleep(5)

    async def market_snapshot(self) -> list:
        """Latest cached per-symbol market view (refreshed by _market_loop)."""
        return self._market_cache

    async def status(self) -> Dict:
        prices = await self.data.get_current_prices(list(self.portfolio.positions))
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
