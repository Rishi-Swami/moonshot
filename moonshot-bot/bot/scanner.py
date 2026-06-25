"""
Scanner — Data Feed Hub.
Polls DexScreener, Helius, Birdeye, CoinGecko on free tiers.
Dispatches signals to each strategy.
"""

import asyncio
import aiohttp
from typing import Optional
from utils.logger import get_logger
from utils.rate_limiter import rate_limited

logger = get_logger("scanner")

DEXSCREENER_BASE = "https://api.dexscreener.com/latest"
COINGECKO_BASE   = "https://api.coingecko.com/api/v3"
BIRDEYE_BASE     = "https://public-api.birdeye.so"
HELIUS_BASE      = "https://api.helius.xyz/v0"
JUPITER_PRICE    = "https://price.jup.ag/v6/price"


class Scanner:
    def __init__(self, config: dict, strategies: list):
        self.config = config
        self.strategies = strategies
        self.helius_key = config["keys"]["helius_api_key"]
        self.birdeye_key = config["keys"].get("birdeye_api_key")
        self.session: Optional[aiohttp.ClientSession] = None

        # scan intervals (seconds)
        self.whale_interval     = config.get("scan_intervals", {}).get("whale_seconds", 20)
        self.listing_interval   = config.get("scan_intervals", {}).get("listing_seconds", 30)
        self.narrative_interval = config.get("scan_intervals", {}).get("narrative_seconds", 120)
        self.price_interval     = config.get("scan_intervals", {}).get("price_seconds", 10)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    # ── PRICE ────────────────────────────────────────────

    async def get_token_price(self, token_address: str) -> Optional[float]:
        """Fetch price from Jupiter (most accurate for Solana)."""
        try:
            await rate_limited("jupiter")
            session = await self._get_session()
            async with session.get(f"{JUPITER_PRICE}?ids={token_address}") as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data.get("data", {}).get(token_address, {}).get("price")
        except Exception as e:
            logger.debug(f"Jupiter price fetch failed for {token_address}: {e}")
            return None

    # ── WHALE TRACKING ───────────────────────────────────

    async def get_wallet_transactions(self, wallet_address: str,
                                       limit: int = 10) -> list:
        """Fetch recent transactions for a wallet via Helius."""
        try:
            await rate_limited("helius")
            session = await self._get_session()
            url = f"{HELIUS_BASE}/addresses/{wallet_address}/transactions"
            params = {"api-key": self.helius_key, "limit": limit, "type": "SWAP"}
            async with session.get(url, params=params) as r:
                if r.status == 429:
                    logger.warning("Helius rate limit hit, backing off 60s")
                    await asyncio.sleep(60)
                    return []
                if r.status != 200:
                    return []
                return await r.json()
        except asyncio.TimeoutError:
            logger.debug(f"Helius timeout for wallet {wallet_address[:8]}...")
            return []
        except Exception as e:
            logger.debug(f"Helius wallet fetch error: {e}")
            return []

    # ── NEW LISTINGS ──────────────────────────────────────

    async def get_new_solana_pairs(self) -> list:
        """Fetch newest Solana pairs from DexScreener."""
        try:
            await rate_limited("dexscreener")
            session = await self._get_session()
            url = f"{DEXSCREENER_BASE}/dex/search/?q=SOL"
            async with session.get(url) as r:
                if r.status != 200:
                    return []
                data = await r.json()
                pairs = data.get("pairs", [])
                # filter to Solana only, very new (< 2 hours old)
                sol_pairs = []
                for p in pairs:
                    if p.get("chainId") != "solana":
                        continue
                    age_mins = p.get("pairCreatedAt", 0)
                    if age_mins and (asyncio.get_event_loop().time() * 1000 - age_mins) < 7_200_000:
                        sol_pairs.append(p)
                return sol_pairs
        except Exception as e:
            logger.debug(f"DexScreener new pairs error: {e}")
            return []

    async def get_token_info_dexscreener(self, token_address: str) -> Optional[dict]:
        """Get detailed token info from DexScreener."""
        try:
            await rate_limited("dexscreener")
            session = await self._get_session()
            url = f"{DEXSCREENER_BASE}/dex/tokens/{token_address}"
            async with session.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                pairs = data.get("pairs", [])
                if not pairs:
                    return None
                # use highest liquidity pair
                best = max(pairs, key=lambda x: x.get("liquidity", {}).get("usd", 0))
                return {
                    "symbol": best.get("baseToken", {}).get("symbol", "?"),
                    "price_usd": float(best.get("priceUsd", 0) or 0),
                    "liquidity_usd": best.get("liquidity", {}).get("usd", 0),
                    "volume_24h": best.get("volume", {}).get("h24", 0),
                    "price_change_1h": best.get("priceChange", {}).get("h1", 0),
                    "price_change_24h": best.get("priceChange", {}).get("h24", 0),
                    "txns_1h": best.get("txns", {}).get("h1", {}).get("buys", 0),
                    "dex": best.get("dexId", "unknown"),
                    "pair_address": best.get("pairAddress", ""),
                }
        except Exception as e:
            logger.debug(f"DexScreener token info error: {e}")
            return None

    # ── NARRATIVE / TRENDING ──────────────────────────────

    async def get_trending_coins(self) -> list:
        """Get trending coins from CoinGecko (free, no key needed)."""
        try:
            await rate_limited("coingecko")
            session = await self._get_session()
            url = f"{COINGECKO_BASE}/search/trending"
            async with session.get(url) as r:
                if r.status == 429:
                    await asyncio.sleep(60)
                    return []
                if r.status != 200:
                    return []
                data = await r.json()
                return [
                    {
                        "id": c["item"]["id"],
                        "symbol": c["item"]["symbol"],
                        "name": c["item"]["name"],
                        "market_cap_rank": c["item"].get("market_cap_rank"),
                        "score": c["item"].get("score", 0),
                    }
                    for c in data.get("coins", [])
                ]
        except Exception as e:
            logger.debug(f"CoinGecko trending error: {e}")
            return []

    # ── BIRDEYE (optional, better data if key available) ──

    async def get_token_security(self, token_address: str) -> Optional[dict]:
        """Check token security via Birdeye (mint authority, freeze, etc.)."""
        if not self.birdeye_key:
            return None
        try:
            await rate_limited("birdeye")
            session = await self._get_session()
            url = f"{BIRDEYE_BASE}/defi/token_security"
            headers = {"X-API-KEY": self.birdeye_key}
            params = {"address": token_address}
            async with session.get(url, headers=headers, params=params) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                sec = data.get("data", {})
                return {
                    "mint_authority_enabled": sec.get("mintAuthorityAddress") is not None,
                    "freeze_authority_enabled": sec.get("freezeAuthorityAddress") is not None,
                    "top_holder_pct": sec.get("top10HolderPercent", 0) * 100,
                    "creator_pct": sec.get("creatorPercentage", 0) * 100,
                }
        except Exception as e:
            logger.debug(f"Birdeye security check error: {e}")
            return None

    # ── MAIN SCAN LOOPS ───────────────────────────────────

    async def _whale_scan_loop(self):
        whale_strategy = next((s for s in self.strategies
                                if s.__class__.__name__ == "WhaleCopyStrategy"), None)
        if not whale_strategy:
            return
        while True:
            try:
                await whale_strategy.scan(self)
            except Exception as e:
                logger.error(f"Whale scan error: {e}")
            await asyncio.sleep(self.whale_interval)

    async def _listing_scan_loop(self):
        listing_strategy = next((s for s in self.strategies
                                   if s.__class__.__name__ == "ListingSnipeStrategy"), None)
        if not listing_strategy:
            return
        while True:
            try:
                await listing_strategy.scan(self)
            except Exception as e:
                logger.error(f"Listing scan error: {e}")
            await asyncio.sleep(self.listing_interval)

    async def _narrative_scan_loop(self):
        narrative_strategy = next((s for s in self.strategies
                                    if s.__class__.__name__ == "NarrativeStrategy"), None)
        if not narrative_strategy:
            return
        while True:
            try:
                await narrative_strategy.scan(self)
            except Exception as e:
                logger.error(f"Narrative scan error: {e}")
            await asyncio.sleep(self.narrative_interval)

    async def run(self):
        logger.info("Scanner starting all feed loops...")
        await asyncio.gather(
            self._whale_scan_loop(),
            self._listing_scan_loop(),
            self._narrative_scan_loop(),
        )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
