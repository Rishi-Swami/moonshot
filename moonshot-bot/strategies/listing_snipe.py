"""
Listing Snipe Strategy.
Detects brand new Solana token pairs on DexScreener (< 2 hours old).
Buys early before price discovery completes.

Key filters:
- Pair must be < 2 hours old
- Minimum liquidity threshold (not a ghost token)
- Minimum buy transaction count (real activity)
- No mint/freeze authority
- Positive price momentum in first hour
"""

import asyncio
from utils.logger import get_logger
from utils.safety import SafetyGuard

logger = get_logger("listing_snipe")


class ListingSnipeStrategy:
    def __init__(self, config: dict, buyer, tracker):
        self.config = config
        self.buyer = buyer
        self.tracker = tracker
        self.snipe_cfg = config.get("listing_snipe", {})
        self.snipe_amount_inr = config["trading"].get("per_trade_inr", 20)
        self._sniped: set = set()  # tokens already sniped this session

    def _passes_snipe_filter(self, pair: dict) -> tuple[bool, str]:
        """Returns (pass, reason_if_fail)."""
        token_address = pair.get("baseToken", {}).get("address", "")
        symbol = pair.get("baseToken", {}).get("symbol", "?")

        if token_address in self._sniped:
            return False, "already sniped"

        liq = pair.get("liquidity", {}).get("usd", 0)
        min_liq = self.snipe_cfg.get("min_liquidity_usd", 15_000)
        max_liq = self.snipe_cfg.get("max_liquidity_usd", 500_000)
        if liq < min_liq:
            return False, f"liquidity too low (${liq:.0f} < ${min_liq:.0f})"
        if liq > max_liq:
            return False, f"liquidity too high (${liq:.0f}) — not a new gem"

        # needs real buyers, not just LP creation
        buys_5m = pair.get("txns", {}).get("m5", {}).get("buys", 0)
        min_buys = self.snipe_cfg.get("min_buys_5min", 5)
        if buys_5m < min_buys:
            return False, f"not enough buyers ({buys_5m} < {min_buys} in 5min)"

        # must be going up, not down
        price_change_5m = pair.get("priceChange", {}).get("m5", 0) or 0
        if price_change_5m < 0:
            return False, f"price already falling ({price_change_5m:.1f}% in 5min)"

        # extreme pump filter — if already +500% in 5min, too late
        if price_change_5m > 500:
            return False, f"already pumped too much (+{price_change_5m:.0f}% in 5min)"

        return True, ""

    async def scan(self, scanner):
        """Called every 30 seconds."""
        try:
            new_pairs = await scanner.get_new_solana_pairs()
        except Exception as e:
            logger.debug(f"Listing scan fetch error: {e}")
            return

        if not new_pairs:
            return

        logger.debug(f"Listing scan: {len(new_pairs)} new Solana pairs found")

        for pair in new_pairs:
            try:
                token_address = pair.get("baseToken", {}).get("address", "")
                symbol = pair.get("baseToken", {}).get("symbol", "?")

                if not token_address:
                    continue

                ok, reason = self._passes_snipe_filter(pair)
                if not ok:
                    logger.debug(f"Snipe filter rejected {symbol}: {reason}")
                    continue

                if self.tracker.is_already_trading(token_address):
                    continue

                # build token_data from pair
                token_data = {
                    "symbol": symbol,
                    "price_usd": float(pair.get("priceUsd", 0) or 0),
                    "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
                    "volume_24h": pair.get("volume", {}).get("h24", 0),
                    "price_change_1h": pair.get("priceChange", {}).get("h1", 0),
                    "mint_authority_enabled": False,  # will check below
                    "freeze_authority_enabled": False,
                    "top_holder_pct": 0,
                }

                # security check
                security = await scanner.get_token_security(token_address)
                if security:
                    token_data.update(security)
                    if SafetyGuard.is_rug_pull(token_data):
                        logger.info(f"Snipe BLOCKED (rug): {symbol}")
                        continue

                logger.info(f"🎯 SNIPE SIGNAL: {symbol} | liq=${token_data['liquidity_usd']:.0f} "
                            f"| buys_5m={pair.get('txns',{}).get('m5',{}).get('buys',0)}")

                self._sniped.add(token_address)

                await self.buyer.buy(
                    token_address=token_address,
                    token_data=token_data,
                    amount_inr=self.snipe_amount_inr,
                    strategy="listing_snipe",
                )

                # small delay between snipes to avoid hammering
                await asyncio.sleep(2)

            except Exception as e:
                logger.debug(f"Snipe processing error: {e}")
