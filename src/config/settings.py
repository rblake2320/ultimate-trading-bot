"""
Configuration management for the trading bot
"""

import json
import os
from typing import Dict, Any
import logging


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """
    Load configuration from JSON file

    Args:
        config_path: Path to configuration file

    Returns:
        Configuration dictionary
    """
    try:
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, "r") as f:
            config = json.load(f)

        # Validate required sections
        required_sections = [
            "exchanges",
            "trading",
            "risk_management",
            "ml_models",
            "data_sources",
            "notifications",
        ]

        for section in required_sections:
            if section not in config:
                raise ValueError(f"Missing required configuration section: {section}")

        # Load environment variables for sensitive data
        config = _load_environment_variables(config)

        return config

    except Exception as e:
        logging.error(f"Error loading configuration: {e}")
        raise


def _load_environment_variables(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load sensitive configuration from environment variables
    """
    # Exchange API keys
    for exchange_name, exchange_config in config["exchanges"].items():
        if "api_key" in exchange_config:
            env_key = f"{exchange_name.upper()}_API_KEY"
            if os.getenv(env_key):
                exchange_config["api_key"] = os.getenv(env_key)

        if "api_secret" in exchange_config:
            env_secret = f"{exchange_name.upper()}_API_SECRET"
            if os.getenv(env_secret):
                exchange_config["api_secret"] = os.getenv(env_secret)

    # Notification tokens
    if "telegram" in config["notifications"]:
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            config["notifications"]["telegram"]["bot_token"] = os.getenv(
                "TELEGRAM_BOT_TOKEN"
            )
        if os.getenv("TELEGRAM_CHAT_ID"):
            config["notifications"]["telegram"]["chat_id"] = os.getenv(
                "TELEGRAM_CHAT_ID"
            )

    return config


def validate_config(config: Dict[str, Any]) -> bool:
    """
    Validate configuration parameters

    Args:
        config: Configuration dictionary

    Returns:
        True if valid, raises exception if invalid
    """
    # Validate trading parameters
    trading_config = config["trading"]

    if (
        trading_config["max_position_size"] <= 0
        or trading_config["max_position_size"] > 1
    ):
        raise ValueError("max_position_size must be between 0 and 1")

    if trading_config["stop_loss_percentage"] <= 0:
        raise ValueError("stop_loss_percentage must be positive")

    if trading_config["take_profit_percentage"] <= 0:
        raise ValueError("take_profit_percentage must be positive")

    # Validate risk management
    risk_config = config["risk_management"]

    if risk_config["max_portfolio_risk"] <= 0 or risk_config["max_portfolio_risk"] > 1:
        raise ValueError("max_portfolio_risk must be between 0 and 1")

    if risk_config["drawdown_limit"] <= 0 or risk_config["drawdown_limit"] > 1:
        raise ValueError("drawdown_limit must be between 0 and 1")

    # Validate ML models
    ml_config = config["ml_models"]

    if ml_config["ensemble_threshold"] <= 0 or ml_config["ensemble_threshold"] > 1:
        raise ValueError("ensemble_threshold must be between 0 and 1")

    return True
