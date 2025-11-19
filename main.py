"""
Ultimate AI-Powered Crypto Trading Bot
Main entry point for the trading system
"""

import asyncio
import logging
from src.trading_bot.core import TradingBot
from src.config.settings import load_config


def main() -> None:
    """Main entry point for the trading bot"""
    # Load configuration
    config = load_config()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Initialize and run the trading bot
    bot = TradingBot(config)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logging.info("Trading bot stopped by user")
    except Exception as e:
        logging.error(f"Trading bot crashed: {e}")


if __name__ == "__main__":
    main()
