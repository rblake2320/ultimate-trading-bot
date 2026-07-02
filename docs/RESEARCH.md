# Research: state of the art for algo trading bots (July 2026)

Findings from a web-research sweep that informed the 1.0.0 rebuild. Kept in
the repo so future design choices can be checked against their rationale.

## What the leading open-source bots do best

- **Freqtrade** (~49k stars, monthly releases): strategy-as-a-class on
  pandas frames, Optuna hyperopt, FreqAI (LightGBM default, retrains off
  the inference thread), dry-run default, Telegram + web UI, "Protections"
  circuit breakers. Complaints: config sprawl, one strategy per instance.
- **Hummingbot** (v2.15+): best connector architecture (normalized
  REST+WS adapter per venue). Built for market making; its lesson here is
  the broker/adapter abstraction.
- **Jesse**: reputation built entirely on zero-lookahead backtests. Our
  causal-signal-frame design chases the same guarantee.
- **NautilusTrader**: Rust event core; the killer feature every 2026
  comparison cites is *identical code in backtest and live*. Adopted here
  as the central architectural principle.
- **OctoBot**: proves low-friction onboarding is a demanded feature.

## Decisions taken for this repo (with reasons)

| Decision | Basis |
|---|---|
| ccxt ≥4.5 for connectivity | Still the standard; ccxt.pro WS merged into free package |
| Kraken + Coinbase primaries (US) | Kraken best US API venue 2026 (incl. CFTC-regulated perps); Coinbase Advanced Trade actively invested; Binance.US recovered but thin; Bybit still prohibited for US |
| Own pandas/numpy indicator layer | pandas-ta under archival threat (July 2026 ultimatum); TA-Lib wheels now fine but an extra native dep we don't need |
| No LSTM/deep learning in signal path | 2025–26 consensus: LightGBM/XGBoost on engineered features match or beat LSTMs on hourly/daily bars at a fraction of the cost; deep nets only pay off on order-book tick data |
| LLM as advisory veto only | TradingAgents/Hummingbot-OpenRouter "LLM sidecar" pattern; blocking bad trades needs less precision than picking good ones; single-run +7%/30d anecdotes contradicted by longer-horizon academic skepticism |
| Vol-scaled slippage in backtests | Analysis of 1,243 forum posts: 57% of backtest-to-live failures blamed on slippage under-modeling |
| Freqtrade-style protections + additions | StoplossGuard/MaxDrawdown/Cooldown are the de-facto reference; consecutive-loss counters and hard daily loss limits are the gaps we filled |
| Correlation caps from real returns | No major open bot does this well — alt pairs are ~one beta trade to BTC in stress |
| Paper-first, two-key live gate | Freqtrade's dry_run default, strengthened per 2026 build guides |
| Fractional Kelly 25–50%, ≤1–2% risk/trade, ATR sizing | Universal position-sizing consensus stack |

## Known limits / future work

- **Funding-rate awareness**: only relevant if perps support is added;
  backtests of perp strategies without funding flows are fiction.
- **WebSocket data plane**: REST polling of closed candles is correct for
  candle strategies at 1h; move to ccxt.pro `watch_*` if sub-minute
  timeframes are ever needed, with REST reconciliation after disconnects.
- **Prometheus metrics**: journal tables exist; exporting them is wiring,
  not architecture.
- **LightGBM meta-filter**: behind the PredictionEngine seam, train on
  journaled live/paper history once there is enough of it. Walk-forward
  retraining only; no pretrained-model theater.
- **Kraken history depth**: public OHLC serves only the last ~720 candles;
  deep backtests must pull from binanceus/coinbase (the CLI supports
  `--exchange`).
