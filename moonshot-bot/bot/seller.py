"""
Seller — Profit Ladder & Stop Loss Engine.

Profit ladder per position:
  • 2x (100% gain) → sell 50% → original capital fully recovered
  • 5x (400% gain) → sell 30% → lock serious profit
  • Hold 20%       → free ride, can go to 50x+
  • Stop loss: -25% from entry → full close, cut losses fast

Emergency close: closes all positions at market price.
"""

import asyncio
import aiohttp
import base64
import json
from typing import Optional

from bot.tracker import PositionTracker, Position
from utils.logger import get_logger
from utils.rate_limiter import rate_limited

logger = get_logger("seller")

JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP  = "https://quote-api.jup.ag/v6/swap"
USDC_MINT     = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class Seller:
    def __init__(self, config: dict, tracker: PositionTracker):
        self.config = config
        self.tracker = tracker
        self.session: Optional[aiohttp.ClientSession] = None

        # load keypair same way buyer does
        from solders.keypair import Keypair  # type: ignore
        raw = config["keys"].get("wallet_private_key")
        self._keypair = None
        if raw:
            try:
                if raw.startswith("["):
                    secret = bytes(json.loads(raw))
                else:
                    import base58  # type: ignore
                    secret = base58.b58decode(raw)
                self._keypair = Keypair.from_bytes(secret)
            except Exception as e:
                logger.error(f"Seller keypair load failed: {e}")

        # ladder config
        ladder = config.get("profit_ladder", {})
        self.sell_50_at_x  = ladder.get("sell_50pct_at_x", 2.0)   # 2x
        self.sell_30_at_x  = ladder.get("sell_30pct_at_x", 5.0)   # 5x
        self.stop_loss_pct = ladder.get("stop_loss_pct", 25)       # -25%
        self._inr_to_usd   = 0.012

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self.session

    async def _get_current_price(self, token_address: str) -> Optional[float]:
        """Get current price via Jupiter price API."""
        try:
            await rate_limited("jupiter")
            session = await self._get_session()
            url = f"https://price.jup.ag/v6/price?ids={token_address}"
            async with session.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data.get("data", {}).get(token_address, {}).get("price")
        except Exception as e:
            logger.debug(f"Price fetch error for {token_address[:8]}...: {e}")
            return None

    async def _sell_tokens(self, position: Position, fraction: float,
                            reason: str) -> bool:
        """
        Sell a fraction of tokens back to USDC.
        fraction: 0.0 to 1.0
        """
        tokens_to_sell = position.amount_tokens * fraction
        token_units = int(tokens_to_sell * 1e9)  # lamports equivalent

        if token_units < 1000:
            logger.warning(f"Token amount too small to sell: {token_units}")
            return False

        # PAPER TRADING
        if self.config.get("paper_trading", False):
            current_price = position.current_price_usd
            pnl = position.amount_inr * fraction * (current_price / position.entry_price_usd - 1)
            logger.info(f"[PAPER] SELL {fraction*100:.0f}% of {position.symbol} "
                        f"@ ${current_price:.8f} | PnL: ₹{pnl:.2f} [{reason}]")
            self.tracker.close_position(position.id, current_price, fraction, reason)
            self.tracker.save_state()
            return True

        # LIVE — get sell quote
        try:
            await rate_limited("jupiter")
            session = await self._get_session()
            params = {
                "inputMint":   position.token_address,
                "outputMint":  USDC_MINT,
                "amount":      token_units,
                "slippageBps": self.config["trading"].get("slippage_bps", 500),
            }
            async with session.get(JUPITER_QUOTE, params=params) as r:
                if r.status != 200:
                    logger.error(f"Sell quote failed for {position.symbol}")
                    return False
                quote = await r.json()

            # execute swap
            from solders.transaction import VersionedTransaction  # type: ignore
            from solana.rpc.async_api import AsyncClient  # type: ignore

            swap_payload = {
                "quoteResponse": quote,
                "userPublicKey": str(self._keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            }

            async with session.post(JUPITER_SWAP, json=swap_payload) as r:
                if r.status != 200:
                    logger.error(f"Sell swap build failed for {position.symbol}")
                    return False
                swap_data = await r.json()

            tx_bytes = base64.b64decode(swap_data["swapTransaction"])
            tx = VersionedTransaction.from_bytes(tx_bytes)

            rpc_url = self.config.get("solana", {}).get(
                "rpc_url",
                f"https://mainnet.helius-rpc.com/?api-key={self.config['keys']['helius_api_key']}"
            )
            async with AsyncClient(rpc_url) as client:
                resp = await client.send_raw_transaction(bytes(tx))
                if resp.value is None:
                    return False
                sig = str(resp.value)

                # confirm
                for _ in range(12):
                    await asyncio.sleep(5)
                    status = await client.get_signature_statuses([resp.value])
                    if status.value[0] is not None:
                        if status.value[0].err:
                            logger.error(f"Sell tx failed: {status.value[0].err}")
                            return False
                        self.tracker.close_position(
                            position.id,
                            position.current_price_usd,
                            fraction,
                            reason
                        )
                        self.tracker.save_state()
                        logger.info(f"✅ SELL {position.symbol} {fraction*100:.0f}% [{reason}] {sig[:16]}...")
                        return True

            return False

        except Exception as e:
            logger.error(f"Sell execution error for {position.symbol}: {e}", exc_info=True)
            self.tracker.add_error(f"Sell failed for {position.symbol}: {e}")
            return False

    # ── PROFIT LADDER LOGIC ───────────────────────────────

    async def check_all_positions(self):
        """
        Called every 10 seconds.
        Updates prices and triggers ladder/stop-loss.
        """
        positions = list(self.tracker.open_positions.values())

        for pos in positions:
            try:
                current_price = await self._get_current_price(pos.token_address)
                if current_price is None:
                    continue

                pos.update_price(current_price)
                multiplier = current_price / pos.entry_price_usd if pos.entry_price_usd > 0 else 1

                # ── STOP LOSS ──
                if current_price <= pos.stop_loss_price:
                    logger.warning(f"STOP LOSS triggered: {pos.symbol} "
                                   f"${pos.entry_price_usd:.8f} → ${current_price:.8f}")
                    await self._sell_tokens(pos, 1.0, "stop_loss")
                    continue

                # ── LADDER LEVEL 1: 2x → sell 50% ──
                if not pos.sold_50_pct and multiplier >= self.sell_50_at_x:
                    logger.info(f"LADDER 2x hit: {pos.symbol} — selling 50%")
                    success = await self._sell_tokens(pos, 0.5, "ladder_2x")
                    if success:
                        pos.sold_50_pct = True
                        # move stop loss to break-even now that capital recovered
                        pos.stop_loss_price = pos.entry_price_usd
                    continue

                # ── LADDER LEVEL 2: 5x → sell 30% ──
                if pos.sold_50_pct and not pos.sold_30_pct and multiplier >= self.sell_30_at_x:
                    logger.info(f"LADDER 5x hit: {pos.symbol} — selling 30%")
                    success = await self._sell_tokens(pos, 0.30 / 0.50, "ladder_5x")
                    if success:
                        pos.sold_30_pct = True
                        # trailing stop: 40% below current price
                        pos.stop_loss_price = current_price * 0.60
                    continue

                # ── MOONSHOT HOLD: remaining 20% ──
                # Only stop out if it falls back below 2x from here
                if pos.sold_50_pct and pos.sold_30_pct:
                    moonshot_stop = pos.entry_price_usd * 2.0  # don't let winner become loser
                    if current_price < moonshot_stop:
                        logger.info(f"MOONSHOT STOP: {pos.symbol} fell below 2x — closing remainder")
                        await self._sell_tokens(pos, 1.0, "moonshot_trailing_stop")

            except Exception as e:
                logger.error(f"Position check error for {pos.symbol}: {e}")
                self.tracker.add_error(f"Position check failed for {pos.symbol}: {e}")

    async def emergency_close_all(self):
        """Kill switch — close every open position immediately."""
        logger.critical("EMERGENCY CLOSE ALL POSITIONS")
        positions = list(self.tracker.open_positions.values())
        for pos in positions:
            try:
                price = await self._get_current_price(pos.token_address)
                price = price or pos.current_price_usd
                await self._sell_tokens(pos, 1.0, "emergency_close")
                logger.info(f"Emergency closed: {pos.symbol}")
            except Exception as e:
                logger.error(f"Emergency close failed for {pos.symbol}: {e}")
        logger.critical(f"Emergency close complete. {len(positions)} positions processed.")

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
