"""
Safety Guard — Global kill switch.

Monitors:
- Daily loss limit (default 20% of capital)
- Max open positions
- Rug pull detection (liquidity evaporation)
- Unusual bot behavior (too many trades in short window)
"""

from datetime import datetime, date
from utils.logger import get_logger

logger = get_logger("safety")


class SafetyGuard:
    def __init__(self, config: dict, tracker):
        self.config = config
        self.tracker = tracker
        self.safety_cfg = config.get("safety", {})
        self._today = date.today()
        self._daily_loss_inr = 0.0
        self._trade_timestamps = []

    def check(self) -> dict:
        """Returns {"safe": bool, "reason": str|None}"""
        checks = [
            self._check_daily_loss(),
            self._check_trade_frequency(),
            self._check_capital_floor(),
        ]
        for result in checks:
            if not result["safe"]:
                logger.warning(f"Safety check failed: {result['reason']}")
                return result
        return {"safe": True, "reason": None}

    def _check_daily_loss(self) -> dict:
        # reset counter on new day
        if date.today() != self._today:
            self._today = date.today()
            self._daily_loss_inr = 0.0

        max_loss_pct = self.safety_cfg.get("max_daily_loss_pct", 20)
        total_capital = self.config["capital"]["total_inr"]
        max_loss_inr = total_capital * max_loss_pct / 100

        realized_loss = abs(self.tracker.get_daily_loss())
        if realized_loss >= max_loss_inr:
            return {
                "safe": False,
                "reason": f"Daily loss limit hit: ₹{realized_loss:.2f} >= ₹{max_loss_inr:.2f} ({max_loss_pct}%)"
            }
        return {"safe": True, "reason": None}

    def _check_trade_frequency(self) -> dict:
        """Detect runaway bot — more than N trades per minute."""
        now = datetime.now().timestamp()
        max_per_min = self.safety_cfg.get("max_trades_per_minute", 5)

        self._trade_timestamps = [t for t in self._trade_timestamps if now - t < 60]
        count = len(self._trade_timestamps)

        if count >= max_per_min:
            return {
                "safe": False,
                "reason": f"Trade frequency too high: {count} trades in 60s (max {max_per_min}). Possible runaway bot."
            }
        return {"safe": True, "reason": None}

    def _check_capital_floor(self) -> dict:
        """Stop if total remaining capital drops below floor."""
        floor_inr = self.safety_cfg.get("capital_floor_inr", 20)
        remaining = self.tracker.get_total_value_inr()
        if remaining < floor_inr:
            return {
                "safe": False,
                "reason": f"Capital floor reached: ₹{remaining:.2f} < ₹{floor_inr:.2f}. Bot stopping to preserve funds."
            }
        return {"safe": True, "reason": None}

    def record_trade(self):
        self._trade_timestamps.append(datetime.now().timestamp())

    @staticmethod
    def is_rug_pull(token_data: dict) -> bool:
        """
        Heuristic rug-pull detector.
        Returns True if token looks dangerous.
        """
        reasons = []

        liq = token_data.get("liquidity_usd", 0)
        if liq < 5000:
            reasons.append(f"liquidity too low (${liq:.0f})")

        liq_change_1h = token_data.get("liquidity_change_1h_pct", 0)
        if liq_change_1h < -40:
            reasons.append(f"liquidity dropped {liq_change_1h:.0f}% in 1h (rug signal)")

        top_holder_pct = token_data.get("top_holder_pct", 0)
        if top_holder_pct > 50:
            reasons.append(f"top holder owns {top_holder_pct:.0f}% (centralized)")

        mint_authority = token_data.get("mint_authority_enabled", False)
        if mint_authority:
            reasons.append("mint authority not revoked (infinite print risk)")

        freeze_authority = token_data.get("freeze_authority_enabled", False)
        if freeze_authority:
            reasons.append("freeze authority active (funds can be frozen)")

        if reasons:
            logger.warning(f"RUG PULL DETECTED for {token_data.get('symbol','?')}: {'; '.join(reasons)}")
            return True
        return False

    @staticmethod
    def check_slippage(expected_price: float, actual_price: float, max_pct: float = 3.0) -> bool:
        """Returns True if slippage is acceptable."""
        if expected_price <= 0:
            return False
        slippage = abs(actual_price - expected_price) / expected_price * 100
        if slippage > max_pct:
            logger.warning(f"Slippage too high: {slippage:.2f}% > {max_pct}%")
            return False
        return True
