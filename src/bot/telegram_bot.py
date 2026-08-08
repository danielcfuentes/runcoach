"""Telegram bot — handles inbound messages and outbound sends."""
from __future__ import annotations

import httpx

from src.config import settings

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def send_message(text: str, chat_id: str | None = None, parse_mode: str = "Markdown") -> dict:
    cid = chat_id or settings.telegram_chat_id
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": cid, "text": text, "parse_mode": parse_mode},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


async def set_webhook(url: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TELEGRAM_API}/setWebhook",
            json={"url": url},
        )
        resp.raise_for_status()
        return resp.json()


async def delete_webhook() -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{TELEGRAM_API}/deleteWebhook")
        resp.raise_for_status()
        return resp.json()


async def get_updates(offset: int | None = None) -> list[dict]:
    params = {}
    if offset is not None:
        params["offset"] = offset
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])
