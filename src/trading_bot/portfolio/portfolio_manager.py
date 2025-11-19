"""
Portfolio Manager - Tracks and manages trading portfolio
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict


class PortfolioManager:
    """
    Manages portfolio positions, balance, and performance tracking
    """

    def __init__(self, trading_config: Dict):
        """
        Initialize portfolio manager

        Args:
            trading_config: Trading configuration
        """
        self.trading_config = trading_config
        self.logger = logging.getLogger(__name__)

        # Portfolio state
        self.positions = {}
        self.balances = {}
        self.equity_history = []

        # Performance metrics
        self.total_equity = 100000.0  # Starting capital
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0

        # Trade history
        self.trade_history = []
        self.daily_pnl = defaultdict(float)

        # Configuration
        self.base_currency = trading_config.get("base_currency", "USDT")

    async def initialize(self) -> bool:
        """
        Initialize portfolio manager

        Returns:
            True if successful, False otherwise
        """
        self.logger.info("Initializing portfolio manager...")

        try:
            # Initialize starting balance
            self.balances[self.base_currency] = self.total_equity

            # Load existing positions if any
            await self._load_positions()

            self.logger.info("Portfolio manager initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize portfolio manager: {e}")
            return False

    async def _load_positions(self):
        """Load existing positions from database/storage"""
        try:
            # Placeholder - in production, load from database
            self.logger.info("Positions loaded")

        except Exception as e:
            self.logger.error(f"Error loading positions: {e}")

    async def update_position(self, symbol: str, position_data: Optional[Dict]):
        """
        Update or create a position

        Args:
            symbol: Trading symbol
            position_data: Position data (None to close position)
        """
        try:
            if position_data is None:
                # Close position
                if symbol in self.positions:
                    del self.positions[symbol]
                    self.logger.info(f"Position closed: {symbol}")
            else:
                # Update or create position
                self.positions[symbol] = position_data
                self.logger.info(f"Position updated: {symbol}")

            # Update equity
            await self._update_equity()

        except Exception as e:
            self.logger.error(f"Error updating position {symbol}: {e}")

    async def get_position(self, symbol: str) -> Optional[Dict]:
        """
        Get position for a symbol

        Args:
            symbol: Trading symbol

        Returns:
            Position data or None
        """
        return self.positions.get(symbol)

    async def get_all_positions(self) -> Dict[str, Dict]:
        """
        Get all open positions

        Returns:
            Dictionary of all positions
        """
        return self.positions.copy()

    async def get_balance(self, currency: str = None) -> float:
        """
        Get balance for a currency

        Args:
            currency: Currency symbol (default: base currency)

        Returns:
            Balance amount
        """
        if currency is None:
            currency = self.base_currency

        return self.balances.get(currency, 0.0)

    async def update_balance(self, currency: str, amount: float):
        """
        Update balance for a currency

        Args:
            currency: Currency symbol
            amount: New balance amount
        """
        try:
            self.balances[currency] = amount
            await self._update_equity()

        except Exception as e:
            self.logger.error(f"Error updating balance for {currency}: {e}")

    async def record_trade(self, trade_data: Dict):
        """
        Record a completed trade

        Args:
            trade_data: Trade information
        """
        try:
            self.trade_history.append(trade_data)
            self.total_trades += 1

            # Update trade statistics
            pnl = trade_data.get("pnl", 0)

            if pnl > 0:
                self.winning_trades += 1
            elif pnl < 0:
                self.losing_trades += 1

            self.realized_pnl += pnl

            # Update daily P&L
            trade_date = trade_data.get("timestamp", datetime.now()).date()
            self.daily_pnl[trade_date] += pnl

            self.logger.info(f"Trade recorded: {trade_data.get('symbol')} P&L: {pnl}")

        except Exception as e:
            self.logger.error(f"Error recording trade: {e}")

    async def calculate_unrealized_pnl(
        self, current_prices: Dict[str, float]
    ) -> float:
        """
        Calculate unrealized P&L for open positions

        Args:
            current_prices: Dictionary of current prices

        Returns:
            Total unrealized P&L
        """
        try:
            total_unrealized = 0.0

            for symbol, position in self.positions.items():
                if symbol not in current_prices:
                    continue

                current_price = current_prices[symbol]
                entry_price = position.get("entry_price", 0)
                size = position.get("size", 0)
                side = position.get("side", "long")

                if side == "long":
                    unrealized = (current_price - entry_price) * size
                else:  # short
                    unrealized = (entry_price - current_price) * size

                total_unrealized += unrealized

            self.unrealized_pnl = total_unrealized
            return total_unrealized

        except Exception as e:
            self.logger.error(f"Error calculating unrealized P&L: {e}")
            return 0.0

    async def _update_equity(self):
        """Update total equity calculation"""
        try:
            # Calculate total equity from balances and positions
            total = sum(self.balances.values())
            total += self.unrealized_pnl

            self.total_equity = total

            # Record equity history
            self.equity_history.append(
                {"timestamp": datetime.now(), "equity": total}
            )

            # Keep only recent history (last 10000 points)
            if len(self.equity_history) > 10000:
                self.equity_history.pop(0)

        except Exception as e:
            self.logger.error(f"Error updating equity: {e}")

    async def get_total_value(self) -> float:
        """
        Get total portfolio value

        Returns:
            Total portfolio value
        """
        return self.total_equity

    async def get_performance_metrics(self) -> Dict:
        """
        Get portfolio performance metrics

        Returns:
            Dictionary of performance metrics
        """
        try:
            win_rate = (
                self.winning_trades / self.total_trades
                if self.total_trades > 0
                else 0.0
            )

            avg_win = 0.0
            avg_loss = 0.0

            if self.winning_trades > 0:
                winning_trades_pnl = [
                    t["pnl"] for t in self.trade_history if t.get("pnl", 0) > 0
                ]
                avg_win = sum(winning_trades_pnl) / len(winning_trades_pnl)

            if self.losing_trades > 0:
                losing_trades_pnl = [
                    t["pnl"] for t in self.trade_history if t.get("pnl", 0) < 0
                ]
                avg_loss = sum(losing_trades_pnl) / len(losing_trades_pnl)

            profit_factor = (
                abs(avg_win * self.winning_trades / (avg_loss * self.losing_trades))
                if self.losing_trades > 0 and avg_loss != 0
                else 0.0
            )

            # Calculate returns
            initial_capital = 100000.0
            total_return = (
                (self.total_equity - initial_capital) / initial_capital
                if initial_capital > 0
                else 0.0
            )

            metrics = {
                "total_equity": self.total_equity,
                "realized_pnl": self.realized_pnl,
                "unrealized_pnl": self.unrealized_pnl,
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate": win_rate,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": profit_factor,
                "total_return": total_return,
                "open_positions": len(self.positions),
            }

            return metrics

        except Exception as e:
            self.logger.error(f"Error calculating performance metrics: {e}")
            return {}

    async def update_metrics(self):
        """Update portfolio metrics"""
        try:
            # Update equity
            await self._update_equity()

            # Log current metrics
            metrics = await self.get_performance_metrics()
            self.logger.debug(f"Portfolio metrics updated: {metrics}")

        except Exception as e:
            self.logger.error(f"Error updating metrics: {e}")

    async def get_position_summary(self) -> List[Dict]:
        """
        Get summary of all positions

        Returns:
            List of position summaries
        """
        try:
            summaries = []

            for symbol, position in self.positions.items():
                summary = {
                    "symbol": symbol,
                    "side": position.get("side"),
                    "size": position.get("size"),
                    "entry_price": position.get("entry_price"),
                    "current_price": None,  # Would be filled with current market price
                    "unrealized_pnl": None,  # Would be calculated with current price
                    "timestamp": position.get("timestamp"),
                }

                summaries.append(summary)

            return summaries

        except Exception as e:
            self.logger.error(f"Error getting position summary: {e}")
            return []

    async def get_trade_history(
        self, symbol: str = None, limit: int = 100
    ) -> List[Dict]:
        """
        Get trade history

        Args:
            symbol: Filter by symbol (optional)
            limit: Maximum number of trades to return

        Returns:
            List of trades
        """
        try:
            trades = self.trade_history

            if symbol:
                trades = [t for t in trades if t.get("symbol") == symbol]

            # Sort by timestamp (newest first)
            trades = sorted(
                trades,
                key=lambda x: x.get("timestamp", datetime.now()),
                reverse=True,
            )

            return trades[:limit]

        except Exception as e:
            self.logger.error(f"Error getting trade history: {e}")
            return []

    async def get_daily_pnl(self, days: int = 30) -> Dict:
        """
        Get daily P&L for recent days

        Args:
            days: Number of days to retrieve

        Returns:
            Dictionary of daily P&L
        """
        try:
            # Get recent days
            recent_pnl = dict(sorted(self.daily_pnl.items(), reverse=True)[:days])

            return recent_pnl

        except Exception as e:
            self.logger.error(f"Error getting daily P&L: {e}")
            return {}

    def get_portfolio_allocation(self) -> Dict[str, float]:
        """
        Get portfolio allocation by asset

        Returns:
            Dictionary of allocation percentages
        """
        try:
            if self.total_equity == 0:
                return {}

            allocation = {}

            # Calculate allocation from balances
            for currency, balance in self.balances.items():
                allocation[currency] = balance / self.total_equity

            return allocation

        except Exception as e:
            self.logger.error(f"Error calculating portfolio allocation: {e}")
            return {}
