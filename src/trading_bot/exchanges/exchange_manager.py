"""Exchange connection manager built on ccxt async.

Any ccxt-supported exchange id works. US-friendly venues (2026): kraken and
coinbase (Advanced Trade) as primaries, binanceus / okx as secondaries.
The old ``coinbasepro`` id is dead — Coinbase Pro was shut down in 2023;
ccxt's ``coinbase`` id now targets the Advanced Trade API.

Instances are created keyless when no credentials are configured, which is
enough for all public market data (paper mode needs nothing else).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ExchangeManager:
    """Creates, holds, and health-checks ccxt exchange instances."""

    def __init__(self, exchange_configs: Dict[str, Dict]):
        self.exchange_configs = exchange_configs
        self.exchanges: Dict[str, object] = {}

    async def initialize(self) -> None:
        import ccxt.async_support as ccxt_async

        for name, config in self.exchange_configs.items():
            if not config.get("enabled", False):
                continue
            if not hasattr(ccxt_async, name):
                logger.error("ccxt has no exchange id '%s' — skipping", name)
                continue
            params: Dict[str, object] = {
                "enableRateLimit": True,
                "timeout": 30_000,
            }
            api_key = config.get("api_key")
            api_secret = config.get("api_secret")
            if api_key and api_secret and "YOUR_" not in str(api_key):
                params["apiKey"] = api_key
                params["secret"] = api_secret
                if config.get("password") or config.get("passphrase"):
                    params["password"] = config.get("password") or config.get(
                        "passphrase"
                    )
            exchange = getattr(ccxt_async, name)(params)
            if config.get("testnet"):
                try:
                    exchange.set_sandbox_mode(True)
                except Exception as exc:  # noqa: BLE001 - not all venues have one
                    logger.warning("%s has no sandbox mode: %s", name, exc)
            try:
                await exchange.load_markets()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to connect to %s: %s", name, exc)
                await exchange.close()
                continue
            self.exchanges[name] = exchange
            logger.info(
                "Connected to %s (%d markets, %s)",
                name,
                len(exchange.markets),
                "authenticated" if "apiKey" in params else "public only",
            )

        if not self.exchanges:
            raise RuntimeError(
                "No exchange could be initialized — check config 'exchanges' section"
            )

    def get(self, name: Optional[str] = None):
        if name:
            return self.exchanges.get(name)
        return next(iter(self.exchanges.values()), None)

    @property
    def primary(self):
        return self.get()

    @property
    def primary_name(self) -> Optional[str]:
        return next(iter(self.exchanges), None)

    def names(self) -> List[str]:
        return list(self.exchanges)

    async def health_check(self) -> Dict[str, bool]:
        status = {}
        for name, exchange in self.exchanges.items():
            try:
                await exchange.fetch_time()
                status[name] = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Health check failed for %s: %s", name, exc)
                status[name] = False
        return status

    async def close_all(self) -> None:
        for name, exchange in self.exchanges.items():
            try:
                await exchange.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing %s: %s", name, exc)
        self.exchanges.clear()
