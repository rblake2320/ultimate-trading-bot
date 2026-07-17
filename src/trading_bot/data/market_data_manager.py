"""Market data manager: real OHLCV candles and tickers via ccxt.

Public market data needs no API keys, so paper trading runs against live
exchange prices out of the box. Candles are cached per (symbol, timeframe)
and refreshed incrementally; only *closed* candles are exposed to
strategies (the forming candle is dropped) to keep live behavior identical
to backtests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def ohlcv_to_frame(rows: List[list]) -> pd.DataFrame:
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.astype(float)


class MarketDataManager:
    """Fetches and caches candles/tickers from one ccxt exchange."""

    def __init__(
        self,
        exchange_id: str = "kraken",
        timeframe: str = "1h",
        candle_limit: int = 500,
        ticker_ttl: float = 2.0,
        exchange: Optional[object] = None,
    ):
        if timeframe not in TIMEFRAME_MS:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}'. Use one of {sorted(TIMEFRAME_MS)}"
            )
        self.exchange_id = exchange_id
        self.timeframe = timeframe
        self.candle_limit = int(candle_limit)
        self.ticker_ttl = float(ticker_ttl)
        self._exchange = exchange  # injected (e.g. authenticated) or built lazily
        self._owns_exchange = exchange is None
        self._candles: Dict[Tuple[str, str], pd.DataFrame] = {}
        self._tickers: Dict[str, Tuple[float, float]] = {}  # symbol -> (price, mono)
        self._summaries: Dict[str, Tuple[dict, float]] = {}  # symbol -> (data, mono)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._exchange is None:
            import ccxt.async_support as ccxt_async

            if not hasattr(ccxt_async, self.exchange_id):
                raise ValueError(f"ccxt has no exchange '{self.exchange_id}'")
            # Keyless instance: public endpoints only.
            self._exchange = getattr(ccxt_async, self.exchange_id)(
                {"enableRateLimit": True, "timeout": 30_000}
            )
        await self._exchange.load_markets()
        logger.info(
            "Market data connected: %s (%d markets)",
            self.exchange_id,
            len(self._exchange.markets),
        )

    async def close(self) -> None:
        if self._exchange is not None and self._owns_exchange:
            await self._exchange.close()

    # ------------------------------------------------------------------ #
    # Candles
    # ------------------------------------------------------------------ #

    async def get_candles(
        self,
        symbol: str,
        timeframe: Optional[str] = None,
        limit: Optional[int] = None,
        closed_only: bool = True,
    ) -> pd.DataFrame:
        """Return cached candles, refreshing the tail from the exchange."""
        tf = timeframe or self.timeframe
        lim = limit or self.candle_limit
        key = (symbol, tf)

        async with self._lock:
            cached = self._candles.get(key)
            if cached is None or len(cached) < lim:
                rows = await self._fetch_ohlcv(symbol, tf, lim)
                df = ohlcv_to_frame(rows)
            else:
                # Incremental refresh: re-fetch the last few candles to pick
                # up the newly closed one and correct the previously forming
                # one.
                rows = await self._fetch_ohlcv(symbol, tf, 3)
                fresh = ohlcv_to_frame(rows)
                df = pd.concat([cached, fresh])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                df = df.tail(max(lim, len(cached)))
            self._candles[key] = df

        if closed_only:
            df = self._drop_forming_candle(df, tf)
        return df.copy()

    async def fetch_history(
        self, symbol: str, timeframe: str, since_ms: int, until_ms: Optional[int] = None
    ) -> pd.DataFrame:
        """Paginate full history from `since_ms` — used for backtests/ML."""
        tf_ms = TIMEFRAME_MS[timeframe]
        until_ms = until_ms or int(time.time() * 1000)
        all_rows: List[list] = []
        cursor = since_ms
        first_page = True
        while cursor < until_ms:
            rows = await self._exchange.fetch_ohlcv(
                symbol, timeframe, since=cursor, limit=720
            )
            if not rows:
                break
            if first_page and rows[0][0] > since_ms + 5 * tf_ms:
                # e.g. Kraken's OHLC endpoint only keeps the last 720 candles.
                logger.warning(
                    "%s only serves %s history from %s (requested %s) — "
                    "use another exchange (coinbase/binanceus) for deeper backtests",
                    self.exchange_id,
                    timeframe,
                    pd.Timestamp(rows[0][0], unit="ms", tz="UTC"),
                    pd.Timestamp(since_ms, unit="ms", tz="UTC"),
                )
            first_page = False
            all_rows.extend(rows)
            last_ts = rows[-1][0]
            if last_ts <= cursor:  # exchange returned no forward progress
                break
            cursor = last_ts + tf_ms
            await asyncio.sleep(self._exchange.rateLimit / 1000.0)
        df = ohlcv_to_frame(all_rows)
        return df[df.index <= pd.Timestamp(until_ms, unit="ms", tz="UTC")]

    def _drop_forming_candle(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if df.empty:
            return df
        tf_ms = TIMEFRAME_MS[timeframe]
        now_ms = time.time() * 1000
        last_open_ms = df.index[-1].value // 1_000_000
        if last_open_ms + tf_ms > now_ms:
            return df.iloc[:-1]
        return df

    async def _fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> List[list]:
        return await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    # ------------------------------------------------------------------ #
    # Tickers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _price_from_ticker(ticker: dict) -> float:
        price = float(ticker.get("last") or ticker.get("close") or 0.0)
        if price <= 0:
            bid, ask = ticker.get("bid"), ticker.get("ask")
            if bid and ask:
                price = (float(bid) + float(ask)) / 2.0
        return price

    async def get_current_price(self, symbol: str) -> float:
        """Last trade price with a short TTL cache to respect rate limits."""
        now = time.monotonic()
        cached = self._tickers.get(symbol)
        if cached and now - cached[1] < self.ticker_ttl:
            return cached[0]
        price = self._price_from_ticker(await self._exchange.fetch_ticker(symbol))
        if price <= 0:
            raise RuntimeError(f"No usable price in ticker for {symbol}")
        self._tickers[symbol] = (price, now)
        return price

    async def get_current_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Prices for many symbols in ONE exchange call where supported.

        Sequential per-symbol fetches sit behind the venue rate limiter
        (~1 req/s), so stop-loss latency would scale linearly with open
        positions. Symbols whose price cannot be obtained are omitted —
        callers must treat a missing key as \"no price this cycle\"."""
        now = time.monotonic()
        out: Dict[str, float] = {}
        missing: List[str] = []
        for symbol in symbols:
            cached = self._tickers.get(symbol)
            if cached and now - cached[1] < self.ticker_ttl:
                out[symbol] = cached[0]
            else:
                missing.append(symbol)
        if len(missing) > 1:
            try:
                tickers = await self._exchange.fetch_tickers(missing)
                for symbol in missing:
                    ticker = tickers.get(symbol)
                    if ticker is None:
                        continue
                    price = self._price_from_ticker(ticker)
                    if price > 0:
                        self._tickers[symbol] = (price, now)
                        out[symbol] = price
            except Exception as exc:  # noqa: BLE001 - not all venues batch tickers
                logger.debug("fetch_tickers unsupported/failed (%s) — per-symbol", exc)
        for symbol in missing:
            if symbol in out:
                continue
            try:
                out[symbol] = await self.get_current_price(symbol)
            except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
                logger.warning("No price for %s this cycle: %s", symbol, exc)
        return out

    @staticmethod
    def _summarize(symbol: str, ticker: dict) -> dict:
        last = ticker.get("last") or ticker.get("close")
        pct = ticker.get("percentage")
        if pct is None and ticker.get("open") and last:
            pct = (float(last) / float(ticker["open"]) - 1.0) * 100.0
        return {
            "symbol": symbol,
            "price": float(last) if last else None,
            "change_24h_pct": float(pct) if pct is not None else None,
            "bid": float(ticker["bid"]) if ticker.get("bid") else None,
            "ask": float(ticker["ask"]) if ticker.get("ask") else None,
            "volume_24h": (
                float(ticker["baseVolume"]) if ticker.get("baseVolume") else None
            ),
        }

    async def get_ticker_summary(self, symbol: str, ttl: float = 5.0) -> dict:
        """Live market snapshot for display: price, 24h change, bid/ask.

        Cached for `ttl` seconds so dashboard polling never queues up
        behind the exchange rate limiter."""
        now = time.monotonic()
        cached = self._summaries.get(symbol)
        if cached and now - cached[1] < ttl:
            return cached[0]
        summary = self._summarize(symbol, await self._exchange.fetch_ticker(symbol))
        self._summaries[symbol] = (summary, now)
        return summary

    async def get_ticker_summaries(self, symbols: List[str]) -> List[dict]:
        """Summaries for many symbols in ONE exchange call where supported
        (fetch_tickers), falling back to per-symbol requests."""
        now = time.monotonic()
        try:
            tickers = await self._exchange.fetch_tickers(symbols)
            out = []
            for symbol in symbols:
                ticker = tickers.get(symbol)
                if ticker is None:
                    continue
                summary = self._summarize(symbol, ticker)
                self._summaries[symbol] = (summary, now)
                out.append(summary)
            if out:
                return out
        except Exception as exc:  # noqa: BLE001 - not all venues batch tickers
            logger.debug("fetch_tickers unsupported/failed (%s) — per-symbol", exc)
        return [await self.get_ticker_summary(s) for s in symbols]

    async def get_spread(self, symbol: str) -> Optional[float]:
        """Relative bid/ask spread, if the exchange reports both sides."""
        ticker = await self._exchange.fetch_ticker(symbol)
        bid, ask = ticker.get("bid"), ticker.get("ask")
        if bid and ask and float(bid) > 0:
            return (float(ask) - float(bid)) / float(bid)
        return None

    async def health_check(self) -> bool:
        try:
            await self._exchange.fetch_time()
            return True
        except Exception as exc:  # noqa: BLE001 - report any connectivity issue
            logger.warning("Market data health check failed: %s", exc)
            return False

    def cached_candles(self, symbol: str, timeframe: Optional[str] = None):
        """Last cached candle frame without a network round-trip (or None)."""
        return self._candles.get((symbol, timeframe or self.timeframe))

    @property
    def markets(self) -> dict:
        return getattr(self._exchange, "markets", {}) or {}
