"""Ultimate Trading Bot — command-line interface.

Commands:
  trade      Run the bot (paper mode by default; live needs two-key opt-in)
  backtest   Backtest a strategy on real exchange history
  fetch      Download OHLCV history to CSV
  status     Show journal performance summary

Examples:
  python main.py trade
  python main.py backtest --symbols BTC/USD ETH/USD --days 365 --timeframe 4h
  python main.py backtest --strategy momentum --days 180
  python main.py fetch --symbols BTC/USD --days 90 --timeframe 1h
  python main.py status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from src.config.settings import load_config, validate_config
from src.trading_bot.backtest.backtester import (
    BacktestConfig,
    Backtester,
)
from src.trading_bot.core import TradingBot
from src.trading_bot.data.market_data_manager import MarketDataManager
from src.trading_bot.persistence.journal import TradeJournal
from src.trading_bot.strategies import build_strategy


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)
    handlers = [logging.StreamHandler()]
    log_file = log_cfg.get("file")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers,
    )


async def cmd_trade(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if getattr(args, "live", False):
        config["mode"] = "live"
        # Re-validate: flipping the mode after load_config would otherwise
        # skip the live-mode API-key checks.
        validate_config(config)
    setup_logging(config)
    bot = TradingBot(config)
    try:
        await bot.run()
    except KeyboardInterrupt:
        logging.info("Stopped by user")
    return 0


async def _fetch_history(config, symbols, timeframe, days, exchange_id=None):
    data = MarketDataManager(
        exchange_id=exchange_id or config["exchange"]["id"], timeframe=timeframe
    )
    await data.initialize()
    since_ms = int((time.time() - days * 86400) * 1000)
    frames = {}
    try:
        for symbol in symbols:
            print(f"Fetching {symbol} {timeframe} history ({days}d)...")
            df = await data.fetch_history(symbol, timeframe, since_ms)
            print(f"  {len(df)} candles: {df.index[0]} -> {df.index[-1]}")
            frames[symbol] = df
    finally:
        await data.close()
    return frames


async def cmd_backtest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    setup_logging(config)

    strategy_name = args.strategy or config["signals"]["strategy"]
    strategy = build_strategy(
        strategy_name, config["signals"].get("strategy_params")
    )
    frames = await _fetch_history(
        config, args.symbols, args.timeframe, args.days, args.exchange
    )

    bt_config = BacktestConfig(
        initial_cash=args.cash,
        fee_rate=float(config["exchange"].get("taker_fee", 0.0026)),
        max_open_positions=int(config["trading"]["max_open_positions"]),
        risk=config.get("risk", {}),
    )
    result = Backtester(strategy, bt_config).run(frames)
    print()
    print(f"Strategy: {strategy_name} | symbols: {', '.join(args.symbols)}")
    print(result.summary())
    if args.trades:
        print("\nLast trades:")
        for t in result.trades[-args.trades:]:
            print(
                f"  {t.closed_at:%Y-%m-%d %H:%M} {t.symbol:<10} "
                f"pnl {t.pnl:+10.2f}  ({t.exit_reason})"
            )
    return 0


async def cmd_fetch(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    setup_logging(config)
    frames = await _fetch_history(
        config, args.symbols, args.timeframe, args.days, args.exchange
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for symbol, df in frames.items():
        path = out_dir / f"{symbol.replace('/', '_')}_{args.timeframe}.csv"
        df.to_csv(path)
        print(f"Wrote {path} ({len(df)} rows)")
    return 0


async def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the dashboard standalone (journal-only view)."""
    config = load_config(args.config)
    setup_logging(config)
    from src.trading_bot.web.dashboard import Dashboard

    journal = TradeJournal(config["journal"]["db_path"])
    dash_cfg = config.get("dashboard", {})
    dashboard = Dashboard(
        journal=journal,
        host=dash_cfg.get("host", "127.0.0.1"),
        port=int(args.port or dash_cfg.get("port", 8899)),
    )
    url = await dashboard.start()
    print(f"Dashboard: {url}  (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await dashboard.stop()
        journal.close()
    return 0


def cmd_keygen(args: argparse.Namespace) -> int:
    """Generate an Ed25519 keypair for the Robinhood Crypto API."""
    from src.trading_bot.exchanges.robinhood_crypto import generate_keypair

    pair = generate_keypair()
    print("Robinhood Crypto API keypair (Ed25519):")
    print()
    print("  PUBLIC key  (register at robinhood.com -> Crypto -> API):")
    print(f"    {pair['public_key_b64']}")
    print()
    print("  PRIVATE key (keep secret — set as env var, never commit):")
    print(f"    ROBINHOOD_PRIVATE_KEY={pair['private_key_b64']}")
    print()
    print("After registering, Robinhood issues an API key -> set ROBINHOOD_API_KEY.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db_path = config["journal"]["db_path"]
    if not Path(db_path).exists():
        print(f"No journal at {db_path} — the bot hasn't traded yet.")
        return 1
    journal = TradeJournal(db_path)
    summary = journal.performance_summary()
    print("== Journal summary ==")
    print(f"  trades:    {summary['trades']}")
    print(f"  total pnl: {summary['total_pnl']:+.2f}")
    print(f"  fees:      {summary['total_fees']:.2f}")
    print(f"  win rate:  {summary['win_rate']:.1%}")
    print("\nRecent trades:")
    for t in journal.recent_trades(10):
        print(
            f"  {t['closed_at'][:16]} {t['symbol']:<10} "
            f"pnl {t['pnl']:+10.2f}  ({t['exit_reason']})"
        )
    journal.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-bot", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="config.json", help="config file path")
    sub = parser.add_subparsers(dest="command")

    p_trade = sub.add_parser("trade", help="run the trading bot")
    p_trade.add_argument(
        "--live",
        action="store_true",
        help="enable live trading (also requires TRADING_BOT_LIVE=YES)",
    )

    p_bt = sub.add_parser("backtest", help="backtest on real exchange history")
    p_bt.add_argument("--symbols", nargs="+", default=["BTC/USD"])
    p_bt.add_argument("--timeframe", default="1h")
    p_bt.add_argument("--days", type=int, default=365)
    p_bt.add_argument("--cash", type=float, default=10_000.0)
    p_bt.add_argument("--strategy", default=None, help="strategy name override")
    p_bt.add_argument("--trades", type=int, default=0, help="print last N trades")
    p_bt.add_argument(
        "--exchange",
        default=None,
        help="history source (kraken serves only ~720 recent candles; "
        "use coinbase or binanceus for deep history)",
    )

    p_fetch = sub.add_parser("fetch", help="download OHLCV history to CSV")
    p_fetch.add_argument("--symbols", nargs="+", default=["BTC/USD"])
    p_fetch.add_argument("--timeframe", default="1h")
    p_fetch.add_argument("--days", type=int, default=90)
    p_fetch.add_argument("--out", default="data/history")
    p_fetch.add_argument("--exchange", default=None, help="history source override")

    sub.add_parser("status", help="show journal performance summary")

    p_dash = sub.add_parser("dashboard", help="serve the web dashboard (journal view)")
    p_dash.add_argument("--port", type=int, default=None)

    sub.add_parser("keygen", help="generate Ed25519 keypair for Robinhood Crypto API")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "trade"
    if command == "trade":
        return asyncio.run(cmd_trade(args))
    if command == "backtest":
        return asyncio.run(cmd_backtest(args))
    if command == "fetch":
        return asyncio.run(cmd_fetch(args))
    if command == "dashboard":
        return asyncio.run(cmd_dashboard(args))
    if command == "keygen":
        return cmd_keygen(args)
    if command == "status":
        return cmd_status(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
