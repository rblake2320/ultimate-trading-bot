"""
Exchange Manager - Handles connections to multiple exchanges
"""

# mypy: ignore-errors

import logging
from typing import Dict, List, Optional
import ccxt.async_support as ccxt


class ExchangeManager:
    """
    Manages connections and operations across multiple exchanges
    """

    def __init__(self, exchange_configs: Dict[str, Dict]):
        """
        Initialize exchange manager

        Args:
            exchange_configs: Dictionary of exchange configurations
        """
        self.exchange_configs = exchange_configs
        self.exchanges = {}
        self.logger = logging.getLogger(__name__)

    async def initialize(self):
        """Initialize all configured exchanges"""
        self.logger.info("Initializing exchanges...")

        for exchange_name, config in self.exchange_configs.items():
            if not config.get("enabled", False):
                continue

            try:
                exchange = await self._create_exchange(exchange_name, config)
                if exchange:
                    self.exchanges[exchange_name] = exchange
                    self.logger.info(f"Successfully initialized {exchange_name}")

            except Exception as e:
                self.logger.error(f"Failed to initialize {exchange_name}: {e}")

    async def _create_exchange(
        self, exchange_name: str, config: Dict
    ) -> Optional[ccxt.Exchange]:
        """
        Create and configure an exchange instance

        Args:
            exchange_name: Name of the exchange
            config: Exchange configuration

        Returns:
            Configured exchange instance or None
        """
        try:
            # Map exchange names to CCXT classes
            exchange_classes = {
                "binance": ccxt.binance,
                "coinbase": ccxt.coinbasepro,
                "kraken": ccxt.kraken,
                "bybit": ccxt.bybit,
                "okx": ccxt.okx,
                "huobi": ccxt.huobi,
                "kucoin": ccxt.kucoin,
            }

            if exchange_name not in exchange_classes:
                self.logger.error(f"Unsupported exchange: {exchange_name}")
                return None

            # Create exchange instance
            exchange_class = exchange_classes[exchange_name]

            exchange_config = {
                "apiKey": config.get("api_key"),
                "secret": config.get("api_secret"),
                "sandbox": config.get("testnet", False),
                "enableRateLimit": True,
                "timeout": 30000,
            }

            # Add exchange-specific configurations
            if exchange_name == "coinbase":
                exchange_config["passphrase"] = config.get("passphrase")

            exchange = exchange_class(exchange_config)

            # Test connection
            await exchange.load_markets()

            return exchange

        except Exception as e:
            self.logger.error(f"Error creating {exchange_name} exchange: {e}")
            return None

    async def get_exchange(self, exchange_name: str) -> Optional[ccxt.Exchange]:
        """
        Get exchange instance by name

        Args:
            exchange_name: Name of the exchange

        Returns:
            Exchange instance or None
        """
        return self.exchanges.get(exchange_name)

    async def get_primary_exchange(self) -> Optional[ccxt.Exchange]:
        """Get the primary exchange (first enabled one)"""
        if self.exchanges:
            return list(self.exchanges.values())[0]
        return None

    async def get_ticker(
        self, symbol: str, exchange_name: str = None
    ) -> Optional[Dict]:
        """
        Get ticker data for a symbol

        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT')
            exchange_name: Specific exchange name (optional)

        Returns:
            Ticker data or None
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
                if exchange:
                    return await exchange.fetch_ticker(symbol)
            else:
                # Try primary exchange first
                primary = await self.get_primary_exchange()
                if primary:
                    return await primary.fetch_ticker(symbol)

            return None

        except Exception as e:
            self.logger.error(f"Error fetching ticker {symbol}: {e}")
            return None

    async def get_orderbook(
        self, symbol: str, exchange_name: str = None
    ) -> Optional[Dict]:
        """
        Get order book for a symbol

        Args:
            symbol: Trading symbol
            exchange_name: Specific exchange name (optional)

        Returns:
            Order book data or None
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
                if exchange:
                    return await exchange.fetch_order_book(symbol)
            else:
                primary = await self.get_primary_exchange()
                if primary:
                    return await primary.fetch_order_book(symbol)

            return None

        except Exception as e:
            self.logger.error(f"Error fetching orderbook {symbol}: {e}")
            return None

    async def place_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float = None,
        exchange_name: str = None,
    ) -> Optional[Dict]:
        """
        Place an order on an exchange

        Args:
            symbol: Trading symbol
            order_type: 'market' or 'limit'
            side: 'buy' or 'sell'
            amount: Order amount
            price: Order price (for limit orders)
            exchange_name: Specific exchange name (optional)

        Returns:
            Order data or None
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
            else:
                exchange = await self.get_primary_exchange()

            if not exchange:
                self.logger.error("No exchange available for order placement")
                return None

            # Place order
            if order_type == "market":
                if side == "buy":
                    order = await exchange.create_market_buy_order(symbol, amount)
                else:
                    order = await exchange.create_market_sell_order(symbol, amount)
            else:  # limit order
                if side == "buy":
                    order = await exchange.create_limit_buy_order(symbol, amount, price)
                else:
                    order = await exchange.create_limit_sell_order(
                        symbol, amount, price
                    )

            self.logger.info(f"Order placed: {order['id']} {side} {amount} {symbol}")
            return order

        except Exception as e:
            self.logger.error(f"Error placing order: {e}")
            return None

    async def cancel_order(
        self, order_id: str, symbol: str, exchange_name: str = None
    ) -> bool:
        """
        Cancel an order

        Args:
            order_id: Order ID to cancel
            symbol: Trading symbol
            exchange_name: Specific exchange name (optional)

        Returns:
            True if successful, False otherwise
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
            else:
                exchange = await self.get_primary_exchange()

            if not exchange:
                return False

            await exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Order cancelled: {order_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error cancelling order {order_id}: {e}")
            return False

    async def get_order_status(
        self, order_id: str, symbol: str, exchange_name: str = None
    ) -> Optional[Dict]:
        """
        Get order status

        Args:
            order_id: Order ID
            symbol: Trading symbol
            exchange_name: Specific exchange name (optional)

        Returns:
            Order status data or None
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
            else:
                exchange = await self.get_primary_exchange()

            if not exchange:
                return None

            order = await exchange.fetch_order(order_id, symbol)
            return order

        except Exception as e:
            self.logger.error(f"Error fetching order status {order_id}: {e}")
            return None

    async def get_balance(self, exchange_name: str = None) -> Optional[Dict]:
        """
        Get account balance

        Args:
            exchange_name: Specific exchange name (optional)

        Returns:
            Balance data or None
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
            else:
                exchange = await self.get_primary_exchange()

            if not exchange:
                return None

            balance = await exchange.fetch_balance()
            return balance

        except Exception as e:
            self.logger.error(f"Error fetching balance: {e}")
            return None

    async def get_trading_fees(
        self, symbol: str, exchange_name: str = None
    ) -> Optional[Dict]:
        """
        Get trading fees for a symbol

        Args:
            symbol: Trading symbol
            exchange_name: Specific exchange name (optional)

        Returns:
            Fee data or None
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
            else:
                exchange = await self.get_primary_exchange()

            if not exchange:
                return None

            fees = await exchange.fetch_trading_fees()
            return fees.get(symbol, fees.get("trading", {}))

        except Exception as e:
            self.logger.error(f"Error fetching trading fees: {e}")
            return None

    async def health_check(self) -> Dict[str, bool]:
        """
        Check health of all exchanges

        Returns:
            Dictionary of exchange health status
        """
        health_status = {}

        for exchange_name, exchange in self.exchanges.items():
            try:
                # Simple ping test
                await exchange.fetch_status()
                health_status[exchange_name] = True

            except Exception as e:
                self.logger.warning(
                    f"Exchange {exchange_name} health check failed: {e}"
                )
                health_status[exchange_name] = False

        return health_status

    async def close_all(self):
        """Close all exchange connections"""
        for exchange_name, exchange in self.exchanges.items():
            try:
                await exchange.close()
                self.logger.info(f"Closed connection to {exchange_name}")
            except Exception as e:
                self.logger.error(f"Error closing {exchange_name}: {e}")

    def get_available_exchanges(self) -> List[str]:
        """Get list of available exchange names"""
        return list(self.exchanges.keys())

    def get_supported_symbols(self, exchange_name: str = None) -> List[str]:
        """
        Get supported trading symbols

        Args:
            exchange_name: Specific exchange name (optional)

        Returns:
            List of supported symbols
        """
        try:
            if exchange_name:
                exchange = self.exchanges.get(exchange_name)
            else:
                exchange = list(self.exchanges.values())[0] if self.exchanges else None

            if exchange and hasattr(exchange, "markets"):
                return list(exchange.markets.keys())

            return []

        except Exception as e:
            self.logger.error(f"Error getting supported symbols: {e}")
            return []
