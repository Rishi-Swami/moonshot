"""
Buyer — Executes token purchases via Jupiter Aggregator.
Jupiter is the best DEX aggregator on Solana — finds cheapest route
across Raydium, Orca, Meteora etc automatically.

Flow:
1. Validate signal (safety checks, rug detection)
2. Get quote from Jupiter
3. Check slippage is acceptable
4. Sign and send transaction
5. Confirm on-chain
6. Register position in tracker
"""

import asyncio
import aiohttp
import base64
import json
from typing import Optional
from solders.keypair import Keypair  # type: ignore
from solders.transaction import VersionedTransaction  # type: ignore
from solana.rpc.async_api import AsyncClient  # type: ignore
from solana.rpc.commitment import Confirmed  # type: ignore

from bot.tracker import PositionTracker, Position
from utils.logger import get_logger
from utils.safety import SafetyGuard
from utils.rate_limiter import rate_limited

logger = get_logger("buyer")

JUPITER_QUOTE  = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP   = "https://quote-api.jup.ag/v6/swap"
USDC_MINT      = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT       = "So11111111111111111111111111111111111111112"
LAMPORTS       = 1_000_000_000  # 1 SOL in lamports
USDC_DECIMALS  = 1_000_000      # 1 USDC = 1,000,000 units


class Buyer:
    def __init__(self, config: dict, tracker: PositionTracker):
        self.config = config
        self.tracker = tracker
        self.safety = SafetyGuard(config, tracker)
        self._keypair = self._load_keypair()
        self.rpc_url = config.get("solana", {}).get(
            "rpc_url",
            f"https://mainnet.helius-rpc.com/?api-key={config['keys']['helius_api_key']}"
        )
        self.session: Optional[aiohttp.ClientSession] = None
        # INR to USD conversion (approximate, updated periodically)
        self._inr_to_usd = 0.012

    def _load_keypair(self) -> Optional[Keypair]:
        raw = self.config["keys"].get("wallet_private_key")
        if not raw:
            logger.error("No wallet private key found in environment.")
            return None
        try:
            # supports both base58 string and JSON array format
            if raw.startswith("["):
                secret = bytes(json.loads(raw))
            else:
                import base58  # type: ignore
                secret = base58.b58decode(raw)
            return Keypair.from_bytes(secret)
        except Exception as e:
            logger.error(f"Failed to load keypair: {e}")
            return None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self.session

    # ── PRE-FLIGHT CHECKS ────────────────────────────────

    def _pre_flight(self, token_address: str, amount_inr: float,
                    token_data: dict) -> tuple[bool, str]:
        """
        All safety checks before buying.
        Returns (ok, reason_if_failed).
        """
        if not self._keypair:
            return False, "No wallet keypair loaded"

        if not self.tracker.can_open_position():
            return False, f"Max open positions reached ({self.config['trading']['max_open_positions']})"

        if self.tracker.is_already_trading(token_address):
            return False, f"Already have open position in {token_data.get('symbol','?')}"

        safety = self.safety.check()
        if not safety["safe"]:
            return False, safety["reason"]

        if SafetyGuard.is_rug_pull(token_data):
            return False, f"Rug pull detected for {token_data.get('symbol','?')}"

        min_trade = self.config["trading"].get("min_trade_inr", 5)
        max_trade = self.config["trading"].get("max_trade_inr", 50)
        if amount_inr < min_trade:
            return False, f"Trade size ₹{amount_inr} below minimum ₹{min_trade}"
        if amount_inr > max_trade:
            amount_inr = max_trade
            logger.warning(f"Trade size capped at ₹{max_trade}")

        return True, ""

    # ── JUPITER QUOTE & SWAP ──────────────────────────────

    async def _get_jupiter_quote(self, token_out_mint: str,
                                  usdc_amount: int) -> Optional[dict]:
        """Get best swap route from Jupiter."""
        try:
            await rate_limited("jupiter")
            session = await self._get_session()
            params = {
                "inputMint":   USDC_MINT,
                "outputMint":  token_out_mint,
                "amount":      usdc_amount,
                "slippageBps": self.config["trading"].get("slippage_bps", 300),  # 3%
                "onlyDirectRoutes": False,
            }
            async with session.get(JUPITER_QUOTE, params=params) as r:
                if r.status == 400:
                    body = await r.text()
                    logger.warning(f"Jupiter quote error 400: {body}")
                    return None
                if r.status != 200:
                    return None
                return await r.json()
        except asyncio.TimeoutError:
            logger.warning("Jupiter quote timed out")
            return None
        except Exception as e:
            logger.error(f"Jupiter quote error: {e}")
            return None

    async def _execute_jupiter_swap(self, quote: dict) -> Optional[str]:
        """Build, sign, and send swap transaction. Returns tx signature."""
        if not self._keypair:
            return None
        try:
            await rate_limited("jupiter")
            session = await self._get_session()

            swap_payload = {
                "quoteResponse": quote,
                "userPublicKey": str(self._keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            }

            async with session.post(JUPITER_SWAP, json=swap_payload) as r:
                if r.status != 200:
                    body = await r.text()
                    logger.error(f"Jupiter swap build failed {r.status}: {body}")
                    return None
                swap_data = await r.json()

            # deserialize and sign
            tx_bytes = base64.b64decode(swap_data["swapTransaction"])
            tx = VersionedTransaction.from_bytes(tx_bytes)
            signed_tx = self._keypair.sign_message(bytes(tx.message))

            # send via Helius RPC
            async with AsyncClient(self.rpc_url) as client:
                resp = await client.send_raw_transaction(
                    bytes(tx),
                    opts={"skipPreflight": False, "maxRetries": 3}
                )
                if resp.value is None:
                    logger.error("Transaction send returned None")
                    return None

                sig = str(resp.value)
                logger.info(f"Transaction sent: {sig}")

                # wait for confirmation (max 60s)
                for attempt in range(12):
                    await asyncio.sleep(5)
                    status = await client.get_signature_statuses([resp.value])
                    if status.value[0] is not None:
                        if status.value[0].err:
                            logger.error(f"Transaction failed on-chain: {status.value[0].err}")
                            return None
                        logger.info(f"Transaction confirmed: {sig}")
                        return sig
                    logger.debug(f"Waiting for confirmation... ({attempt+1}/12)")

                logger.warning(f"Transaction confirmation timeout: {sig}")
                return sig  # return anyway, may confirm later

        except Exception as e:
            logger.error(f"Swap execution error: {e}", exc_info=True)
            return None

    # ── MAIN BUY FUNCTION ─────────────────────────────────

    async def buy(self, token_address: str, token_data: dict,
                  amount_inr: float, strategy: str) -> Optional[Position]:
        """
        Main entry point for all strategies.
        Returns Position if successful, None if skipped/failed.
        """
        symbol = token_data.get("symbol", "?")

        # pre-flight
        ok, reason = self._pre_flight(token_address, amount_inr, token_data)
        if not ok:
            logger.info(f"BUY SKIPPED [{symbol}]: {reason}")
            return None

        # convert INR → USDC units
        amount_usd = amount_inr * self._inr_to_usd
        usdc_units = int(amount_usd * USDC_DECIMALS)

        if usdc_units < 100_000:  # < $0.10 USDC - too small for on-chain
            logger.info(f"BUY SKIPPED [{symbol}]: trade too small after conversion (${amount_usd:.3f})")
            return None

        logger.info(f"[{strategy}] BUY SIGNAL: {symbol} | ₹{amount_inr:.2f} (~${amount_usd:.2f})")

        # get quote
        quote = await self._get_jupiter_quote(token_address, usdc_units)
        if not quote:
            self.tracker.add_error(f"Jupiter quote failed for {symbol}")
            return None

        # slippage check
        out_amount = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct", 0))
        max_impact = self.config["trading"].get("max_price_impact_pct", 5)
        if price_impact > max_impact:
            logger.warning(f"BUY SKIPPED [{symbol}]: price impact {price_impact:.1f}% > {max_impact}%")
            return None

        # calculate entry price
        entry_price = token_data.get("price_usd", 0)
        if entry_price <= 0 and out_amount > 0:
            # estimate from quote
            entry_price = amount_usd / (out_amount / 1e9)

        # PAPER TRADING MODE - skip actual transaction
        if self.config.get("paper_trading", False):
            logger.info(f"[PAPER] BUY {symbol} @ ${entry_price:.8f} | ₹{amount_inr:.2f}")
            pos = Position(
                token_address=token_address,
                symbol=symbol,
                entry_price_usd=entry_price,
                amount_inr=amount_inr,
                amount_tokens=out_amount / 1e9,
                strategy=strategy,
            )
            self.tracker.add_position(pos)
            self.tracker.save_state()
            self.safety.record_trade()
            return pos

        # LIVE TRADING — execute swap
        sig = await self._execute_jupiter_swap(quote)
        if not sig:
            self.tracker.add_error(f"Swap execution failed for {symbol}")
            return None

        pos = Position(
            token_address=token_address,
            symbol=symbol,
            entry_price_usd=entry_price,
            amount_inr=amount_inr,
            amount_tokens=out_amount / 1e9,
            strategy=strategy,
        )
        self.tracker.add_position(pos)
        self.tracker.save_state()
        self.safety.record_trade()
        logger.info(f"✅ BUY CONFIRMED: {symbol} | {sig[:16]}...")
        return pos

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
