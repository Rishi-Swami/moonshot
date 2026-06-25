"""
Dashboard Writer.
Writes dashboard.json every 15 seconds.
GitHub Pages fetches this file to update the live dashboard.
Optionally serves via a tiny HTTP server for CORS.
"""

import json
import asyncio
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from bot.tracker import PositionTracker
from utils.logger import get_logger

logger = get_logger("dashboard")


class DashboardWriter:
    def __init__(self, config: dict, tracker: PositionTracker):
        self.config = config
        self.tracker = tracker
        self.output_path = Path(config["keys"]["dashboard_output"])
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._start_capital = config["capital"]["total_inr"]
        self._error_history: list = []

    def write(self, status: str = "running", error: Optional[str] = None):
        """Build and write the dashboard JSON."""
        try:
            if error:
                self._error_history.append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "message": error
                })
                self._error_history = self._error_history[-5:]  # keep last 5

            total_value   = self.tracker.get_total_value_inr()
            total_pnl     = self.tracker.get_total_pnl_inr()
            total_pct     = (total_pnl / self._start_capital * 100) if self._start_capital > 0 else 0
            today_pnl     = self.tracker.get_today_pnl()
            today_pct     = (today_pnl / self._start_capital * 100) if self._start_capital > 0 else 0
            open_positions = list(self.tracker.open_positions.values())
            closed_trades  = self.tracker.closed_positions[-50:]  # last 50

            # build trade feed (open + recent closed, sorted newest first)
            trades = []

            for pos in open_positions:
                trades.append({
                    "id":        pos.id,
                    "time":      datetime.fromisoformat(pos.opened_at).strftime("%H:%M"),
                    "coin":      pos.symbol,
                    "entry":     f"${pos.entry_price_usd:.6f}",
                    "pnl":       round(pos.pnl_inr, 2),
                    "pnl_pct":   round(pos.pnl_pct, 1),
                    "strategy":  pos.strategy,
                    "status":    "OPEN",
                })

            for t in reversed(closed_trades):
                trades.append({
                    "id":        t.get("id", ""),
                    "time":      datetime.fromisoformat(
                                     t.get("closed_at", datetime.now().isoformat())
                                 ).strftime("%H:%M"),
                    "coin":      t.get("symbol", "?"),
                    "entry":     f"${t.get('entry_price_usd', 0):.6f}",
                    "pnl":       round(t.get("realized_pnl", 0), 2),
                    "pnl_pct":   round(
                                     (t.get("realized_pnl", 0) / t.get("amount_inr", 1) * 100)
                                     if t.get("amount_inr") else 0, 1
                                 ),
                    "strategy":  t.get("strategy", "?"),
                    "status":    "CLOSED",
                })

            # active coin names for dashboard subtitle
            active_coins = ", ".join(p.symbol for p in open_positions) or "scanning..."

            # best trade
            best = self.tracker._best_trade
            best_trade_inr = best.get("pnl_inr", 0)
            best_trade_coin = best.get("symbol", "—")

            # last whale signal
            last_signal = self.tracker.errors[-1]["time"] if self.tracker.errors else "—"

            payload = {
                # top hero numbers
                "totalProfit":     round(total_value, 2),
                "startCapital":    round(self._start_capital, 2),
                "totalPct":        round(total_pct, 2),
                "todayProfit":     round(today_pnl, 2),
                "todayPct":        round(today_pct, 2),

                # stat cards
                "totalTrades":     len(self.tracker.closed_positions),
                "winRate":         self.tracker.get_win_rate(),
                "bestTrade":       round(best_trade_inr, 2),
                "bestCoin":        best_trade_coin,
                "openPositions":   len(open_positions),
                "activeCoins":     active_coins,
                "whalesTracked":   self.tracker.whales_tracked,
                "lastSignal":      last_signal,

                # trade feed
                "trades":          trades[:60],  # cap at 60 rows

                # bot health
                "botStatus":       status,       # running | stopped | error | killed
                "error":           self._error_history[-1]["message"] if self._error_history else None,
                "errorHistory":    self._error_history,
                "lastUpdated":     datetime.now().isoformat(),
                "paperTrading":    self.config.get("paper_trading", True),
            }

            # atomic write (write to temp, then rename — prevents partial reads)
            tmp_path = self.output_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(payload, f, indent=2)
            tmp_path.replace(self.output_path)

            logger.debug(f"Dashboard written: value=₹{total_value:.2f} "
                         f"status={status} trades={len(trades)}")

        except Exception as e:
            logger.error(f"Dashboard write failed: {e}")
