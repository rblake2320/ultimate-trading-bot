# Changelog

## [Unreleased] - 2026-07-17

Production-readiness pass driven by a 7-dimension adversarially-verified
audit (55 confirmed findings).

### Fixed
- **Live double-order risk**: LiveBroker no longer blind-retries after a
  network timeout — orders carry a `clientOrderId` and every retry first
  searches the venue for the accepted order.
- **Stopless positions**: entries that fill asynchronously via the poll
  loop now receive the risk decision's stop-loss/take-profit (previously
  only synchronous fills did).
- **Duplicate close orders**: the stop/take-profit check no longer re-fires
  a full-size sell every 10s while a close order is still working.
- **Loop error isolation**: one failing ticker no longer aborts stop checks
  for the remaining positions; one failing emergency close no longer
  abandons the rest.
- Live `TradeRecord.pnl` is now fee-inclusive, matching the backtester
  (Kelly sizing and loss halts previously ran on rosier numbers live).
- `trade --live` re-validates the config, so the flag cannot bypass
  live-mode API-key checks.
- Paper orders rejected at cross time no longer leak in `open_orders`;
  immediate fills are journaled once, not twice; dashboard port fallbacks
  unified at 8899.

### Security
- Dashboard escapes all journal/exchange-derived strings (stored-XSS path
  from hostile exchange responses), sends CSP/nosniff headers, pins the
  Host header on loopback binds (DNS-rebinding), SRI-pins Chart.js, and
  warns loudly on non-loopback binds. Telegram/Discord secrets are
  redacted from notification failure logs. Docker container runs as a
  non-root user.

### Performance
- Position/maintenance/signal/status paths fetch prices via one batched
  `fetch_tickers` call instead of per-symbol requests behind the venue
  rate limiter — stop-loss latency no longer scales with position count.

### Added
- 30 offline tests (76 → 106) covering the previously untested safety
  gates: two-key live gate, stop propagation, single-shot closes,
  LiveBroker idempotency, closed-candle invariant, risk rejection
  branches, settings validation, LLM fail-open (moved into the gating
  suite).
- CI: least-privilege token, concurrency groups, Python 3.13, coverage
  reporting, pip-audit job, pinned ruff. Dependabot for pip, actions, and
  docker. `.dockerignore`, `.env.example`, Docker `HEALTHCHECK`/`EXPOSE`,
  `DASHBOARD_HOST`/`DASHBOARD_PORT` env overrides.

### Known gaps (need a human design decision — see todo.md)
- No position/state reconstruction on restart: pre-existing live positions
  become unmanaged.
- Backtester fill-model optimism (gap-through stops fill at the stop
  price; entry-bar stops skipped; sizing marks at fill-bar close).

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
