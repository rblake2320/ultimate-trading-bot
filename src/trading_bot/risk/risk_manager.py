"""
Risk Manager - Advanced risk management and position sizing
"""

import asyncio
import logging
import numpy as np
import pandas as pd
from typing import Dict
from datetime import datetime
import math


class RiskManager:
    """
    Advanced risk management system with multiple risk controls
    """

    def __init__(self, risk_config: Dict):
        """
        Initialize risk manager

        Args:
            risk_config: Risk management configuration
        """
        self.risk_config = risk_config
        self.logger = logging.getLogger(__name__)

        # Risk metrics
        self.portfolio_value = 100000.0  # Starting portfolio value
        self.daily_pnl = 0.0
        self.max_drawdown = 0.0
        self.current_drawdown = 0.0
        self.var_95 = 0.0  # Value at Risk 95%

        # Position tracking
        self.position_sizes = {}
        self.correlation_matrix = pd.DataFrame()
        self.volatility_data = {}

        # Risk limits
        self.max_portfolio_risk = risk_config.get("max_portfolio_risk", 0.02)
        self.max_position_size = risk_config.get("max_position_size", 0.1)
        self.correlation_threshold = risk_config.get("correlation_threshold", 0.7)
        self.drawdown_limit = risk_config.get("drawdown_limit", 0.1)
        self.volatility_lookback = risk_config.get("volatility_lookback", 20)

    async def initialize(self):
        """Initialize risk management system"""
        self.logger.info("Initializing risk manager...")

        try:
            # Initialize correlation matrix
            await self._initialize_correlation_matrix()

            # Initialize volatility calculations
            await self._initialize_volatility_data()

            self.logger.info("Risk manager initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize risk manager: {e}")
            raise

    async def _initialize_correlation_matrix(self):
        """Initialize correlation matrix for assets"""
        try:
            # Default symbols
            symbols = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]

            # Create identity matrix as default (no correlation)
            self.correlation_matrix = pd.DataFrame(
                np.eye(len(symbols)), index=symbols, columns=symbols
            )

        except Exception as e:
            self.logger.error(f"Error initializing correlation matrix: {e}")

    async def _initialize_volatility_data(self):
        """Initialize volatility calculations"""
        try:
            symbols = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]

            for symbol in symbols:
                self.volatility_data[symbol] = {
                    "returns": [],
                    "volatility": 0.02,  # Default 2% daily volatility
                    "last_update": datetime.now(),
                }

        except Exception as e:
            self.logger.error(f"Error initializing volatility data: {e}")

    async def assess_trade(self, signal: Dict) -> Dict:
        """
        Assess trade risk and approve/reject

        Args:
            signal: Trading signal

        Returns:
            Risk assessment result
        """
        try:
            symbol = signal["symbol"]
            action = signal["action"]

            # Check portfolio risk limits
            portfolio_risk = await self.calculate_portfolio_risk()
            if portfolio_risk > self.max_portfolio_risk:
                return {
                    "approved": False,
                    "reason": f"Portfolio risk too high: {portfolio_risk:.3f} > {self.max_portfolio_risk:.3f}",
                }

            # Check drawdown limits
            if self.current_drawdown > self.drawdown_limit:
                return {
                    "approved": False,
                    "reason": f"Drawdown limit exceeded: {self.current_drawdown:.3f} > {self.drawdown_limit:.3f}",
                }

            # Check correlation limits for new positions
            if action == "buy":
                correlation_risk = await self._check_correlation_risk(symbol)
                if correlation_risk > self.correlation_threshold:
                    return {
                        "approved": False,
                        "reason": f"High correlation risk: {correlation_risk:.3f} > {self.correlation_threshold:.3f}",
                    }

            # Check volatility-adjusted risk
            volatility_risk = await self._check_volatility_risk(symbol, signal)
            if not volatility_risk["approved"]:
                return volatility_risk

            return {
                "approved": True,
                "reason": "Trade approved",
                "risk_score": portfolio_risk,
            }

        except Exception as e:
            self.logger.error(f"Error assessing trade risk: {e}")
            return {"approved": False, "reason": f"Risk assessment error: {e}"}

    async def calculate_position_size(self, signal: Dict) -> float:
        """
        Calculate optimal position size using Kelly Criterion and risk limits

        Args:
            signal: Trading signal

        Returns:
            Position size
        """
        try:
            symbol = signal["symbol"]
            confidence = signal["confidence"]

            # Get volatility for the symbol
            volatility = self.volatility_data.get(symbol, {}).get("volatility", 0.02)

            # Kelly Criterion calculation
            win_rate = confidence  # Use confidence as win rate proxy
            avg_win = 0.02  # Average win 2%
            avg_loss = 0.01  # Average loss 1%

            kelly_fraction = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
            kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Cap at 25%

            # Volatility adjustment
            vol_adjustment = 0.02 / volatility  # Target 2% volatility
            vol_adjustment = min(vol_adjustment, 2.0)  # Cap adjustment

            # Base position size
            base_size = kelly_fraction * vol_adjustment

            # Apply risk limits
            max_size = self.max_position_size
            position_size = min(base_size, max_size)

            # Portfolio heat adjustment
            portfolio_heat = await self._calculate_portfolio_heat()
            if portfolio_heat > 0.5:  # If portfolio is more than 50% invested
                position_size *= 1 - portfolio_heat

            # Minimum position size
            min_size = 0.001  # 0.1%
            position_size = max(position_size, min_size)

            self.logger.info(f"Position size calculated: {symbol} {position_size:.4f}")
            return position_size

        except Exception as e:
            self.logger.error(f"Error calculating position size: {e}")
            return 0.001  # Minimum size

    async def calculate_portfolio_risk(self) -> float:
        """
        Calculate current portfolio risk (VaR)

        Returns:
            Portfolio risk as percentage
        """
        try:
            if not self.position_sizes:
                return 0.0

            # Calculate individual position risks
            position_risks = []
            weights = []

            for symbol, size in self.position_sizes.items():
                volatility = self.volatility_data.get(symbol, {}).get(
                    "volatility", 0.02
                )
                position_risk = size * volatility
                position_risks.append(position_risk)
                weights.append(size)

            if not position_risks:
                return 0.0

            # Portfolio risk calculation with correlation
            portfolio_variance = 0.0

            for i, risk_i in enumerate(position_risks):
                for j, risk_j in enumerate(position_risks):
                    symbol_i = list(self.position_sizes.keys())[i]
                    symbol_j = list(self.position_sizes.keys())[j]

                    correlation = self._get_correlation(symbol_i, symbol_j)
                    portfolio_variance += (
                        weights[i] * weights[j] * risk_i * risk_j * correlation
                    )

            portfolio_risk = math.sqrt(portfolio_variance)
            return portfolio_risk

        except Exception as e:
            self.logger.error(f"Error calculating portfolio risk: {e}")
            return 0.0

    async def _check_correlation_risk(self, symbol: str) -> float:
        """
        Check correlation risk for adding new position

        Args:
            symbol: Symbol to check

        Returns:
            Maximum correlation with existing positions
        """
        try:
            if not self.position_sizes:
                return 0.0

            max_correlation = 0.0

            for existing_symbol in self.position_sizes.keys():
                correlation = self._get_correlation(symbol, existing_symbol)
                max_correlation = max(max_correlation, abs(correlation))

            return max_correlation

        except Exception as e:
            self.logger.error(f"Error checking correlation risk: {e}")
            return 0.0

    async def _check_volatility_risk(self, symbol: str, signal: Dict) -> Dict:
        """
        Check volatility-based risk

        Args:
            symbol: Trading symbol
            signal: Trading signal

        Returns:
            Volatility risk assessment
        """
        try:
            volatility = self.volatility_data.get(symbol, {}).get("volatility", 0.02)

            # High volatility threshold (5% daily)
            high_vol_threshold = 0.05

            if volatility > high_vol_threshold:
                return {
                    "approved": False,
                    "reason": f"High volatility: {volatility:.3f} > {high_vol_threshold:.3f}",
                }

            return {"approved": True, "reason": "Volatility risk acceptable"}

        except Exception as e:
            self.logger.error(f"Error checking volatility risk: {e}")
            return {"approved": False, "reason": f"Volatility check error: {e}"}

    async def _calculate_portfolio_heat(self) -> float:
        """
        Calculate portfolio heat (percentage of capital deployed)

        Returns:
            Portfolio heat as percentage
        """
        try:
            total_exposure = sum(self.position_sizes.values())
            return min(total_exposure, 1.0)

        except Exception as e:
            self.logger.error(f"Error calculating portfolio heat: {e}")
            return 0.0

    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """
        Get correlation between two symbols

        Args:
            symbol1: First symbol
            symbol2: Second symbol

        Returns:
            Correlation coefficient
        """
        try:
            if symbol1 == symbol2:
                return 1.0

            if (
                symbol1 in self.correlation_matrix.index
                and symbol2 in self.correlation_matrix.columns
            ):
                return self.correlation_matrix.loc[symbol1, symbol2]

            # Default correlation for crypto pairs
            if "BTC" in symbol1 and "BTC" in symbol2:
                return 1.0
            elif ("BTC" in symbol1 or "BTC" in symbol2) and (
                "ETH" in symbol1 or "ETH" in symbol2
            ):
                return 0.7  # BTC-ETH correlation
            else:
                return 0.5  # Default moderate correlation

        except Exception as e:
            self.logger.error(f"Error getting correlation: {e}")
            return 0.5

    async def update_market_data(self, market_data: Dict):
        """
        Update risk calculations with new market data

        Args:
            market_data: Latest market data
        """
        try:
            prices = market_data.get("prices", {})

            for symbol, price_data in prices.items():
                await self._update_volatility(symbol, price_data)

            # Update correlation matrix periodically
            await self._update_correlation_matrix()

        except Exception as e:
            self.logger.error(f"Error updating market data: {e}")

    async def _update_volatility(self, symbol: str, price_data: Dict):
        """
        Update volatility calculation for a symbol

        Args:
            symbol: Trading symbol
            price_data: Price data
        """
        try:
            if symbol not in self.volatility_data:
                self.volatility_data[symbol] = {
                    "returns": [],
                    "volatility": 0.02,
                    "last_price": price_data.get("price", 0),
                    "last_update": datetime.now(),
                }
                return

            vol_data = self.volatility_data[symbol]
            current_price = price_data.get("price", 0)
            last_price = vol_data.get("last_price", current_price)

            if last_price > 0 and current_price > 0:
                # Calculate return
                return_pct = (current_price - last_price) / last_price

                # Add to returns list
                vol_data["returns"].append(return_pct)

                # Keep only recent returns
                if len(vol_data["returns"]) > self.volatility_lookback:
                    vol_data["returns"].pop(0)

                # Calculate volatility (standard deviation of returns)
                if len(vol_data["returns"]) >= 5:
                    returns_array = np.array(vol_data["returns"])
                    volatility = np.std(returns_array) * math.sqrt(
                        1440
                    )  # Annualized (1440 minutes per day)
                    vol_data["volatility"] = volatility

                vol_data["last_price"] = current_price
                vol_data["last_update"] = datetime.now()

        except Exception as e:
            self.logger.error(f"Error updating volatility for {symbol}: {e}")

    async def _update_correlation_matrix(self):
        """Update correlation matrix with recent price data"""
        try:
            # This would calculate correlations from recent price data
            # For now, use static correlations
            pass

        except Exception as e:
            self.logger.error(f"Error updating correlation matrix: {e}")

    async def update_position(self, symbol: str, position_size: float):
        """
        Update position size tracking

        Args:
            symbol: Trading symbol
            position_size: New position size
        """
        try:
            if position_size > 0:
                self.position_sizes[symbol] = position_size
            else:
                self.position_sizes.pop(symbol, None)

        except Exception as e:
            self.logger.error(f"Error updating position: {e}")

    async def update_pnl(self, pnl: float):
        """
        Update P&L and drawdown calculations

        Args:
            pnl: Profit/Loss amount
        """
        try:
            self.daily_pnl += pnl

            # Update portfolio value
            self.portfolio_value += pnl

            # Calculate drawdown
            if self.portfolio_value > self.max_drawdown:
                self.max_drawdown = self.portfolio_value
                self.current_drawdown = 0.0
            else:
                self.current_drawdown = (
                    self.max_drawdown - self.portfolio_value
                ) / self.max_drawdown

        except Exception as e:
            self.logger.error(f"Error updating P&L: {e}")

    def get_risk_metrics(self) -> Dict:
        """
        Get current risk metrics

        Returns:
            Dictionary of risk metrics
        """
        try:
            return {
                "portfolio_value": self.portfolio_value,
                "daily_pnl": self.daily_pnl,
                "max_drawdown": self.max_drawdown,
                "current_drawdown": self.current_drawdown,
                "portfolio_risk": asyncio.run(self.calculate_portfolio_risk()),
                "portfolio_heat": asyncio.run(self._calculate_portfolio_heat()),
                "active_positions": len(self.position_sizes),
                "var_95": self.var_95,
            }

        except Exception as e:
            self.logger.error(f"Error getting risk metrics: {e}")
            return {}

    async def emergency_risk_check(self) -> Dict:
        """
        Emergency risk check for immediate action

        Returns:
            Emergency risk assessment
        """
        try:
            actions = []

            # Check drawdown
            if self.current_drawdown > self.drawdown_limit:
                actions.append(
                    {
                        "action": "close_all_positions",
                        "reason": f"Drawdown limit exceeded: {self.current_drawdown:.3f}",
                    }
                )

            # Check portfolio risk
            portfolio_risk = await self.calculate_portfolio_risk()
            if portfolio_risk > self.max_portfolio_risk * 2:  # 2x normal limit
                actions.append(
                    {
                        "action": "reduce_positions",
                        "reason": f"Extreme portfolio risk: {portfolio_risk:.3f}",
                    }
                )

            return {"emergency": len(actions) > 0, "actions": actions}

        except Exception as e:
            self.logger.error(f"Error in emergency risk check: {e}")
            return {"emergency": False, "actions": []}
