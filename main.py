"""Main application entrypoint for Hyperliquid Multi-Timeframe SMC Trading Bot.
Starts the background scanning loop and serves the real-time visual dashboard.
"""

import sys
import os
import asyncio
import argparse
import logging
from config import config
from src.bot import SMCBot
from src.dashboard_server import DashboardServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("main")


def parse_args():
    parser = argparse.ArgumentParser(description="Hyperliquid MTF SMC Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run paper trading mode")
    parser.add_argument("--live", action="store_true", help="Run in live exchange execution mode")
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols (e.g. BTC,ETH,SOL)")
    parser.add_argument("--port", type=int, default=config.dashboard_port, help="Dashboard HTTP port")
    parser.add_argument("--scan-interval", type=int, default=20, help="Scan interval in seconds")
    return parser.parse_args()


async def async_main():
    args = parse_args()

    # Apply CLI overrides
    if args.dry_run:
        config.dry_run = True
    elif args.live:
        config.dry_run = False

    if args.symbols:
        config.symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.port:
        config.dashboard_port = args.port

    print("=" * 70)
    print("   ⚡ HYPERLIQUID MULTI-TIMEFRAME SMC TRADING BOT ⚡")
    print("=" * 70)
    print(f" Environment       : {config.env.upper()}")
    print(f" Mode              : {'DRY RUN / PAPER' if config.dry_run else 'LIVE EXCHANGE ORDERS'}")
    print(f" Auto-Trade        : {'ENABLED' if config.auto_trade_enabled else 'PAUSED'}")
    print(f" Monitored Pairs   : {', '.join(config.symbols)}")
    print(f" Timeframes        : {', '.join(config.timeframes)}")
    print(f" Risk USD / Trade  : ${config.risk_usd_per_trade:.2f}")
    print(f" Target R:R        : 1:{config.target_rr}")
    print(f" Dashboard URL     : http://localhost:{config.dashboard_port}")
    print("=" * 70)

    # Initialize Bot & Dashboard Server
    bot = SMCBot(config)
    dashboard = DashboardServer(bot, config)

    # Start Dashboard Server
    await dashboard.start()

    # Start periodic scanning loop (executes initial scan immediately on entry)
    try:
        await bot.run_loop(interval_seconds=args.scan_interval)
    except asyncio.CancelledError:
        logger.info("Main loop cancelled. Shutting down...")
    finally:
        bot.stop()


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")


if __name__ == "__main__":
    main()
