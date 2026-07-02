"""Robinhood Crypto Trading API — native Broker adapter.

Robinhood's key-based API (docs.robinhood.com/crypto/trading) is not in
ccxt, so this adapter implements the ``Broker`` interface directly.

Auth: Ed25519 request signing. Generate a keypair with
``python main.py keygen``, register the PUBLIC key at
robinhood.com → Account → Crypto → API, choose *trading* permission, and
set the environment variables::

    ROBINHOOD_API_KEY=rh-api-...        (issued by Robinhood)
    ROBINHOOD_PRIVATE_KEY=<base64 seed> (from keygen — keep secret)

Every request is signed over ``api_key + timestamp + path + method + body``
and sent with x-api-key / x-signature / x-timestamp headers, exactly per
the official docs.

Notes that matter:
  - US customers only; quotes are spread-inclusive ("commission-free").
  - Rate limits are unpublished and fluctuate — 429s are retried with
    backoff.
  - Symbols use dashes ("BTC-USD"); the bot's "BTC/USD" is converted.
  - Robinhood's *stock* Agentic Trading product is MCP/OAuth only (no
    key-based REST) — see docs/CONNECTIVITY.md for how to use it with
    Claude directly.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Dict, Optional

from ..execution.brokers import Broker
from ..models import Order, OrderStatus, OrderType, utc_now

logger = logging.getLogger(__name__)

BASE_URL = "https://trading.robinhood.com"

STATE_MAP = {
    "open": OrderStatus.OPEN,
    "pending": OrderStatus.PENDING,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "failed": OrderStatus.REJECTED,
    "rejected": OrderStatus.REJECTED,
}


def to_rh_symbol(symbol: str) -> str:
    """BTC/USD -> BTC-USD"""
    return symbol.replace("/", "-")


def generate_keypair() -> Dict[str, str]:
    """Create an Ed25519 keypair in the base64 format Robinhood expects."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "private_key_b64": base64.b64encode(seed).decode(),
        "public_key_b64": base64.b64encode(public).decode(),
    }


class RobinhoodCryptoBroker(Broker):
    def __init__(
        self,
        api_key: str,
        private_key_b64: str,
        max_retries: int = 3,
        timeout: float = 20.0,
    ):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        if not api_key or not private_key_b64:
            raise ValueError(
                "Robinhood broker needs ROBINHOOD_API_KEY and "
                "ROBINHOOD_PRIVATE_KEY (see 'python main.py keygen')"
            )
        self.api_key = api_key
        self._signer = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(private_key_b64)
        )
        self.max_retries = int(max_retries)
        self.timeout = float(timeout)

    @property
    def is_paper(self) -> bool:
        return False  # Robinhood has no crypto sandbox

    # ------------------------------------------------------------------ #
    # Signed HTTP
    # ------------------------------------------------------------------ #

    def _headers(self, method: str, path: str, body: str) -> Dict[str, str]:
        timestamp = str(int(time.time()))
        message = f"{self.api_key}{timestamp}{path}{method}{body}"
        signature = self._signer.sign(message.encode("utf-8"))
        return {
            "x-api-key": self.api_key,
            "x-signature": base64.b64encode(signature).decode(),
            "x-timestamp": timestamp,
            "Content-Type": "application/json; charset=utf-8",
        }

    async def _request(
        self, method: str, path: str, payload: Optional[dict] = None
    ) -> dict:
        import aiohttp

        body = json.dumps(payload) if payload is not None else ""
        last_error: Optional[str] = None
        for attempt in range(1, self.max_retries + 1):
            headers = self._headers(method, path, body)
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(
                        method,
                        f"{BASE_URL}{path}",
                        data=body if body else None,
                        headers=headers,
                    ) as resp:
                        text = await resp.text()
                        if resp.status == 429:
                            wait = 2.0**attempt
                            logger.warning(
                                "Robinhood rate limited (attempt %d) — %.0fs backoff",
                                attempt,
                                wait,
                            )
                            await asyncio.sleep(wait)
                            last_error = "429 rate limited"
                            continue
                        if resp.status in (401, 403):
                            raise PermissionError(
                                f"Robinhood auth failed ({resp.status}): {text[:200]}"
                            )
                        if resp.status >= 400:
                            raise RuntimeError(
                                f"Robinhood API {resp.status}: {text[:300]}"
                            )
                        return json.loads(text) if text else {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = str(exc)
                wait = 2.0**attempt
                logger.warning(
                    "Robinhood network error (attempt %d/%d): %s — retry in %.0fs",
                    attempt,
                    self.max_retries,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
        raise RuntimeError(f"Robinhood request failed after retries: {last_error}")

    # ------------------------------------------------------------------ #
    # Broker interface
    # ------------------------------------------------------------------ #

    @staticmethod
    def _order_payload(order: Order) -> dict:
        payload: dict = {
            "client_order_id": str(uuid.uuid4()),
            "side": order.side.value,
            "symbol": to_rh_symbol(order.symbol),
            "type": order.type.value,
        }
        qty = f"{order.amount:.10f}".rstrip("0").rstrip(".")
        if order.type == OrderType.MARKET:
            payload["market_order_config"] = {"asset_quantity": qty}
        else:
            payload["limit_order_config"] = {
                "asset_quantity": qty,
                "limit_price": f"{order.price:.10f}".rstrip("0").rstrip("."),
                "time_in_force": "gtc",
            }
        return payload

    def _apply(self, order: Order, raw: dict) -> None:
        order.exchange_order_id = raw.get("id", order.exchange_order_id)
        order.status = STATE_MAP.get(str(raw.get("state")), order.status)
        filled = raw.get("filled_asset_quantity")
        if filled is not None:
            order.filled = float(filled)
        if raw.get("average_price") is not None:
            order.average_price = float(raw["average_price"])
        # Executions carry per-fill price/quantity when average is absent.
        executions = raw.get("executions") or []
        if order.average_price is None and executions:
            total_qty = sum(float(e["quantity"]) for e in executions)
            if total_qty > 0:
                order.average_price = (
                    sum(float(e["effective_price"]) * float(e["quantity"]) for e in executions)
                    / total_qty
                )
                order.filled = max(order.filled, total_qty)
        order.updated_at = utc_now()

    async def create_order(self, order: Order) -> Order:
        try:
            raw = await self._request(
                "POST", "/api/v1/crypto/trading/orders/", self._order_payload(order)
            )
            self._apply(order, raw)
        except (PermissionError, RuntimeError) as exc:
            order.status = OrderStatus.REJECTED
            order.reason += f" | {exc}"
            logger.error("Robinhood order rejected: %s", exc)
        return order

    async def sync_order(self, order: Order) -> Order:
        if not order.exchange_order_id or order.is_closed:
            return order
        try:
            raw = await self._request(
                "GET", f"/api/v1/crypto/trading/orders/{order.exchange_order_id}/"
            )
            self._apply(order, raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Robinhood order sync failed: %s", exc)
        return order

    async def cancel_order(self, order: Order) -> bool:
        if not order.exchange_order_id:
            return False
        try:
            await self._request(
                "POST",
                f"/api/v1/crypto/trading/orders/{order.exchange_order_id}/cancel/",
            )
            order.status = OrderStatus.CANCELLED
            order.updated_at = utc_now()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Robinhood cancel failed: %s", exc)
            return False

    async def fetch_balances(self) -> Dict[str, float]:
        balances: Dict[str, float] = {}
        account = await self._request("GET", "/api/v1/crypto/trading/accounts/")
        if account.get("buying_power") is not None:
            balances[account.get("buying_power_currency", "USD")] = float(
                account["buying_power"]
            )
        holdings = await self._request("GET", "/api/v1/crypto/trading/holdings/")
        for row in holdings.get("results", []):
            qty = row.get("quantity_available_for_trading", row.get("total_quantity"))
            if qty is not None:
                balances[row["asset_code"]] = float(qty)
        return balances

    async def best_bid_ask(self, symbol: str) -> dict:
        """Live spread-inclusive quote for a symbol."""
        raw = await self._request(
            "GET",
            f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={to_rh_symbol(symbol)}",
        )
        results = raw.get("results") or [{}]
        return results[0]
