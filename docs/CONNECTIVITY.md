# Connecting the bot to brokers and exchanges (July 2026)

The bot's `Broker` interface (`create_order / cancel_order / sync_order /
fetch_balances`) is the single extension point. Anything with a
programmatic trading API can sit behind it. What's wired today, and how to
connect each venue:

## Crypto — works now

### Any ccxt exchange (100+): Kraken, Coinbase, Binance.US, OKX, ...
```json
"exchange": { "id": "kraken" }
```
```bash
export EXCHANGE_API_KEY=...     # trade-only key, no withdrawal permission
export EXCHANGE_API_SECRET=...
export TRADING_BOT_LIVE=YES
python main.py trade --live
```
US recommendations (2026): **kraken** and **coinbase** (Advanced Trade)
primaries; `binanceus`, `okx` secondaries. Always scope keys to
trade-only and IP-allowlist where offered.

### Robinhood Crypto (native adapter — not in ccxt)
Robinhood's key-based API covers **crypto only** and uses Ed25519 request
signing. Quotes are spread-inclusive. US customers only. No sandbox.

1. `python main.py keygen` — prints an Ed25519 keypair
2. Register the **public** key: robinhood.com → Account → Crypto → API →
   select **trading** permission. Robinhood issues your API key.
3. ```bash
   export ROBINHOOD_API_KEY=rh-api-...
   export ROBINHOOD_PRIVATE_KEY=<base64 private key from keygen>
   export TRADING_BOT_LIVE=YES
   ```
4. ```json
   "exchange": { "id": "robinhood", "data_id": "kraken" }
   ```
   `data_id` matters: Robinhood has no candle endpoint, so strategy data
   streams from a ccxt venue while orders route to Robinhood.

## Robinhood Agentic Trading (stocks) — MCP, not REST

What "connect your AI agent to Robinhood" refers to: **Agentic Trading**
(beta, launched 2026-05-27) is an official Robinhood-hosted **MCP server**
— stocks only at launch, tied to a dedicated Agentic account with in-app
oversight. It is OAuth/session-based with no API keys, so it plugs into
MCP clients (Claude Code/Desktop, ChatGPT, Cursor), not into this bot's
headless loop:

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

Options/crypto/futures are announced as "coming soon" there; if Robinhood
ever exposes key-based stock trading, it becomes one more `Broker`
adapter here.

## US stocks — recommended programmatic paths

| Venue | Why | Notes |
|---|---|---|
| **Alpaca** | Simplest: header-key REST, free paper endpoint (`paper-api.alpaca.markets`), official MCP server (`alpacahq/alpaca-mcp-server`, 65 tools, paper-default) | Crypto side is even in ccxt (`alpaca`) |
| **Tradier** | Dead-simple Bearer-token REST + free sandbox | Good options support |
| **IBKR Web API** | Broadest assets/markets | Client Portal Gateway session friction for unattended bots |

An `AlpacaStockBroker` adapter is the natural next step — same Broker
interface, plus per-venue market-hours/session handling in the engine.

## Official AI-agent integrations by venue (2026)

- **Alpaca**: official MCP server — most mature
- **Robinhood**: hosted Agentic Trading MCP (stocks, beta)
- **Coinbase**: "Coinbase for Agents" MCP (2026-06-11, spot + derivatives)
  plus AgentKit/x402 for onchain agents
- **Kraken**: official Kraken CLI (151 commands) with built-in MCP server
  and a local paper-trading engine
- IBKR / Schwab / Tradier / Webull: community MCPs only — audit anything
  that will hold your trading keys

## Compliance notes worth knowing

- **The PDT rule is gone**: FINRA's amended Rule 4210 took effect
  2026-06-04, eliminating the pattern-day-trader $25k minimum. Brokers may
  phase in until late 2027 — check your broker's margin policy before
  assuming unlimited day trades. Cash accounts still follow T+1
  settlement rules. Crypto never had PDT.
- Never give a bot key withdrawal/transfer permissions.
- Paper endpoints: Alpaca (best), Tradier sandbox, IBKR paper, Kraken
  futures demo. Robinhood and Schwab have none — this bot's own
  PaperBroker fills that gap against live prices.
