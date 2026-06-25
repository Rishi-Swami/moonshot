"""
Token-bucket rate limiter.
Prevents API bans by limiting calls per second per service.
"""

import asyncio
import time
from collections import defaultdict
from utils.logger import get_logger

logger = get_logger("rate_limiter")


class RateLimiter:
    """Simple sliding window rate limiter."""

    def __init__(self):
        # calls_per_second per API name
        self._limits = {
            "helius":      10,
            "dexscreener":  5,
            "birdeye":      5,
            "jupiter":      3,
            "coingecko":    2,
            "solscan":      3,
        }
        self._windows = defaultdict(list)

    async def wait(self, api: str):
        limit = self._limits.get(api, 5)
        now = time.monotonic()
        window = self._windows[api]

        # remove calls older than 1 second
        self._windows[api] = [t for t in window if now - t < 1.0]

        if len(self._windows[api]) >= limit:
            sleep_for = 1.0 - (now - self._windows[api][0]) + 0.01
            if sleep_for > 0:
                logger.debug(f"Rate limit hit for {api}, sleeping {sleep_for:.2f}s")
                await asyncio.sleep(sleep_for)

        self._windows[api].append(time.monotonic())


# singleton
_limiter = RateLimiter()


async def rate_limited(api: str):
    await _limiter.wait(api)
