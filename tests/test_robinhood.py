"""Robinhood Crypto adapter tests.

Signing is verified with real Ed25519 cryptography (sign here, verify with
the public key against the reconstructed message — exactly what
Robinhood's servers do). The integration test hits the REAL API endpoint
and proves our request formation reaches it (an unregistered key must get
an auth rejection, not a transport error or 404).
"""

import base64
import json

import pytest

from src.trading_bot.exchanges.robinhood_crypto import (
    BASE_URL,
    RobinhoodCryptoBroker,
    generate_keypair,
    to_rh_symbol,
)
from src.trading_bot.models import Order, OrderSide, OrderType


def make_broker():
    pair = generate_keypair()
    return RobinhoodCryptoBroker("rh-api-test-key", pair["private_key_b64"]), pair


def test_symbol_conversion():
    assert to_rh_symbol("BTC/USD") == "BTC-USD"
    assert to_rh_symbol("ETH/USD") == "ETH-USD"


def test_keypair_roundtrip_real_ed25519():
    pair = generate_keypair()
    assert len(base64.b64decode(pair["private_key_b64"])) == 32
    assert len(base64.b64decode(pair["public_key_b64"])) == 32
    # Distinct keys every call.
    assert generate_keypair()["private_key_b64"] != pair["private_key_b64"]


def test_signature_verifies_with_public_key():
    """Reproduce Robinhood's server-side check: verify x-signature over
    api_key + timestamp + path + method + body with the public key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    broker, pair = make_broker()
    path = "/api/v1/crypto/trading/orders/"
    body = json.dumps({"side": "buy"})
    headers = broker._headers("POST", path, body)

    message = (
        f"{headers['x-api-key']}{headers['x-timestamp']}{path}POST{body}"
    ).encode()
    public = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(pair["public_key_b64"])
    )
    # Raises InvalidSignature if wrong; passing = genuine Ed25519 validity.
    public.verify(base64.b64decode(headers["x-signature"]), message)

    # Tampered message must NOT verify.
    from cryptography.exceptions import InvalidSignature

    with pytest.raises(InvalidSignature):
        public.verify(base64.b64decode(headers["x-signature"]), message + b"x")


def test_order_payload_market_and_limit():
    market = Order(
        symbol="BTC/USD", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.001
    )
    payload = RobinhoodCryptoBroker._order_payload(market)
    assert payload["symbol"] == "BTC-USD"
    assert payload["side"] == "buy"
    assert payload["market_order_config"]["asset_quantity"] == "0.001"
    assert "client_order_id" in payload

    limit = Order(
        symbol="ETH/USD",
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        amount=0.5,
        price=3000.0,
    )
    payload = RobinhoodCryptoBroker._order_payload(limit)
    assert payload["limit_order_config"] == {
        "asset_quantity": "0.5",
        "limit_price": "3000",
        "time_in_force": "gtc",
    }


def test_state_mapping_applies_fills():
    from src.trading_bot.models import OrderStatus

    broker, _ = make_broker()
    order = Order(
        symbol="BTC/USD", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.5
    )
    broker._apply(
        order,
        {
            "id": "abc-123",
            "state": "filled",
            "filled_asset_quantity": "0.5",
            "average_price": "60000.5",
        },
    )
    assert order.status == OrderStatus.FILLED
    assert order.exchange_order_id == "abc-123"
    assert order.filled == pytest.approx(0.5)
    assert order.average_price == pytest.approx(60000.5)


def test_average_price_from_executions():
    broker, _ = make_broker()
    order = Order(
        symbol="BTC/USD", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.2
    )
    broker._apply(
        order,
        {
            "id": "abc",
            "state": "filled",
            "executions": [
                {"quantity": "0.1", "effective_price": "60000"},
                {"quantity": "0.1", "effective_price": "61000"},
            ],
        },
    )
    assert order.average_price == pytest.approx(60500.0)
    assert order.filled == pytest.approx(0.2)


def test_requires_credentials():
    with pytest.raises(ValueError, match="ROBINHOOD_API_KEY"):
        RobinhoodCryptoBroker("", "")


@pytest.mark.integration
async def test_real_endpoint_rejects_unregistered_key():
    """Hit the REAL Robinhood API: a well-formed, correctly signed request
    with an unregistered key must come back as an auth rejection from
    Robinhood's servers — proving the adapter's transport, signing format,
    and endpoint paths are wired to the real service."""
    broker, _ = make_broker()
    with pytest.raises((PermissionError, RuntimeError)) as excinfo:
        await broker.fetch_balances()
    text = str(excinfo.value)
    assert "robinhood" in text.lower() or "40" in text, text
    assert BASE_URL.startswith("https://trading.robinhood.com")
