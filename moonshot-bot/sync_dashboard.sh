#!/bin/bash
# ──────────────────────────────────────────────────────
# sync_dashboard.sh
# Runs as a cron job on Oracle server every 30 seconds.
# Pushes updated dashboard.json to your GitHub Pages repo
# so the live website reflects bot data.
#
# SETUP:
# 1. Clone your moonshot GitHub repo on Oracle server
# 2. Set REPO_PATH below
# 3. Run: chmod +x sync_dashboard.sh
# 4. Add to crontab: * * * * * /path/to/sync_dashboard.sh
#    (cron minimum is 1 minute, script loops internally for 30s)
# ──────────────────────────────────────────────────────

REPO_PATH="/home/ubuntu/moonshot-pages"   # your GitHub Pages repo clone
JSON_SOURCE="/home/ubuntu/moonshot-bot/data/dashboard.json"
BRANCH="main"

# run twice per minute (every 30 seconds)
for i in 1 2; do
    if [ -f "$JSON_SOURCE" ]; then
        cp "$JSON_SOURCE" "$REPO_PATH/data/dashboard.json"
        cd "$REPO_PATH" || exit 1

        if ! git diff --quiet data/dashboard.json; then
            git add data/dashboard.json
            git commit -m "bot: update dashboard $(date '+%H:%M:%S')" --quiet
            git push origin "$BRANCH" --quiet
            echo "[$(date '+%H:%M:%S')] Dashboard synced to GitHub Pages"
        fi
    else
        echo "[$(date '+%H:%M:%S')] dashboard.json not found, bot may not be running"
    fi

    if [ $i -eq 1 ]; then
        sleep 30
    fi
done
