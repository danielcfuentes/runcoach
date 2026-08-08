#!/usr/bin/env python3
"""Set the Telegram webhook URL. Run after deploying."""
import asyncio
import sys

sys.path.insert(0, ".")
from src.bot.telegram_bot import set_webhook, delete_webhook
from src.config import settings


async def main():
    if len(sys.argv) == 2 and sys.argv[1] == "delete":
        result = await delete_webhook()
        print("Webhook deleted:", result)
        return

    url = settings.webhook_url or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not url:
        print("Usage: python scripts/set_telegram_webhook.py <webhook_url>")
        print("   or: python scripts/set_telegram_webhook.py delete")
        sys.exit(1)

    tg_webhook = f"{url.rstrip('/')}/webhook/telegram"
    result = await set_webhook(tg_webhook)
    print("Webhook set:", result)
    print(f"URL: {tg_webhook}")


asyncio.run(main())
