"""
Unit tests for configuration management
"""

import pytest
import os
import json
import tempfile
from src.config.settings import load_config, validate_config


class TestConfigManagement:
    """Test suite for configuration management"""

    @pytest.mark.unit
    def test_load_config_file_not_found(self):
        """Test loading non-existent config file raises error"""
        with pytest.raises(FileNotFoundError):
            load_config("non_existent_config.json")

    @pytest.mark.unit
    def test_validate_config_valid(self, sample_config):
        """Test validation of valid configuration"""
        assert validate_config(sample_config) is True

    @pytest.mark.unit
    def test_validate_config_invalid_position_size(self, sample_config):
        """Test validation rejects invalid position size"""
        sample_config["trading"]["max_position_size"] = 1.5

        with pytest.raises(ValueError, match="max_position_size"):
            validate_config(sample_config)

    @pytest.mark.unit
    def test_validate_config_invalid_stop_loss(self, sample_config):
        """Test validation rejects invalid stop loss"""
        sample_config["trading"]["stop_loss_percentage"] = -0.02

        with pytest.raises(ValueError, match="stop_loss_percentage"):
            validate_config(sample_config)

    @pytest.mark.unit
    def test_validate_config_invalid_risk(self, sample_config):
        """Test validation rejects invalid portfolio risk"""
        sample_config["risk_management"]["max_portfolio_risk"] = 1.5

        with pytest.raises(ValueError, match="max_portfolio_risk"):
            validate_config(sample_config)

    @pytest.mark.unit
    def test_load_config_with_valid_file(self, sample_config):
        """Test loading configuration from valid file"""
        # Create temporary config file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(sample_config, f)
            temp_file = f.name

        try:
            # Load config
            loaded_config = load_config(temp_file)

            assert loaded_config is not None
            assert "exchanges" in loaded_config
            assert "trading" in loaded_config

        finally:
            # Cleanup
            os.unlink(temp_file)
