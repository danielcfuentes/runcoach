"""Telegram bot — handles inbound messages and outbound sends."""
from __future__ import annotations

import httpx

from src.config import settings

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


TELEGRAM_MAX_MESSAGE_CHARS = 4096


def _chunk_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> list[str]:
    """Split text into <=limit-char chunks on paragraph/line boundaries where possible.

    Telegram hard-rejects any sendMessage over 4096 chars (400 Bad Request) —
    long coach messages (weekly plans especially) can exceed that.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Prefer to split on a paragraph break, then a line break, within the limit.
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def send_message(text: str, chat_id: str | None = None, parse_mode: str = "Markdown") -> dict:
    cid = chat_id or settings.telegram_chat_id
    chunks = _chunk_message(text)

    last_resp: dict = {}
    async with httpx.AsyncClient() as client:
        for i, chunk in enumerate(chunks):
            # Number continuation parts so a multi-message plan reads clearly in Telegram.
            body = chunk if len(chunks) == 1 else f"{chunk}\n\n_({i + 1}/{len(chunks)})_"
            resp = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": cid, "text": body, "parse_mode": parse_mode},
                timeout=30,
            )
            resp.raise_for_status()
            last_resp = resp.json()
    return last_resp


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
