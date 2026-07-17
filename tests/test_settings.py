"""Config loading, env overrides, and validation."""

import json

import pytest

from src.config.settings import DEFAULT_CONFIG, load_config, validate_config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config("does_not_exist.json")
    assert config["mode"] == "paper"
    assert config["exchange"]["id"] == "kraken"
    assert config["trading"]["symbols"]


def test_user_file_deep_merges(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"trading": {"timeframe": "4h"}}))
    config = load_config(str(path))
    assert config["trading"]["timeframe"] == "4h"
    # Untouched keys keep defaults.
    assert config["trading"]["max_open_positions"] == 4
    assert config["risk"]["risk_per_trade"] == 0.0075


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCHANGE_ID", "coinbase")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    config = load_config(str(tmp_path / "missing.json"))
    assert config["exchange"]["id"] == "coinbase"
    assert config["notifications"]["telegram"]["enabled"] is True
    assert config["notifications"]["telegram"]["bot_token"] == "tok123"


def test_live_mode_requires_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "live"}))
    with pytest.raises(ValueError, match="live mode requires"):
        load_config(str(path))


def test_invalid_mode_rejected():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["mode"] = "yolo"
    with pytest.raises(ValueError, match="mode must be"):
        validate_config(config)


def test_risk_bounds_enforced():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["risk"]["risk_per_trade"] = 0.5  # 50% per trade: absurd
    with pytest.raises(ValueError, match="risk_per_trade"):
        validate_config(config)


def test_bad_symbol_format_rejected():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["trading"]["symbols"] = ["BTCUSD"]
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        validate_config(config)


def test_empty_symbols_rejected():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["trading"]["symbols"] = []
    with pytest.raises(ValueError, match="must not be empty"):
        validate_config(config)


def test_max_open_positions_floor():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["trading"]["max_open_positions"] = 0
    with pytest.raises(ValueError, match="max_open_positions"):
        validate_config(config)


def test_live_robinhood_requires_env_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ROBINHOOD_API_KEY", raising=False)
    monkeypatch.delenv("ROBINHOOD_PRIVATE_KEY", raising=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "live", "exchange": {"id": "robinhood"}}))
    with pytest.raises(ValueError, match="ROBINHOOD_API_KEY"):
        load_config(str(path))
    monkeypatch.setenv("ROBINHOOD_API_KEY", "k")
    monkeypatch.setenv("ROBINHOOD_PRIVATE_KEY", "p")
    assert load_config(str(path))["exchange"]["id"] == "robinhood"


def test_trading_mode_env_cannot_bypass_key_checks(tmp_path, monkeypatch):
    """TRADING_MODE=live alone must still fail closed on missing API keys —
    a refactor reordering _apply_env/validate_config would break this
    silently without the pin."""
    monkeypatch.setenv("TRADING_MODE", "live")
    for var in ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="live mode requires"):
        load_config(str(tmp_path / "missing.json"))


def test_discord_auto_enables_on_webhook(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    config = load_config(str(tmp_path / "missing.json"))
    assert config["notifications"]["discord"]["enabled"] is True


def test_dashboard_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("DASHBOARD_PORT", "9001")
    config = load_config(str(tmp_path / "missing.json"))
    assert config["dashboard"]["host"] == "0.0.0.0"
    assert config["dashboard"]["port"] == 9001
