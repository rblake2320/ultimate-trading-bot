"""Configuration loading and validation.

Precedence: environment variables > config.json > built-in defaults.
Secrets (API keys, tokens) should live in the environment or a .env file,
never in config.json.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "paper",
    "kill_file": "KILL",
    "exchange": {
        "id": "kraken",
        "api_key": "",
        "api_secret": "",
        "password": "",
        "testnet": False,
        "taker_fee": 0.0026,
        "maker_fee": 0.0016,
    },
    "trading": {
        "quote_currency": "USD",
        "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "timeframe": "1h",
        "poll_seconds": 30,
        "price_check_seconds": 10,
        "max_open_positions": 4,
        "paper_starting_cash": 10000.0,
        "trailing_stop_pct": 0.0,
        "stale_order_seconds": 300,
        "emergency_close_positions": True,
    },
    "risk": {
        "risk_per_trade": 0.0075,
        "max_position_pct": 0.20,
        "max_portfolio_heat": 0.05,
        "kelly_fraction": 0.5,
        "max_daily_loss_pct": 0.03,
        "max_drawdown_pct": 0.15,
        "stoploss_guard_count": 4,
        "stoploss_guard_window_hours": 24,
        "max_consecutive_losses": 5,
        "halt_cooldown_hours": 12,
        "reentry_cooldown_minutes": 60,
        "correlation_threshold": 0.85,
        "min_notional": 10.0,
    },
    "signals": {
        "strategy": "ensemble",
        "strategy_params": {},
        "min_confidence": 0.55,
        "llm_analyst": {
            "enabled": False,
            "provider": "ollama",
            "model": "gemma4:latest",
            "ollama_url": "http://localhost:11434",
            "timeout_seconds": 20,
            "min_veto_confidence": 0.6,
        },
    },
    "notifications": {
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "discord": {"enabled": False, "webhook_url": ""},
    },
    "journal": {"db_path": "data/trading_bot.db"},
    "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8899},
    "logging": {"level": "INFO", "file": "logs/trading_bot.log"},
}

# Environment overrides: VAR -> (config path, cast)
ENV_OVERRIDES = {
    "EXCHANGE_ID": (("exchange", "id"), str),
    "EXCHANGE_API_KEY": (("exchange", "api_key"), str),
    "EXCHANGE_API_SECRET": (("exchange", "api_secret"), str),
    "EXCHANGE_PASSWORD": (("exchange", "password"), str),
    "TELEGRAM_BOT_TOKEN": (("notifications", "telegram", "bot_token"), str),
    "TELEGRAM_CHAT_ID": (("notifications", "telegram", "chat_id"), str),
    "DISCORD_WEBHOOK_URL": (("notifications", "discord", "webhook_url"), str),
    "TRADING_MODE": (("mode",), str),
    "DASHBOARD_HOST": (("dashboard", "host"), str),
    "DASHBOARD_PORT": (("dashboard", "port"), int),
}


def _deep_merge(base: Dict, override: Dict) -> Dict:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _apply_env(config: Dict) -> Dict:
    for var, (path, cast) in ENV_OVERRIDES.items():
        raw = os.getenv(var)
        if raw is None or raw == "":
            continue
        node = config
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = cast(raw)
    # Auto-enable notification channels when their secrets are present.
    tg = config["notifications"]["telegram"]
    if tg.get("bot_token") and tg.get("chat_id"):
        tg["enabled"] = True
    dc = config["notifications"]["discord"]
    if dc.get("webhook_url"):
        dc["enabled"] = True
    return config


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """Load config with defaults; a missing file just means defaults."""
    config = deepcopy(DEFAULT_CONFIG)
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            user_config = json.load(fh)
        config = _deep_merge(config, user_config)
        logger.info("Loaded config from %s", path)
    else:
        logger.info("No %s found — using defaults (paper mode)", config_path)
    config = _apply_env(config)
    validate_config(config)
    return config


def validate_config(config: Dict[str, Any]) -> None:
    mode = config.get("mode")
    if mode not in ("paper", "live"):
        raise ValueError(f"mode must be 'paper' or 'live', got {mode!r}")

    trading = config["trading"]
    if not trading.get("symbols"):
        raise ValueError("trading.symbols must not be empty")
    for symbol in trading["symbols"]:
        if "/" not in symbol:
            raise ValueError(f"symbol '{symbol}' must be BASE/QUOTE format")
    if trading["max_open_positions"] < 1:
        raise ValueError("trading.max_open_positions must be >= 1")

    risk = config["risk"]
    for key, low, high in [
        ("risk_per_trade", 0.0, 0.05),
        ("max_position_pct", 0.0, 1.0),
        ("max_portfolio_heat", 0.0, 0.5),
        ("kelly_fraction", 0.0, 1.0),
        ("max_daily_loss_pct", 0.0, 0.5),
        ("max_drawdown_pct", 0.0, 0.9),
    ]:
        value = risk[key]
        if not (low < value <= high):
            raise ValueError(
                f"risk.{key} must be in ({low}, {high}], got {value}"
            )

    if mode == "live":
        exchange = config["exchange"]
        if exchange.get("id") == "robinhood":
            if not (
                os.getenv("ROBINHOOD_API_KEY") and os.getenv("ROBINHOOD_PRIVATE_KEY")
            ):
                raise ValueError(
                    "live robinhood mode requires ROBINHOOD_API_KEY and "
                    "ROBINHOOD_PRIVATE_KEY in the environment "
                    "(generate a keypair with 'python main.py keygen')"
                )
        elif not exchange.get("api_key") or not exchange.get("api_secret"):
            raise ValueError(
                "live mode requires exchange.api_key and exchange.api_secret "
                "(set EXCHANGE_API_KEY / EXCHANGE_API_SECRET)"
            )
