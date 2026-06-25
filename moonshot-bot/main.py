"""
MOONSHOT BOT — Master Orchestrator
Oracle server entry point. Runs all strategies, manages positions,
writes dashboard JSON, handles restarts and kill switches.
"""

import asyncio
import signal
import sys
import time
from datetime import datetime

from bot.scanner import Scanner
from bot.buyer import Buyer
from bot.seller import Seller
from bot.tracker import PositionTracker
from bot.dashboard import DashboardWriter
from strategies.whale_copy import WhaleCopyStrategy
from strategies.listing_snipe import ListingSnipeStrategy
from strategies.narrative import NarrativeStrategy
from utils.logger import get_logger
from utils.safety import SafetyGuard
from utils.config import load_config

logger = get_logger("master")


class MoonshotBot:
    def __init__(self):
        self.config = load_config()
        self.running = False
        self.restart_count = 0
        self.max_restarts = 10

        # core modules
        self.tracker = PositionTracker(self.config)
        self.buyer = Buyer(self.config, self.tracker)
        self.seller = Seller(self.config, self.tracker)
        self.dashboard = DashboardWriter(self.config, self.tracker)
        self.safety = SafetyGuard(self.config, self.tracker)

        # strategies (each runs independently, feeds signals to buyer)
        self.strategies = [
            WhaleCopyStrategy(self.config, self.buyer, self.tracker),
            ListingSnipeStrategy(self.config, self.buyer, self.tracker),
            NarrativeStrategy(self.config, self.buyer, self.tracker),
        ]

        # scanner wraps all data feeds
        self.scanner = Scanner(self.config, self.strategies)

        # graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, *_):
        logger.info("Shutdown signal received. Closing positions and saving state...")
        self.running = False
        self.tracker.save_state()
        self.dashboard.write(status="stopped", error=None)
        sys.exit(0)

    async def _heartbeat(self):
        """Write dashboard every 15s regardless of trade activity."""
        while self.running:
            try:
                self.dashboard.write(status="running", error=None)
            except Exception as e:
                logger.error(f"Dashboard write failed: {e}")
            await asyncio.sleep(15)

    async def _seller_loop(self):
        """Check all open positions for take-profit / stop-loss every 10s."""
        while self.running:
            try:
                await self.seller.check_all_positions()
            except Exception as e:
                logger.error(f"Seller loop error: {e}")
                self.dashboard.write(status="running", error=f"Seller error: {e}")
            await asyncio.sleep(10)

    async def _safety_loop(self):
        """Global kill switch — checks daily loss limit every 30s."""
        while self.running:
            try:
                verdict = self.safety.check()
                if not verdict["safe"]:
                    logger.critical(f"SAFETY KILL: {verdict['reason']}")
                    self.dashboard.write(status="killed", error=verdict["reason"])
                    # close all open positions
                    await self.seller.emergency_close_all()
                    self.running = False
                    break
            except Exception as e:
                logger.error(f"Safety loop error: {e}")
            await asyncio.sleep(30)

    async def run(self):
        self.running = True
        logger.info("=" * 60)
        logger.info("  MOONSHOT BOT STARTING")
        logger.info(f"  Capital: {self.config['capital']['total_inr']} INR")
        logger.info(f"  Max positions: {self.config['trading']['max_open_positions']}")
        logger.info(f"  Strategies: {[s.__class__.__name__ for s in self.strategies]}")
        logger.info("=" * 60)

        self.tracker.load_state()
        self.dashboard.write(status="starting", error=None)

        try:
            await asyncio.gather(
                self.scanner.run(),        # feeds signals to strategies
                self._seller_loop(),       # monitors profits/stops
                self._heartbeat(),         # keeps dashboard fresh
                self._safety_loop(),       # global kill switch
            )
        except Exception as e:
            logger.critical(f"Master loop crashed: {e}", exc_info=True)
            self.dashboard.write(status="error", error=str(e))
            raise


async def main():
    max_restarts = 10
    restart_delay = 30  # seconds

    for attempt in range(max_restarts):
        try:
            bot = MoonshotBot()
            await bot.run()
        except KeyboardInterrupt:
            logger.info("Manual stop.")
            break
        except Exception as e:
            logger.error(f"Bot crashed (attempt {attempt+1}/{max_restarts}): {e}")
            if attempt < max_restarts - 1:
                logger.info(f"Restarting in {restart_delay}s...")
                time.sleep(restart_delay)
                restart_delay = min(restart_delay * 2, 300)  # exponential backoff, max 5min
            else:
                logger.critical("Max restarts reached. Manual intervention needed.")
                break


if __name__ == "__main__":
    asyncio.run(main())
