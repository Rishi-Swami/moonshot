"""
Position Tracker.
Single source of truth for all positions, P&L, and portfolio state.
Persists to disk so state survives restarts.
"""

import json
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from utils.logger import get_logger

logger = get_logger("tracker")

STATE_FILE = Path(__file__).parent.parent / "data" / "state.json"


class Position:
    def __init__(self, token_address: str, symbol: str, entry_price_usd: float,
                 amount_inr: float, amount_tokens: float, strategy: str,
                 position_id: str = None):
        self.id = position_id or str(uuid.uuid4())[:8]
        self.token_address = token_address
        self.symbol = symbol
        self.entry_price_usd = entry_price_usd
        self.amount_inr = amount_inr            # INR invested
        self.amount_tokens = amount_tokens       # tokens held
        self.strategy = strategy
        self.opened_at = datetime.now().isoformat()
        self.status = "OPEN"                     # OPEN | PARTIAL | CLOSED | ERROR

        # profit ladder tracking
        self.sold_50_pct = False   # triggered at 2x
        self.sold_30_pct = False   # triggered at 5x
        # remaining 20% rides to moon

        # stop loss
        self.stop_loss_price = entry_price_usd * 0.75   # -25%
        self.current_price_usd = entry_price_usd
        self.pnl_inr = 0.0
        self.pnl_pct = 0.0

    def update_price(self, current_price: float):
        self.current_price_usd = current_price
        multiplier = current_price / self.entry_price_usd if self.entry_price_usd > 0 else 1
        self.pnl_pct = (multiplier - 1) * 100
        self.pnl_inr = self.amount_inr * (multiplier - 1)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "token_address": self.token_address,
            "symbol": self.symbol,
            "entry_price_usd": self.entry_price_usd,
            "current_price_usd": self.current_price_usd,
            "amount_inr": self.amount_inr,
            "amount_tokens": self.amount_tokens,
            "strategy": self.strategy,
            "opened_at": self.opened_at,
            "status": self.status,
            "sold_50_pct": self.sold_50_pct,
            "sold_30_pct": self.sold_30_pct,
            "stop_loss_price": self.stop_loss_price,
            "pnl_inr": round(self.pnl_inr, 2),
            "pnl_pct": round(self.pnl_pct, 2),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        p = cls(
            token_address=d["token_address"],
            symbol=d["symbol"],
            entry_price_usd=d["entry_price_usd"],
            amount_inr=d["amount_inr"],
            amount_tokens=d["amount_tokens"],
            strategy=d["strategy"],
            position_id=d["id"],
        )
        p.opened_at = d.get("opened_at", p.opened_at)
        p.status = d.get("status", "OPEN")
        p.sold_50_pct = d.get("sold_50_pct", False)
        p.sold_30_pct = d.get("sold_30_pct", False)
        p.stop_loss_price = d.get("stop_loss_price", p.entry_price_usd * 0.75)
        p.current_price_usd = d.get("current_price_usd", p.entry_price_usd)
        p.pnl_inr = d.get("pnl_inr", 0.0)
        p.pnl_pct = d.get("pnl_pct", 0.0)
        return p


class PositionTracker:
    def __init__(self, config: dict):
        self.config = config
        self.open_positions: dict[str, Position] = {}   # id -> Position
        self.closed_positions: list[dict] = []
        self.errors: list[dict] = []
        self._start_capital_inr = config["capital"]["total_inr"]
        self._realized_pnl_inr = 0.0
        self._daily_loss_inr = 0.0
        self._today = date.today()
        self._best_trade = {"symbol": "—", "pnl_inr": 0.0}
        self.whales_tracked = len(config.get("whales", {}).get("watch_list", []))

    # ── POSITION MANAGEMENT ──────────────────────────────

    def add_position(self, position: Position):
        self.open_positions[position.id] = position
        logger.info(f"[{position.strategy}] OPEN {position.symbol} "
                    f"@ ${position.entry_price_usd:.8f} | ₹{position.amount_inr:.2f}")

    def update_position_price(self, position_id: str, current_price: float):
        if position_id in self.open_positions:
            self.open_positions[position_id].update_price(current_price)

    def close_position(self, position_id: str, close_price: float,
                       fraction: float = 1.0, reason: str = ""):
        """
        Close a fraction of a position.
        fraction=1.0 = full close, 0.5 = sell half, etc.
        """
        if position_id not in self.open_positions:
            logger.warning(f"close_position: ID {position_id} not found")
            return None

        pos = self.open_positions[position_id]
        pos.update_price(close_price)

        realized_pnl = pos.pnl_inr * fraction
        self._realized_pnl_inr += realized_pnl

        if realized_pnl < 0:
            self._update_daily_loss(abs(realized_pnl))

        if realized_pnl > self._best_trade["pnl_inr"]:
            self._best_trade = {"symbol": pos.symbol, "pnl_inr": realized_pnl}

        closed_record = {**pos.to_dict(), "close_price": close_price,
                         "fraction_sold": fraction, "realized_pnl": round(realized_pnl, 2),
                         "reason": reason, "closed_at": datetime.now().isoformat()}

        if fraction >= 1.0:
            pos.status = "CLOSED"
            del self.open_positions[position_id]
        else:
            pos.amount_inr *= (1 - fraction)
            pos.amount_tokens *= (1 - fraction)

        self.closed_positions.append(closed_record)
        logger.info(f"CLOSE {pos.symbol} fraction={fraction} pnl=₹{realized_pnl:.2f} [{reason}]")
        return closed_record

    def add_error(self, error_msg: str):
        self.errors.append({
            "time": datetime.now().isoformat(),
            "message": error_msg
        })
        # keep only last 20 errors
        self.errors = self.errors[-20:]

    def can_open_position(self) -> bool:
        max_pos = self.config["trading"]["max_open_positions"]
        return len(self.open_positions) < max_pos

    def is_already_trading(self, token_address: str) -> bool:
        return any(p.token_address == token_address
                   for p in self.open_positions.values())

    # ── P&L / STATS ─────────────────────────────────────

    def get_total_value_inr(self) -> float:
        unrealized = sum(p.pnl_inr for p in self.open_positions.values())
        return self._start_capital_inr + self._realized_pnl_inr + unrealized

    def get_total_pnl_inr(self) -> float:
        unrealized = sum(p.pnl_inr for p in self.open_positions.values())
        return self._realized_pnl_inr + unrealized

    def get_daily_loss(self) -> float:
        if date.today() != self._today:
            self._daily_loss_inr = 0.0
            self._today = date.today()
        return self._daily_loss_inr

    def get_win_rate(self) -> float:
        if not self.closed_positions:
            return 0.0
        wins = sum(1 for t in self.closed_positions if t.get("realized_pnl", 0) > 0)
        return round(wins / len(self.closed_positions) * 100, 1)

    def get_today_pnl(self) -> float:
        today = date.today().isoformat()
        return sum(
            t.get("realized_pnl", 0)
            for t in self.closed_positions
            if t.get("closed_at", "").startswith(today)
        )

    def _update_daily_loss(self, loss: float):
        if date.today() != self._today:
            self._daily_loss_inr = 0.0
            self._today = date.today()
        self._daily_loss_inr += loss

    # ── PERSISTENCE ─────────────────────────────────────

    def save_state(self):
        STATE_FILE.parent.mkdir(exist_ok=True)
        state = {
            "open_positions": {k: v.to_dict() for k, v in self.open_positions.items()},
            "closed_positions": self.closed_positions[-200:],   # keep last 200
            "realized_pnl_inr": self._realized_pnl_inr,
            "start_capital_inr": self._start_capital_inr,
            "best_trade": self._best_trade,
            "errors": self.errors,
            "saved_at": datetime.now().isoformat(),
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        logger.debug("State saved.")

    def load_state(self):
        if not STATE_FILE.exists():
            logger.info("No previous state found. Starting fresh.")
            return
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            for pid, pdata in state.get("open_positions", {}).items():
                self.open_positions[pid] = Position.from_dict(pdata)
            self.closed_positions = state.get("closed_positions", [])
            self._realized_pnl_inr = state.get("realized_pnl_inr", 0.0)
            self._start_capital_inr = state.get("start_capital_inr", self._start_capital_inr)
            self._best_trade = state.get("best_trade", self._best_trade)
            self.errors = state.get("errors", [])
            logger.info(f"State loaded: {len(self.open_positions)} open positions, "
                        f"₹{self._realized_pnl_inr:.2f} realized PnL")
        except Exception as e:
            logger.error(f"Failed to load state: {e}. Starting fresh.")
