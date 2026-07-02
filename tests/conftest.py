"""Shared fixtures.

Test data is REAL market history (Binance.US 1h OHLCV, 400 days), fetched
with `python main.py fetch` and committed under tests/data/. Refresh it
the same way whenever you like — the tests assert structural properties,
not specific prices.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

DATA_DIR = Path(__file__).parent / "data"


def load_frame(name: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / name, index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index, tz="UTC") if df.index.tz is None else df.index
    return df[["open", "high", "low", "close", "volume"]].astype(float)


@pytest.fixture(scope="session")
def btc_df() -> pd.DataFrame:
    return load_frame("BTC_USDT_1h.csv")


@pytest.fixture(scope="session")
def eth_df() -> pd.DataFrame:
    return load_frame("ETH_USDT_1h.csv")
