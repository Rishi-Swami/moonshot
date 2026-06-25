# 🌙 MOONSHOT BOT

Automated Solana crypto moonshot scanner and trader.
Runs 24/7 on Oracle Cloud free tier. Pushes live data to GitHub Pages dashboard.

---

## Architecture

```
Oracle Server (free forever)
├── main.py              ← master orchestrator
├── strategies/
│   ├── whale_copy.py    ← copies profitable whale wallets
│   ├── listing_snipe.py ← snipes brand new token listings
│   └── narrative.py     ← rides trending narratives (AI, gaming, meme)
├── bot/
│   ├── scanner.py       ← fetches data from free APIs
│   ├── buyer.py         ← executes swaps via Jupiter
│   ├── seller.py        ← profit ladder + stop loss
│   ├── tracker.py       ← position management + P&L
│   └── dashboard.py     ← writes JSON for GitHub Pages
└── data/
    └── dashboard.json   ← synced to GitHub Pages every 30s
```

---

## Profit Ladder

Every position follows this automatic exit plan:

| Trigger | Action | Why |
|---------|--------|-----|
| 2x price | Sell 50% | Recover full original capital |
| 5x price | Sell 30% | Lock serious profit |
| Remaining 20% | Hold forever | Free moonshot ride |
| -25% from entry | Sell 100% | Stop loss, cut losses fast |

After the 2x sell, your remaining position is **risk-free** — worst case you break even.

---

## Free APIs Used

| API | Purpose | Get Key |
|-----|---------|---------|
| Helius | Solana RPC + wallet tracking | helius.dev |
| DexScreener | New listings + token data | No key needed |
| Jupiter | Best swap routes + execution | No key needed |
| CoinGecko | Trending narratives | No key needed |
| Birdeye | Token security checks | birdeye.so (optional) |

---

## Setup on Oracle Cloud (Free Forever Tier)

### Step 1: Create Oracle Account
1. Go to cloud.oracle.com → sign up
2. Create an **Always Free** VM: Ubuntu 22.04, 1 OCPU, 1GB RAM
3. Note your server IP address

### Step 2: Connect to Server
```bash
ssh ubuntu@YOUR_SERVER_IP
```

### Step 3: Install Dependencies
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11+
sudo apt install python3.11 python3.11-venv python3-pip git -y

# Clone bot
git clone https://github.com/YOUR_USERNAME/moonshot-bot.git
cd moonshot-bot

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install Python packages
pip install -r requirements.txt
```

### Step 4: Configure
```bash
# Copy example env file
cp .env.example .env

# Edit with your real keys
nano .env
```

Fill in:
- `WALLET_PRIVATE_KEY` — export from Phantom wallet (Settings → Export Private Key)
- `HELIUS_API_KEY` — from helius.dev (free, takes 2 minutes)
- `BIRDEYE_API_KEY` — from birdeye.so (optional)

> ⚠️ Use a DEDICATED hot wallet with ONLY your trading budget. Never use your main wallet.

### Step 5: Add Whale Wallets
Edit `config.yaml` and add profitable Solana wallet addresses to `whales.watch_list`.

Find good whale wallets on:
- **gmgn.ai** — filter by PnL, copy top wallets
- **cielo.finance** — professional whale tracker
- **nansen.ai** — on-chain analytics

### Step 6: Test in Paper Mode
```bash
# Paper trading is ON by default in config.yaml
# Run for a few days and watch the logs

source venv/bin/activate
python main.py
```

### Step 7: Run as System Service (24/7)
```bash
# Copy service file
sudo cp moonshot.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable moonshot
sudo systemctl start moonshot

# Check it's running
sudo systemctl status moonshot

# Watch live logs
sudo journalctl -u moonshot -f
```

### Step 8: Auto-Sync Dashboard to GitHub Pages
```bash
# Make sync script executable
chmod +x sync_dashboard.sh

# Edit REPO_PATH in sync_dashboard.sh to point to your GitHub Pages clone
nano sync_dashboard.sh

# Add to crontab (runs every minute, script loops for 30s)
crontab -e
# Add this line:
* * * * * /home/ubuntu/moonshot-bot/sync_dashboard.sh >> /home/ubuntu/sync.log 2>&1
```

---

## Going Live (Disabling Paper Trading)

Only do this after paper trading shows consistent profit for 2+ weeks.

```yaml
# config.yaml
paper_trading: false   # change this line
```

Then restart the bot:
```bash
sudo systemctl restart moonshot
```

---

## Safety Features

- **Daily loss kill switch** — bot stops if daily loss > 20% of capital
- **Capital floor** — bot stops if total value drops below ₹50
- **Rug pull detection** — checks mint authority, freeze authority, top holder concentration
- **Slippage protection** — rejects trades with > 3% slippage
- **Price impact filter** — rejects trades with > 5% price impact
- **Max positions** — never holds more than 5 open positions
- **Runaway bot detector** — stops if more than 5 trades per minute
- **Automatic restart** — exponential backoff restart on crashes
- **State persistence** — survives restarts, loads open positions from disk
- **Atomic dashboard writes** — prevents partial/corrupt JSON reads

---

## Files

```
moonshot-bot/
├── main.py                 ← start here
├── config.yaml             ← all settings (safe to commit)
├── .env.example            ← copy to .env, fill in keys
├── .env                    ← NEVER commit this
├── requirements.txt
├── moonshot.service        ← systemd service for 24/7 running
├── sync_dashboard.sh       ← syncs dashboard.json to GitHub Pages
├── bot/
│   ├── buyer.py
│   ├── seller.py
│   ├── scanner.py
│   ├── tracker.py
│   └── dashboard.py
├── strategies/
│   ├── whale_copy.py
│   ├── listing_snipe.py
│   └── narrative.py
├── utils/
│   ├── config.py
│   ├── logger.py
│   ├── rate_limiter.py
│   └── safety.py
├── data/
│   ├── dashboard.json      ← live dashboard data (auto-generated)
│   └── state.json          ← bot state (auto-generated)
└── logs/
    └── moonshot_YYYY-MM-DD.log
```

---

## ⚠️ Disclaimer

This bot is for educational purposes. Crypto trading carries extreme risk.
Never invest more than you can afford to lose completely.
Start with paper trading. Validate the strategy before using real money.
