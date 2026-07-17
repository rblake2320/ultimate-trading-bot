"""Deterministic MarketDataManager guarantees — the closed-candle invariant.

The live==backtest parity of this system rests on strategies never seeing
the forming candle. These functions are pure; testing them needs no
exchange (the integration suite separately checks the live behavior).
"""

import time

import pytest

from src.trading_bot.data.market_data_manager import (
    TIMEFRAME_MS,
    MarketDataManager,
    ohlcv_to_frame,
)


def make_rows(opens_ms):
    return [[ts, 100.0, 110.0, 90.0, 105.0, 1.0] for ts in opens_ms]


class TestOhlcvToFrame:
    def test_dedup_keeps_last_and_sorts(self):
        base = 1_700_000_000_000
        rows = make_rows([base + 3_600_000, base])
        rows.append([base, 1.0, 2.0, 0.5, 1.5, 9.0])  # duplicate ts, new data
        df = ohlcv_to_frame(rows)
        assert len(df) == 2
        assert df.index.is_monotonic_increasing
        # keep="last": the corrected candle wins.
        assert df.iloc[0]["close"] == pytest.approx(1.5)


class TestClosedCandleGuarantee:
    def test_forming_candle_dropped(self):
        data = MarketDataManager(exchange_id="kraken", timeframe="1h")
        tf = TIMEFRAME_MS["1h"]
        now_ms = int(time.time() * 1000)
        forming_open = (now_ms // tf) * tf  # current hour: still forming
        df = ohlcv_to_frame(make_rows([forming_open - 2 * tf, forming_open - tf, forming_open]))
        out = data._drop_forming_candle(df, "1h")
        assert len(out) == 2
        assert out.index[-1].value // 1_000_000 == forming_open - tf

    def test_closed_candles_kept(self):
        data = MarketDataManager(exchange_id="kraken", timeframe="1h")
        tf = TIMEFRAME_MS["1h"]
        now_ms = int(time.time() * 1000)
        last_closed = (now_ms // tf) * tf - tf
        df = ohlcv_to_frame(make_rows([last_closed - tf, last_closed]))
        out = data._drop_forming_candle(df, "1h")
        assert len(out) == 2

    def test_empty_frame_passthrough(self):
        data = MarketDataManager(exchange_id="kraken", timeframe="1h")
        df = ohlcv_to_frame([])
        assert data._drop_forming_candle(df, "1h").empty


def test_unsupported_timeframe_rejected():
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        MarketDataManager(exchange_id="kraken", timeframe="7m")
