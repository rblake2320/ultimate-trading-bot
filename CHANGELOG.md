# Changelog

## [1.0.0] - 2026-07-02

Ground-up rebuild. The previous version could not run: `core.py` imported
five modules that did not exist, and order execution was a simulation stub
that never touched an exchange.

### Added
- Causal technical-indicator library (pandas/numpy only): EMA, SMA, RSI,
  MACD, ATR, Bollinger, ADX/DI, stochastic, Donchian, OBV, VWAP, z-score,
  realized volatility.
- Strategy framework with vectorized causal signal frames shared between
  backtesting and live trading: momentum, mean reversion, breakout, and a
  regime-aware ensemble (per-bar regime classification with per-regime
  strategy weights; entries suppressed in downtrends/vol spikes).
- Event-driven backtester: next-open execution, taker fees, volatility-
  scaled slippage, intra-bar stop/target simulation (stop-first when
  ambiguous), Sharpe/Sortino/CAGR/max-DD/win-rate/profit-factor/exposure
  metrics, walk-forward split helper. Deterministic and lookahead-proof by
  test.
- Risk engine: ATR/stop-distance sizing, fractional-Kelly cap from
  realized history, portfolio heat cap, correlation guard from real return
  series, daily-loss halt (UTC rollover), max-drawdown halt, stoploss
  guard, consecutive-loss halt, per-symbol re-entry cooldown.
- Broker abstraction: PaperBroker (simulated fills against live exchange
  prices, fees/slippage/resting limit orders/balance checks) and
  LiveBroker (ccxt, precision + min-notional handling, retry with
  backoff). OrderManager with stale-order reaping.
- Market data manager: incremental candle cache, closed-candle guarantee,
  paginated deep-history fetch with venue-depth warning, ticker TTL cache.
- Optional LLM analyst (Ollama or Anthropic API): advisory veto on
  entries, time-boxed, fail-open, disabled by default.
- SQLite trade journal (orders, trades, equity snapshots, events).
- Telegram/Discord notifications via plain aiohttp.
- Two-key live-mode gate (`mode: live` + `TRADING_BOT_LIVE=YES`),
  KILL-file kill switch, emergency close-all.
- CLI: `trade`, `backtest`, `fetch`, `status`.
- Real-data test suite (400 days of committed 1h OHLCV) plus live
  integration tests against exchange public APIs and local Ollama.
- CI (GitHub Actions), Dockerfile, honest README.

### Changed
- Config schema redesigned (`config.example.json`); secrets come from the
  environment. Kraken is the default venue (was Binance).
- Requirements cut from ~40 packages (TensorFlow, PyTorch, MongoDB, ...)
  to 4 core runtime deps: ccxt, pandas, numpy, aiohttp.

### Removed
- Unimplementable marketing claims (sub-millisecond routing, MEV capture,
  "military-grade" everything) and the corresponding dead config sections.
- Stub order execution, hardcoded fake correlations, `asyncio.run` calls
  inside the event loop.

## [0.1.0] - 2025-08-12
- Initial architecture skeleton (exchange/order/risk manager drafts).
- chore: clean imports and add typing ignores
