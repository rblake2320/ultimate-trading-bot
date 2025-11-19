# Trading Bot Tests

This directory contains the test suite for the Ultimate Trading Bot.

## Test Structure

```
tests/
├── conftest.py          # Shared fixtures and configuration
├── unit/                # Unit tests
│   ├── test_config.py
│   └── test_risk_manager.py
├── integration/         # Integration tests
└── e2e/                 # End-to-end tests
```

## Running Tests

### Run all tests
```bash
pytest
```

### Run specific test types
```bash
# Unit tests only
pytest -m unit

# Integration tests only
pytest -m integration

# End-to-end tests only
pytest -m e2e
```

### Run with coverage
```bash
pytest --cov=src --cov-report=html
```

### Run specific test file
```bash
pytest tests/unit/test_risk_manager.py
```

### Run specific test
```bash
pytest tests/unit/test_risk_manager.py::TestRiskManager::test_initialization
```

## Test Markers

- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.e2e` - End-to-end tests
- `@pytest.mark.slow` - Slow running tests
- `@pytest.mark.exchange` - Tests requiring exchange API
- `@pytest.mark.requires_config` - Tests requiring configuration files

## Writing Tests

### Unit Tests
Unit tests should test individual components in isolation:

```python
import pytest
from src.trading_bot.risk.risk_manager import RiskManager

class TestRiskManager:
    @pytest.mark.unit
    async def test_calculate_position_size(self, risk_manager):
        size = await risk_manager.calculate_position_size(signal)
        assert size > 0
```

### Integration Tests
Integration tests should test multiple components working together:

```python
@pytest.mark.integration
async def test_trading_workflow(trading_bot):
    # Test complete trading workflow
    await trading_bot.initialize()
    # ... test trading operations
```

### Async Tests
Use `@pytest.mark.asyncio` for async tests:

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result is not None
```

## Fixtures

Common fixtures are defined in `conftest.py`:
- `sample_config` - Test configuration
- `sample_market_data` - Mock market data
- `sample_trading_signal` - Mock trading signal

## Coverage

After running tests with coverage, view the HTML report:
```bash
open htmlcov/index.html
```

## Continuous Integration

Tests are automatically run on:
- Push to main branch
- Pull requests
- Scheduled daily runs
