"""
Core Trading Bot System Architecture
Main trading bot class that orchestrates all components
"""

import asyncio
import logging
from typing import Dict
from datetime import datetime

from .exchanges.exchange_manager import ExchangeManager
from .data.market_data_manager import MarketDataManager
from .ml.prediction_engine import PredictionEngine
from .risk.risk_manager import RiskManager
from .execution.order_manager import OrderManager
from .portfolio.portfolio_manager import PortfolioManager
from .notifications.notification_manager import NotificationManager


class TradingBot:
    """
    Main trading bot class that coordinates all trading operations
    """

    def __init__(self, config: Dict):
        """Initialize the trading bot with configuration"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.is_running = False
        self.start_time = None

        # Initialize core components
        self.exchange_manager = ExchangeManager(config["exchanges"])
        self.market_data_manager = MarketDataManager(config["data_sources"])
        self.prediction_engine = PredictionEngine(config["ml_models"])
        self.risk_manager = RiskManager(config["risk_management"])
        self.order_manager = OrderManager(config["trading"])
        self.portfolio_manager = PortfolioManager(config["trading"])
        self.notification_manager = NotificationManager(config["notifications"])

        # Trading state
        self.active_positions = {}
        self.pending_orders = {}
        self.daily_pnl = 0.0
        self.total_pnl = 0.0

    async def initialize(self):
        """Initialize all components and connections"""
        self.logger.info("Initializing trading bot...")

        try:
            # Initialize exchanges
            await self.exchange_manager.initialize()

            # Initialize market data feeds
            await self.market_data_manager.initialize()

            # Load ML models
            await self.prediction_engine.initialize()

            # Initialize risk management
            await self.risk_manager.initialize()

            # Initialize portfolio tracking
            await self.portfolio_manager.initialize()

            self.logger.info("Trading bot initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize trading bot: {e}")
            return False

    async def run(self):
        """Main trading loop"""
        if not await self.initialize():
            self.logger.error("Failed to initialize. Exiting.")
            return

        self.is_running = True
        self.start_time = datetime.now()

        self.logger.info("Starting trading bot main loop...")

        try:
            # Start background tasks
            tasks = [
                asyncio.create_task(self._market_data_loop()),
                asyncio.create_task(self._trading_loop()),
                asyncio.create_task(self._risk_monitoring_loop()),
                asyncio.create_task(self._portfolio_update_loop()),
                asyncio.create_task(self._health_check_loop()),
            ]

            # Wait for all tasks
            await asyncio.gather(*tasks)

        except Exception as e:
            self.logger.error(f"Trading bot crashed: {e}")
            await self.emergency_shutdown()

        finally:
            self.is_running = False

    async def _market_data_loop(self):
        """Continuous market data processing"""
        while self.is_running:
            try:
                # Get latest market data
                market_data = await self.market_data_manager.get_latest_data()

                # Update prediction models with new data
                await self.prediction_engine.update_data(market_data)

                # Update risk calculations
                await self.risk_manager.update_market_data(market_data)

                await asyncio.sleep(1)  # 1 second update frequency

            except Exception as e:
                self.logger.error(f"Error in market data loop: {e}")
                await asyncio.sleep(5)

    async def _trading_loop(self):
        """Main trading decision loop"""
        while self.is_running:
            try:
                # Get trading signals from ML models
                signals = await self.prediction_engine.get_signals()

                # Process each signal
                for signal in signals:
                    await self._process_trading_signal(signal)

                # Check existing positions
                await self._manage_existing_positions()

                await asyncio.sleep(10)  # 10 second trading frequency

            except Exception as e:
                self.logger.error(f"Error in trading loop: {e}")
                await asyncio.sleep(30)

    async def _process_trading_signal(self, signal: Dict):
        """Process a trading signal from the ML models"""
        try:
            symbol = signal["symbol"]
            action = signal["action"]  # 'buy', 'sell', 'hold'
            confidence = signal["confidence"]
            price = signal["price"]

            # Check if signal meets confidence threshold
            if confidence < self.config["ml_models"]["ensemble_threshold"]:
                return

            # Risk assessment
            risk_assessment = await self.risk_manager.assess_trade(signal)
            if not risk_assessment["approved"]:
                self.logger.info(
                    f"Trade rejected by risk manager: {risk_assessment['reason']}"
                )
                return

            # Calculate position size
            position_size = await self.risk_manager.calculate_position_size(signal)

            # Execute trade
            if action == "buy":
                await self._execute_buy_order(symbol, position_size, price)
            elif action == "sell":
                await self._execute_sell_order(symbol, position_size, price)

        except Exception as e:
            self.logger.error(f"Error processing trading signal: {e}")

    async def _execute_buy_order(self, symbol: str, size: float, price: float):
        """Execute a buy order"""
        try:
            order = await self.order_manager.place_buy_order(
                symbol=symbol, size=size, price=price, order_type="limit"
            )

            if order:
                self.pending_orders[order["id"]] = order
                self.logger.info(f"Buy order placed: {symbol} {size} @ {price}")

                # Send notification
                await self.notification_manager.send_trade_notification(
                    f"Buy order placed: {symbol} {size} @ {price}"
                )

        except Exception as e:
            self.logger.error(f"Error executing buy order: {e}")

    async def _execute_sell_order(self, symbol: str, size: float, price: float):
        """Execute a sell order"""
        try:
            order = await self.order_manager.place_sell_order(
                symbol=symbol, size=size, price=price, order_type="limit"
            )

            if order:
                self.pending_orders[order["id"]] = order
                self.logger.info(f"Sell order placed: {symbol} {size} @ {price}")

                # Send notification
                await self.notification_manager.send_trade_notification(
                    f"Sell order placed: {symbol} {size} @ {price}"
                )

        except Exception as e:
            self.logger.error(f"Error executing sell order: {e}")

    async def _manage_existing_positions(self):
        """Manage existing positions and orders"""
        try:
            # Check pending orders
            for order_id, order in list(self.pending_orders.items()):
                status = await self.order_manager.get_order_status(order_id)

                if status["status"] == "filled":
                    # Order filled, update positions
                    await self._handle_filled_order(order, status)
                    del self.pending_orders[order_id]

                elif status["status"] == "cancelled":
                    # Order cancelled
                    del self.pending_orders[order_id]

            # Check stop losses and take profits
            for symbol, position in self.active_positions.items():
                await self._check_position_exits(symbol, position)

        except Exception as e:
            self.logger.error(f"Error managing positions: {e}")

    async def _handle_filled_order(self, order: Dict, status: Dict):
        """Handle a filled order"""
        try:
            symbol = order["symbol"]
            side = order["side"]
            size = status["filled_size"]
            price = status["average_price"]

            if side == "buy":
                # Add to positions
                if symbol in self.active_positions:
                    # Average down
                    existing = self.active_positions[symbol]
                    total_size = existing["size"] + size
                    avg_price = (
                        existing["size"] * existing["entry_price"] + size * price
                    ) / total_size

                    self.active_positions[symbol] = {
                        "size": total_size,
                        "entry_price": avg_price,
                        "side": "long",
                        "timestamp": datetime.now(),
                    }
                else:
                    self.active_positions[symbol] = {
                        "size": size,
                        "entry_price": price,
                        "side": "long",
                        "timestamp": datetime.now(),
                    }

            elif side == "sell":
                # Reduce or close position
                if symbol in self.active_positions:
                    existing = self.active_positions[symbol]
                    if existing["size"] <= size:
                        # Close position
                        pnl = (price - existing["entry_price"]) * existing["size"]
                        self.daily_pnl += pnl
                        self.total_pnl += pnl
                        del self.active_positions[symbol]

                        self.logger.info(f"Position closed: {symbol} PnL: {pnl:.2f}")
                    else:
                        # Partial close
                        self.active_positions[symbol]["size"] -= size
                        pnl = (price - existing["entry_price"]) * size
                        self.daily_pnl += pnl
                        self.total_pnl += pnl

            # Update portfolio
            await self.portfolio_manager.update_position(
                symbol, self.active_positions.get(symbol)
            )

        except Exception as e:
            self.logger.error(f"Error handling filled order: {e}")

    async def _check_position_exits(self, symbol: str, position: Dict):
        """Check if position should be closed (stop loss/take profit)"""
        try:
            current_price = await self.market_data_manager.get_current_price(symbol)
            entry_price = position["entry_price"]

            # Calculate P&L percentage
            pnl_pct = (current_price - entry_price) / entry_price

            # Check stop loss
            stop_loss_pct = self.config["trading"]["stop_loss_percentage"]
            if pnl_pct <= -stop_loss_pct:
                await self._close_position(symbol, position, "stop_loss")
                return

            # Check take profit
            take_profit_pct = self.config["trading"]["take_profit_percentage"]
            if pnl_pct >= take_profit_pct:
                await self._close_position(symbol, position, "take_profit")
                return

        except Exception as e:
            self.logger.error(f"Error checking position exits: {e}")

    async def _close_position(self, symbol: str, position: Dict, reason: str):
        """Close a position"""
        try:
            current_price = await self.market_data_manager.get_current_price(symbol)

            order = await self.order_manager.place_sell_order(
                symbol=symbol,
                size=position["size"],
                price=current_price,
                order_type="market",
            )

            if order:
                self.logger.info(f"Position closed: {symbol} Reason: {reason}")

                # Send notification
                await self.notification_manager.send_trade_notification(
                    f"Position closed: {symbol} Reason: {reason}"
                )

        except Exception as e:
            self.logger.error(f"Error closing position: {e}")

    async def _risk_monitoring_loop(self):
        """Continuous risk monitoring"""
        while self.is_running:
            try:
                # Check daily loss limits
                max_daily_loss = self.config["trading"]["max_daily_loss"]
                if self.daily_pnl <= -max_daily_loss:
                    await self.emergency_shutdown("Daily loss limit exceeded")
                    break

                # Check portfolio risk
                portfolio_risk = await self.risk_manager.calculate_portfolio_risk()
                max_portfolio_risk = self.config["risk_management"][
                    "max_portfolio_risk"
                ]

                if portfolio_risk > max_portfolio_risk:
                    await self._reduce_portfolio_risk()

                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                self.logger.error(f"Error in risk monitoring: {e}")
                await asyncio.sleep(60)

    async def _portfolio_update_loop(self):
        """Update portfolio metrics"""
        while self.is_running:
            try:
                await self.portfolio_manager.update_metrics()
                await asyncio.sleep(300)  # Update every 5 minutes

            except Exception as e:
                self.logger.error(f"Error updating portfolio: {e}")
                await asyncio.sleep(300)

    async def _health_check_loop(self):
        """System health monitoring"""
        while self.is_running:
            try:
                # Check exchange connections
                await self.exchange_manager.health_check()

                # Check data feeds
                await self.market_data_manager.health_check()

                # Log system status
                uptime = datetime.now() - self.start_time
                self.logger.info(f"System healthy. Uptime: {uptime}")

                await asyncio.sleep(300)  # Check every 5 minutes

            except Exception as e:
                self.logger.error(f"Health check failed: {e}")
                await asyncio.sleep(60)

    async def emergency_shutdown(self, reason: str = "Emergency shutdown"):
        """Emergency shutdown procedure"""
        self.logger.critical(f"EMERGENCY SHUTDOWN: {reason}")

        try:
            # Cancel all pending orders
            for order_id in self.pending_orders:
                await self.order_manager.cancel_order(order_id)

            # Close all positions (optional - depends on strategy)
            # for symbol, position in self.active_positions.items():
            #     await self._close_position(symbol, position, 'emergency')

            # Send emergency notification
            await self.notification_manager.send_emergency_notification(
                f"Trading bot emergency shutdown: {reason}"
            )

            self.is_running = False

        except Exception as e:
            self.logger.error(f"Error during emergency shutdown: {e}")

    async def get_status(self) -> Dict:
        """Get current bot status"""
        return {
            "is_running": self.is_running,
            "uptime": (
                str(datetime.now() - self.start_time) if self.start_time else None
            ),
            "active_positions": len(self.active_positions),
            "pending_orders": len(self.pending_orders),
            "daily_pnl": self.daily_pnl,
            "total_pnl": self.total_pnl,
            "portfolio_value": await self.portfolio_manager.get_total_value(),
        }
