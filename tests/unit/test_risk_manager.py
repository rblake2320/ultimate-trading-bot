"""
Unit tests for Risk Manager
"""

import pytest
from src.trading_bot.risk.risk_manager import RiskManager


class TestRiskManager:
    """Test suite for RiskManager"""

    @pytest.fixture
    async def risk_manager(self, sample_config):
        """Create RiskManager instance for testing"""
        manager = RiskManager(sample_config["risk_management"])
        await manager.initialize()
        return manager

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_initialization(self, risk_manager):
        """Test risk manager initializes correctly"""
        assert risk_manager is not None
        assert risk_manager.max_portfolio_risk == 0.02
        assert risk_manager.drawdown_limit == 0.1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_assess_trade_approval(self, risk_manager, sample_trading_signal):
        """Test trade assessment returns approval"""
        result = await risk_manager.assess_trade(sample_trading_signal)

        assert "approved" in result
        assert "reason" in result
        assert isinstance(result["approved"], bool)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_calculate_position_size(self, risk_manager, sample_trading_signal):
        """Test position size calculation"""
        size = await risk_manager.calculate_position_size(sample_trading_signal)

        assert isinstance(size, float)
        assert size > 0
        assert size <= risk_manager.max_position_size

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_portfolio_risk_calculation(self, risk_manager):
        """Test portfolio risk calculation"""
        risk = await risk_manager.calculate_portfolio_risk()

        assert isinstance(risk, float)
        assert risk >= 0

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_position(self, risk_manager):
        """Test position update"""
        await risk_manager.update_position("BTC/USDT", 0.05)

        assert "BTC/USDT" in risk_manager.position_sizes
        assert risk_manager.position_sizes["BTC/USDT"] == 0.05

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_drawdown_limit_exceeded(self, risk_manager, sample_trading_signal):
        """Test rejection when drawdown limit exceeded"""
        # Simulate high drawdown
        risk_manager.current_drawdown = 0.15

        result = await risk_manager.assess_trade(sample_trading_signal)

        assert result["approved"] is False
        assert "drawdown" in result["reason"].lower()
