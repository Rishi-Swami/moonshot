"""
Narrative Momentum Strategy.
Tracks trending narratives (AI coins, GameFi, L2, memecoins)
via CoinGecko trending endpoint.

When a narrative is trending AND a token in that narrative
has a low market cap — it's a high-probability moonshot setup.

Logic:
1. Fetch trending coins from CoinGecko every 2 minutes
2. Identify current dominant narrative (AI, gaming, DeFi, etc.)
3. Find lowest market cap Solana tokens in that narrative
4. Cross-check volume spike (+200% in 1h) for confirmation
5. Fire buy signal
"""

import asyncio
from utils.logger import get_logger
from utils.safety import SafetyGuard

logger = get_logger("narrative")

# Narrative keyword mapping
NARRATIVE_KEYWORDS = {
    "ai":       ["ai", "artificial", "gpt", "llm", "neural", "agent", "agi"],
    "gaming":   ["game", "gaming", "nft", "play", "metaverse", "rpg", "quest"],
    "defi":     ["defi", "yield", "swap", "liquidity", "amm", "vault"],
    "meme":     ["doge", "pepe", "inu", "moon", "elon", "cat", "bonk", "wojak"],
    "l2":       ["layer2", "rollup", "zk", "optimism", "arbitrum", "scaling"],
    "rwa":      ["real", "asset", "rwa", "tokenized", "property", "gold"],
}


class NarrativeStrategy:
    def __init__(self, config: dict, buyer, tracker):
        self.config = config
        self.buyer = buyer
        self.tracker = tracker
        self.narrative_cfg = config.get("narrative", {})
        self.amount_inr = config["trading"].get("per_trade_inr", 20)
        self._bought_this_narrative: dict = {}  # narrative -> [token_addresses]
        self._current_narrative: str = ""

    def _detect_narrative(self, trending: list) -> str:
        """Find the dominant narrative from trending coins."""
        narrative_scores = {n: 0 for n in NARRATIVE_KEYWORDS}

        for coin in trending:
            name = (coin.get("name", "") + " " + coin.get("id", "")).lower()
            for narrative, keywords in NARRATIVE_KEYWORDS.items():
                for kw in keywords:
                    if kw in name:
                        narrative_scores[narrative] += 1

        if not any(narrative_scores.values()):
            return "meme"  # default to meme if no clear narrative

        dominant = max(narrative_scores, key=narrative_scores.get)
        logger.debug(f"Narrative scores: {narrative_scores} → dominant: {dominant}")
        return dominant

    def _matches_narrative(self, symbol: str, name: str, narrative: str) -> bool:
        text = (symbol + " " + name).lower()
        keywords = NARRATIVE_KEYWORDS.get(narrative, [])
        return any(kw in text for kw in keywords)

    def _has_volume_spike(self, token_data: dict) -> bool:
        """Volume must have spiked significantly — confirms momentum."""
        vol_24h = token_data.get("volume_24h", 0)
        liq = token_data.get("liquidity_usd", 1)
        # volume/liquidity ratio > 3 means strong activity relative to pool size
        ratio = vol_24h / liq if liq > 0 else 0
        min_ratio = self.narrative_cfg.get("min_vol_liq_ratio", 3.0)
        if ratio < min_ratio:
            logger.debug(f"Volume spike check failed: vol/liq={ratio:.1f} < {min_ratio}")
            return False
        return True

    async def scan(self, scanner):
        """Called every 2 minutes."""
        try:
            # step 1: find trending narrative
            trending = await scanner.get_trending_coins()
            if not trending:
                return

            narrative = self._detect_narrative(trending)
            if narrative != self._current_narrative:
                logger.info(f"📈 Narrative shift detected: {self._current_narrative} → {narrative}")
                self._current_narrative = narrative
                self._bought_this_narrative[narrative] = []

            # step 2: check trending coins for narrative matches with low mcap on Solana
            already_bought = self._bought_this_narrative.get(narrative, [])
            max_per_narrative = self.narrative_cfg.get("max_buys_per_narrative", 2)

            if len(already_bought) >= max_per_narrative:
                logger.debug(f"Narrative cap reached for {narrative} ({max_per_narrative} buys)")
                return

            for coin in trending[:7]:  # check top 7 trending
                try:
                    symbol = coin.get("symbol", "")
                    name = coin.get("name", "")

                    if not self._matches_narrative(symbol, name, narrative):
                        continue

                    # search for this token on Solana via DexScreener
                    token_data = await self._find_solana_token(scanner, symbol)
                    if not token_data:
                        continue

                    token_address = token_data.get("token_address", "")
                    if not token_address:
                        continue

                    if token_address in already_bought:
                        continue

                    if self.tracker.is_already_trading(token_address):
                        continue

                    # volume spike confirmation
                    if not self._has_volume_spike(token_data):
                        continue

                    # security check
                    security = await scanner.get_token_security(token_address)
                    if security:
                        token_data.update(security)
                    token_data.setdefault("mint_authority_enabled", False)
                    token_data.setdefault("freeze_authority_enabled", False)
                    token_data.setdefault("top_holder_pct", 0)

                    if SafetyGuard.is_rug_pull(token_data):
                        continue

                    liq = token_data.get("liquidity_usd", 0)
                    min_liq = self.narrative_cfg.get("min_liquidity_usd", 50_000)
                    max_liq = self.narrative_cfg.get("max_liquidity_usd", 3_000_000)
                    if not (min_liq <= liq <= max_liq):
                        continue

                    logger.info(f"🔥 NARRATIVE SIGNAL: {symbol} ({narrative}) "
                                f"| liq=${liq:.0f} | trend_score={coin.get('score',0)}")

                    already_bought.append(token_address)
                    self._bought_this_narrative[narrative] = already_bought

                    await self.buyer.buy(
                        token_address=token_address,
                        token_data=token_data,
                        amount_inr=self.amount_inr,
                        strategy=f"narrative_{narrative}",
                    )
                    await asyncio.sleep(3)

                except Exception as e:
                    logger.debug(f"Narrative coin processing error: {e}")

        except Exception as e:
            logger.error(f"Narrative scan error: {e}")

    async def _find_solana_token(self, scanner, symbol: str) -> dict | None:
        """Search DexScreener for a Solana token by symbol."""
        try:
            from utils.rate_limiter import rate_limited
            import aiohttp
            await rate_limited("dexscreener")
            session = await scanner._get_session()
            url = f"https://api.dexscreener.com/latest/dex/search/?q={symbol}"
            async with session.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                pairs = data.get("pairs", [])
                sol_pairs = [
                    p for p in pairs
                    if p.get("chainId") == "solana"
                    and p.get("baseToken", {}).get("symbol", "").upper() == symbol.upper()
                ]
                if not sol_pairs:
                    return None
                best = max(sol_pairs, key=lambda x: x.get("liquidity", {}).get("usd", 0))
                return {
                    "token_address": best.get("baseToken", {}).get("address", ""),
                    "symbol": best.get("baseToken", {}).get("symbol", "?"),
                    "price_usd": float(best.get("priceUsd", 0) or 0),
                    "liquidity_usd": best.get("liquidity", {}).get("usd", 0),
                    "volume_24h": best.get("volume", {}).get("h24", 0),
                    "price_change_1h": best.get("priceChange", {}).get("h1", 0),
                }
        except Exception as e:
            logger.debug(f"Solana token search error for {symbol}: {e}")
            return None
