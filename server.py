"""
server.py — Render + UptimeRobot keepalive server.

Render free tier sleeps after 15 minutes of no HTTP traffic.
UptimeRobot pings /ping every 5 minutes to keep it awake.

This file does two things simultaneously:
1. Runs a tiny Flask web server (so Render stays awake)
2. Runs the bot scan loop in a background thread (actual work)
"""

import threading
import asyncio
import time
import json
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify

app = Flask(__name__)

# ── ROUTES ───────────────────────────────────────────

@app.route("/")
def home():
    """Simple status page."""
    return """
    <html>
    <head><title>Moonshot Bot</title></head>
    <body style="background:#08000F;color:#D4B8F0;font-family:monospace;padding:40px">
        <h1 style="color:#C9A84C">🌙 MOONSHOT BOT</h1>
        <p>Bot is running continuously in the background.</p>
        <p>Status: <span style="color:#4DFFA0">● LIVE</span></p>
        <p><a href="/status" style="color:#A855F7">→ View bot status JSON</a></p>
        <p><a href="https://rishi-swami.github.io/moonshot" style="color:#A855F7">→ View live dashboard</a></p>
    </body>
    </html>
    """

@app.route("/ping")
def ping():
    """UptimeRobot pings this every 5 minutes to keep Render awake."""
    return jsonify({
        "status": "alive",
        "time": datetime.now().isoformat(),
        "message": "moonshot bot running"
    })

@app.route("/status")
def status():
    """Returns current dashboard data as JSON."""
    dashboard_path = Path("moonshot-bot/data/dashboard.json")
    if dashboard_path.exists():
        with open(dashboard_path) as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({"status": "starting", "message": "No data yet"})


# ── BOT BACKGROUND THREAD ─────────────────────────────

def run_bot_forever():
    """
    Runs in a background thread.
    Creates its own asyncio event loop and runs the bot continuously.
    Restarts automatically if it crashes.
    """
    import sys
    sys.path.insert(0, "moonshot-bot")

    restart_delay = 30

    while True:
        try:
            print(f"[server] Starting bot loop at {datetime.now().strftime('%H:%M:%S')}")
            asyncio.run(_bot_loop())
        except Exception as e:
            print(f"[server] Bot crashed: {e}. Restarting in {restart_delay}s...")
            time.sleep(restart_delay)
            restart_delay = min(restart_delay * 2, 300)  # max 5 min backoff


async def _bot_loop():
    """Continuous async bot loop — runs scan every 20 seconds."""
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
    from pathlib import Path

    Path("moonshot-bot/data").mkdir(parents=True, exist_ok=True)
    Path("moonshot-bot/logs").mkdir(parents=True, exist_ok=True)

    logger = get_logger("bot_loop")
    config = load_config()

    tracker = PositionTracker(config)
    tracker.load_state()

    buyer = Buyer(config, tracker)
    seller = Seller(config, tracker)
    dashboard = DashboardWriter(config, tracker)
    safety = SafetyGuard(config, tracker)

    strategies = [
        WhaleCopyStrategy(config, buyer, tracker),
        ListingSnipeStrategy(config, buyer, tracker),
        NarrativeStrategy(config, buyer, tracker),
    ]
    scanner = Scanner(config, strategies)

    scan_interval = 20  # seconds between scans

    logger.info("Bot loop started — scanning every 20 seconds")

    while True:
        cycle_start = datetime.now()

        try:
            # safety check first
            verdict = safety.check()
            if not verdict["safe"]:
                logger.critical(f"SAFETY KILL: {verdict['reason']}")
                dashboard.write(status="killed", error=verdict["reason"])
                await seller.emergency_close_all()
                break

            # run all strategies concurrently
            await asyncio.wait_for(
                asyncio.gather(
                    _scan_once(strategies[0], scanner, "whale"),
                    _scan_once(strategies[1], scanner, "listing"),
                    _scan_once(strategies[2], scanner, "narrative"),
                    return_exceptions=True
                ),
                timeout=15  # must finish within 15s to stay under 20s cycle
            )

            # update position prices and check profit ladder
            await seller.check_all_positions()

            # save and update dashboard
            tracker.save_state()
            dashboard.write(status="running", error=None)

        except asyncio.TimeoutError:
            logger.warning("Scan cycle timed out at 15s")
            tracker.add_error("Scan timed out")
        except Exception as e:
            logger.error(f"Scan cycle error: {e}")
            tracker.add_error(str(e))
            dashboard.write(status="error", error=str(e))

        # sleep until next scan
        elapsed = (datetime.now() - cycle_start).total_seconds()
        sleep_for = max(0, scan_interval - elapsed)
        await asyncio.sleep(sleep_for)


async def _scan_once(strategy, scanner, name):
    try:
        await strategy.scan(scanner)
    except Exception as e:
        print(f"[{name}] strategy error: {e}")


# ── STARTUP ───────────────────────────────────────────

def start():
    # start bot in background thread
    bot_thread = threading.Thread(target=run_bot_forever, daemon=True)
    bot_thread.start()
    print("[server] Bot background thread started")

    # start Flask (blocks here, keeps Render alive)
    port = int(__import__("os").environ.get("PORT", 10000))
    print(f"[server] Flask starting on port {port}")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    start()
