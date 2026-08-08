#!/usr/bin/env bash
# First-time setup script
set -euo pipefail

echo "=== RunCoach Setup ==="

# Check for .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✓ Created .env from .env.example — fill in your API keys before continuing"
    echo ""
    echo "Required keys:"
    echo "  STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET  — from https://www.strava.com/settings/api"
    echo "  GARMIN_EMAIL / GARMIN_PASSWORD           — your Garmin Connect credentials"
    echo "  ANTHROPIC_API_KEY                        — from https://console.anthropic.com"
    echo "  TELEGRAM_BOT_TOKEN                       — from @BotFather on Telegram"
    echo "  TELEGRAM_CHAT_ID                         — your personal chat ID (message @userinfobot)"
    echo "  WEBHOOK_URL                              — your public URL (use ngrok for local dev)"
    echo ""
else
    echo "✓ .env already exists"
fi

# Python venv
if [ ! -d .venv ]; then
    python3.11 -m venv .venv
    echo "✓ Created .venv"
fi
source .venv/bin/activate
pip install -e ".[dev]" -q
echo "✓ Dependencies installed"

echo ""
echo "=== Next steps ==="
echo "1. Fill in .env"
echo "2. Start the database:  docker-compose up db -d"
echo "3. Run the app:         source .venv/bin/activate && uvicorn src.main:app --reload"
echo "4. Authorize Strava:    open http://localhost:8000/admin/strava-auth-url (get URL, visit it)"
echo "5. Set Telegram webhook: curl -X POST http://localhost:8000/admin/sync-now  (after tunnel is up)"
echo "6. Trigger a manual sync: curl -X POST http://localhost:8000/admin/sync-now"
