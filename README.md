<div align="center">

# 🌙 MOONSHOT

*An automated Solana crypto scanner that hunts moonshots while you sleep.*

![Status](https://img.shields.io/badge/status-live-brightgreen?style=flat-square)
![Chain](https://img.shields.io/badge/chain-Solana-9945FF?style=flat-square)
![Mode](https://img.shields.io/badge/mode-paper_trading-gold?style=flat-square)
![Cost](https://img.shields.io/badge/server_cost-₹0-blueviolet?style=flat-square)

**[🚀 Live Dashboard](https://rishi-swami.github.io/moonshot)**

</div>

---

## What It Does

Moonshot is a fully automated bot that runs on GitHub Actions for free. Every 5 minutes it wakes up, scans the Solana blockchain for high-probability moonshot opportunities, and goes back to sleep. All results show up live on the dashboard.

Three strategies run in parallel on every scan:

- **Whale Copy** — watches profitable wallets and copies their buys before the market reacts
- **Listing Snipe** — detects brand new token pairs within minutes of launch
- **Narrative Momentum** — rides trending narratives like AI, gaming, and memecoins at the earliest signal

---

## How Money Is Managed

Every position follows this automatic exit plan with zero human input:

| Trigger | Action |
|--------|--------|
| Price hits 2x | Sells 50% — original capital fully recovered |
| Price hits 5x | Sells 30% — serious profit locked |
| Remaining 20% | Holds free — moonshot ride to 50x+ |
| Price drops 25% | Sells 100% — stop loss, cut fast |

After the 2x sell the remaining position costs nothing. Worst case from there is break even.

---

## Stack

| Layer | Tool | Cost |
|-------|------|------|
| Compute | GitHub Actions | Free |
| Blockchain | Solana via Helius RPC | Free |
| Swap execution | Jupiter Aggregator | Free |
| New listings | DexScreener API | Free |
| Trend detection | CoinGecko Trending | Free |
| Security checks | Birdeye API | Free |
| Dashboard | GitHub Pages | Free |

Total monthly cost — ₹0.

---

## Safety

- Daily loss kill switch — stops if losses exceed 20% of capital in one day
- Capital floor — stops if total value drops below ₹50
- Rug pull detector — checks mint authority, freeze authority, top holder concentration
- Slippage guard — skips any trade with slippage above 3%
- Max 5 open positions at any time
- Automatic restart with exponential backoff on any crash
- State saved to disk — survives restarts with all positions intact

---

## Schedule

Runs every 5 minutes during active market hours (12:00–23:59 UTC / 5:30 PM–5:30 AM IST). Stays quiet during dead hours to stay within GitHub's free tier.

---

## Dashboard

The live dashboard updates every 5 minutes automatically. It shows total portfolio value, today's profit and loss, win rate, open positions, live trade feed, and any bot errors.

**[→ Open Dashboard](https://rishi-swami.github.io/moonshot)**

---

<div align="center">

*CURRENTLY IN TESTING PHASE, NOT FOR USE | Testing in paper trading mode — no real money at risk.*

</div>
