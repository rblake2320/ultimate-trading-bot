"""
Pytest configuration and fixtures
"""

import pytest
import asyncio
from typing import Dict


@pytest.fixture
def sample_config() -> Dict:
    """Sample configuration for testing"""
    return {
        "exchanges": {
            "binance": {
                "api_key": "test_api_key",
                "api_secret": "test_api_secret",
                "testnet": True,
                "enabled": True,
            }
        },
        "trading": {
            "base_currency": "USDT",
            "trading_pairs": ["BTC/USDT", "ETH/USDT"],
            "max_position_size": 0.1,
            "stop_loss_percentage": 0.02,
            "take_profit_percentage": 0.05,
            "max_daily_loss": 0.05,
            "max_open_positions": 5,
        },
        "risk_management": {
            "max_portfolio_risk": 0.02,
            "position_sizing_method": "kelly",
            "volatility_lookback": 20,
            "correlation_threshold": 0.7,
            "drawdown_limit": 0.1,
        },
        "ml_models": {
            "ensemble_threshold": 0.7,
            "retrain_frequency": "daily",
            "lookback_period": 100,
            "features": {
                "technical_indicators": True,
                "sentiment_analysis": False,
                "on_chain_metrics": False,
                "macro_indicators": False,
            },
        },
        "data_sources": {
            "market_data": {"primary": "binance", "backup": "coinbase"},
            "sentiment": {},
            "on_chain": {},
        },
        "notifications": {
            "telegram": {"enabled": False},
            "email": {"enabled": False},
        },
        "logging": {
            "level": "INFO",
            "file": "logs/trading_bot.log",
        },
        "database": {
            "type": "sqlite",
            "path": ":memory:",
        },
    }


@pytest.fixture
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_market_data() -> Dict:
    """Sample market data for testing"""
    return {
        "prices": {
            "BTC/USDT": 50000.0,
            "ETH/USDT": 3000.0,
            "BNB/USDT": 500.0,
        },
        "tickers": {
            "BTC/USDT": {
                "last": 50000.0,
                "volume": 1000000.0,
                "high": 51000.0,
                "low": 49000.0,
                "change": 0.02,
            }
        },
    }


@pytest.fixture
def sample_trading_signal() -> Dict:
    """Sample trading signal for testing"""
    return {
        "symbol": "BTC/USDT",
        "action": "buy",
        "confidence": 0.75,
        "price": 50000.0,
    }
