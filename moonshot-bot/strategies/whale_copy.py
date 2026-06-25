"""
Whale Copy Strategy.
Watches a curated list of known profitable wallets on Solana.
When they buy a small-cap token, we copy the trade immediately.

Logic:
1. Fetch recent swaps for each whale wallet (Helius API)
2. Filter to buys only (not sells)
3. Filter to small-cap tokens ($1M-$50M market cap)
4. Cross-check security (no mint authority, no freeze)
5. Fire buy signal
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from utils.logger import get_logger
from utils.safety import SafetyGuard

logger = get_logger("whale_copy")


class WhaleCopyStrategy:
    def __init__(self, config: dict, buyer, tracker):
        self.config = config
        self.buyer = buyer
        self.tracker = tracker
        self.watch_list: list[str] = config.get("whales", {}).get("watch_list", [])
        self.min_whale_trade_usd = config.get("whales", {}).get("min_whale_trade_usd", 500)
        self.copy_amount_inr = config["trading"].get("per_trade_inr", 20)
        self._seen_signatures: set = set()  # dedup
        self._seen_expiry: dict = {}        # clean up old sigs

    def _is_new_tx(self, sig: str) -> bool:
        """Returns True if we haven't processed this transaction before."""
        if sig in self._seen_signatures:
            return False
        self._seen_signatures.add(sig)
        self._seen_expiry[sig] = datetime.now()
        # cleanup old entries > 1 hour
        cutoff = datetime.now() - timedelta(hours=1)
        expired = [s for s, t in self._seen_expiry.items() if t < cutoff]
        for s in expired:
            self._seen_signatures.discard(s)
            del self._seen_expiry[s]
        return True

    def _is_valid_market_cap(self, token_data: dict) -> bool:
        """Only target $1M-$50M market cap — sweet spot for moonshots."""
        liq = token_data.get("liquidity_usd", 0)
        # liquidity is roughly 30-50% of market cap for healthy tokens
        # so $300K-$25M liquidity ≈ $1M-$50M mcap
        min_liq = self.config.get("whales", {}).get("min_liquidity_usd", 30_000)
        max_liq = self.config.get("whales", {}).get("max_liquidity_usd", 5_000_000)
        return min_liq <= liq <= max_liq

    async def scan(self, scanner):
        """Called every 20 seconds by Scanner."""
        if not self.watch_list:
            logger.debug("Whale watch list is empty. Add wallets to config.yaml")
            return

        for wallet in self.watch_list:
            try:
                txns = await scanner.get_wallet_transactions(wallet, limit=5)
                for tx in txns:
                    await self._process_transaction(tx, scanner)
            except Exception as e:
                logger.debug(f"Whale scan error for {wallet[:8]}...: {e}")
            await asyncio.sleep(1)  # small gap between wallets

    async def _process_transaction(self, tx: dict, scanner):
        """Analyse one transaction. Fire buy if signal is strong."""
        sig = tx.get("signature", "")
        if not sig or not self._is_new_tx(sig):
            return

        # only look at swaps
        tx_type = tx.get("type", "")
        if tx_type != "SWAP":
            return

        # extract swap details from Helius enhanced transaction
        token_transfers = tx.get("tokenTransfers", [])
        if not token_transfers:
            return

        # find the output token (the one whale received)
        out_transfer = None
        for transfer in token_transfers:
            if transfer.get("toUserAccount") and transfer.get("mint") not in [
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
                "So11111111111111111111111111111111111111112",      # SOL
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
            ]:
                out_transfer = transfer
                break

        if not out_transfer:
            return

        token_address = out_transfer.get("mint", "")
        if not token_address:
            return

        # skip if already in portfolio
        if self.tracker.is_already_trading(token_address):
            return

        # get token data
        token_data = await scanner.get_token_info_dexscreener(token_address)
        if not token_data:
            logger.debug(f"Whale buy: no DexScreener data for {token_address[:8]}...")
            return

        symbol = token_data.get("symbol", "?")

        # market cap filter
        if not self._is_valid_market_cap(token_data):
            logger.debug(f"Whale buy skipped {symbol}: market cap out of range "
                         f"(liq=${token_data.get('liquidity_usd',0):.0f})")
            return

        # security check (Birdeye if available, else basic checks)
        security = await scanner.get_token_security(token_address)
        if security:
            token_data.update(security)
        else:
            # fallback basic checks
            token_data.setdefault("mint_authority_enabled", False)
            token_data.setdefault("freeze_authority_enabled", False)
            token_data.setdefault("top_holder_pct", 0)

        if SafetyGuard.is_rug_pull(token_data):
            logger.info(f"Whale buy BLOCKED (rug): {symbol}")
            return

        logger.info(f"🐋 WHALE SIGNAL: {symbol} | liq=${token_data['liquidity_usd']:.0f} "
                    f"| vol24h=${token_data.get('volume_24h',0):.0f}")

        # fire buy
        await self.buyer.buy(
            token_address=token_address,
            token_data=token_data,
            amount_inr=self.copy_amount_inr,
            strategy="whale_copy",
        )
