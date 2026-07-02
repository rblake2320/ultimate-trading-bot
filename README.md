# Ultimate Trading Bot

An AI-assisted crypto trading bot built for honesty over hype: regime-aware
ensemble strategies, a lookahead-proof backtester that shares code with live
trading, hard-nosed risk controls, and paper trading against **live exchange
prices** as the default mode.

> **Reality check**: no bot prints money. Target realistic risk-adjusted
> returns, verify everything in backtests and paper mode, and never trade
> money you can't lose. This project makes it hard to fool yourself — that
> is its main feature.

## What it actually does

- **Strategies** — momentum (EMA cross + ADX filter), mean reversion
  (Bollinger + RSI, trend-guarded), Donchian breakout with volume
  confirmation, combined by a **regime-aware ensemble**: each bar is
  classified (trending up/down, ranging, high-volatility) and strategy
  votes are weighted accordingly. Entries are suppressed entirely in
  downtrends and volatility spikes (long-only spot).
- **Backtest/live parity** — strategies emit vectorized, *causal* signal
  frames. The same code drives the backtester and the live loop, and the
  same risk engine gates entries in both. Signals generated on bar *i*
  execute at bar *i+1*'s open. Tests enforce the no-lookahead property.
- **Risk engine** — ATR/stop-distance position sizing (≤0.75% equity risk
  per trade by default), fractional-Kelly cap learned from realized trade
  history, portfolio heat cap, **correlation guard computed from real
  return series** (refuses a second position that's ≥0.85 correlated with
  what you hold), daily-loss halt with UTC rollover, max-drawdown halt,
  stoploss guard (N stops in a window → cooldown), consecutive-loss halt,
  re-entry cooldown per symbol.
- **Execution** — one `Broker` interface. `PaperBroker` simulates fills
  against live exchange prices (fees, slippage, resting limit orders,
  balance checks). `LiveBroker` trades real money through ccxt with
  precision/min-notional handling and network retries.
- **LLM analyst (optional, off by default)** — an advisory *veto* sidecar,
  never a signal source. Reviews proposed entries with compact numeric
  context via local Ollama (private) or the Anthropic API. Strictly
  time-boxed and fail-open: if the model is slow or down, the quantitative
  decision stands.
- **Ops** — SQLite trade journal (orders, trades, equity snapshots,
  events), Telegram/Discord notifications, `KILL`-file kill switch,
  emergency close-all, stale-order reaping, health checks.

## Safety model (read this)

1. **Paper mode is the default.** It uses real, live market data — only the
   fills are simulated.
2. **Live trading is two-key**: `"mode": "live"` in the config **and**
   `TRADING_BOT_LIVE=YES` in the environment. A config typo cannot trade
   real money.
3. The risk engine can halt all entries at any time; exits are never
   blocked.
4. Creating a file named `KILL` in the working directory triggers an
   emergency shutdown (cancel orders, optionally close positions, notify).

## Quick start

```bash
git clone https://github.com/rblake2320/ultimate-trading-bot.git
cd ultimate-trading-bot
pip install -r requirements.txt

# Paper-trade BTC/ETH/SOL on Kraken live data (no API keys needed):
python main.py trade

# Backtest the ensemble on real history (Binance.US serves deep history):
python main.py backtest --symbols BTC/USDT ETH/USDT --days 365 \
    --timeframe 1h --exchange binanceus --trades 10

# Compare a single strategy:
python main.py backtest --strategy momentum --days 180 --exchange binanceus

# Download history to CSV:
python main.py fetch --symbols BTC/USDT --days 400 --exchange binanceus

# Performance so far (reads the journal):
python main.py status
```

Configuration: copy `config.example.json` to `config.json` and edit.
Secrets belong in the environment (`EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`,
`TELEGRAM_BOT_TOKEN`, ...), never in the file.

### Going live (only after paper trading works for you)

```bash
export EXCHANGE_API_KEY=...      # from your exchange
export EXCHANGE_API_SECRET=...
export TRADING_BOT_LIVE=YES      # the second key
python main.py trade --live
```

Venue notes (2026, US): **Kraken** and **Coinbase Advanced Trade** are the
sane primaries; `binanceus` and `okx` work via ccxt. Kraken's public OHLC
endpoint only serves the most recent ~720 candles — backtest against
`--exchange binanceus` or `coinbase` for depth.

## Architecture

```
main.py                       CLI: trade | backtest | fetch | status
src/config/settings.py       defaults < config.json < environment
src/trading_bot/
  core.py                    engine: candle loop, position loop, kill switch
  models.py                  Signal/Order/Position/TradeRecord dataclasses
  indicators/ta.py           causal pandas/numpy TA (no external TA lib)
  strategies/                base + momentum/mean_reversion/breakout/ensemble
  ml/prediction_engine.py    strategy runner + regime tagging + LLM veto
  ai/llm_analyst.py          optional Ollama/Anthropic advisory veto
  risk/risk_manager.py       sizing, protections, correlation guard
  execution/brokers.py       Broker interface: PaperBroker / LiveBroker + OrderManager
  data/market_data_manager.py ccxt candles/tickers, closed-candle guarantee
  portfolio/portfolio_manager.py positions, PnL, equity
  backtest/backtester.py     event-driven, fees + vol-scaled slippage, walk-forward
  persistence/journal.py     SQLite journal
  notifications/             Telegram / Discord / log
tests/                       real-data test suite (committed 400d of 1h OHLCV)
```

## Testing

The suite uses **real market data** (400 days of Binance.US 1h candles,
committed under `tests/data/`) and real components — no mocks. Live
integration tests hit exchange public APIs and a local Ollama if present.

```bash
pytest -q                      # everything
pytest -m "not integration" -q # offline-only (still real data)
```

Key invariants under test: indicators and strategies are causal (appending
future data never changes past values), backtests are deterministic, the
no-lookahead property holds end-to-end, costs reduce returns, stops are
enforced, protections halt trading, and the correlation guard fires on real
BTC/ETH return series.

## Honest limitations

- Long-only spot. No shorting, no leverage, no perps/funding — by design,
  those multiply ways to lose.
- The ensemble's default parameters are sensible, not optimized. Run your
  own backtests and walk-forward validation (`walk_forward_splits`) before
  trusting any configuration.
- Backtest results overstate live performance even with fee/slippage
  modeling. Paper-trade for weeks before going live.
- No ML model ships in the signal path: transparent rules only. A LightGBM
  meta-filter is a planned extension once journaled live history exists to
  train on honestly.

## License

MIT — see [LICENSE](LICENSE).

**Disclaimer**: educational and research software. Cryptocurrency trading
involves substantial risk of loss. Past performance does not guarantee
future results. You are responsible for your own trades and for compliance
with your local regulations.
