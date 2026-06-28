import asyncio
import sys
from pathlib import Path
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

logger = get_logger("main_once")

Path("data").mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)

async def run_once():
    start = datetime.now()
    logger.info(f"=== Scan cycle starting at {start.strftime('%H:%M:%S')} ===")
    try:
        config = load_config()
    except EnvironmentError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)

    tracker = PositionTracker(config)
    tracker.load_state()
    buyer = Buyer(config, tracker)
    seller = Seller(config, tracker)
    dashboard = DashboardWriter(config, tracker)
    safety = SafetyGuard(config, tracker)

    verdict = safety.check()
    if not verdict["safe"]:
        logger.warning(f"Safety check failed: {verdict['reason']}")
        dashboard.write(status="killed", error=verdict["reason"])
        tracker.save_state()
        return

    strategies = [
        WhaleCopyStrategy(config, buyer, tracker),
        ListingSnipeStrategy(config, buyer, tracker),
        NarrativeStrategy(config, buyer, tracker),
    ]
    scanner = Scanner(config, strategies)

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _scan_once(strategies[0], scanner, "whale"),
                _scan_once(strategies[1], scanner, "listing"),
                _scan_once(strategies[2], scanner, "narrative"),
                return_exceptions=True
            ),
            timeout=180
        )
        await seller.check_all_positions()
    except asyncio.TimeoutError:
        logger.warning("Scan cycle timed out")
        tracker.add_error("Scan cycle timed out")
    except Exception as e:
        logger.error(f"Scan cycle error: {e}", exc_info=True)
        tracker.add_error(str(e))
        dashboard.write(status="error", error=str(e))
    finally:
        tracker.save_state()
        dashboard.write(status="running", error=None)
        await scanner.close()
        await buyer.close()
        await seller.close()

    elapsed = (datetime.now() - start).seconds
    logger.info(f"=== Scan cycle complete in {elapsed}s ===")

async def _scan_once(strategy, scanner, name: str):
    try:
        await strategy.scan(scanner)
    except Exception as e:
        logger.error(f"{name} strategy error: {e}")

if __name__ == "__main__":
    asyncio.run(run_once())
