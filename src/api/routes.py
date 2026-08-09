"""FastAPI routes — Telegram webhook, Strava OAuth callback, admin endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from src.config import settings

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------------------------

class TelegramUpdate(BaseModel):
    update_id: int
    message: dict | None = None
    callback_query: dict | None = None


@router.post("/webhook/telegram")
async def telegram_webhook(update: TelegramUpdate, bg: BackgroundTasks) -> dict:
    if update.message:
        msg = update.message
        chat_id = str(msg.get("chat", {}).get("id", ""))
        # Only respond to our own chat
        if chat_id != settings.telegram_chat_id and settings.telegram_chat_id:
            return {"ok": True}

        text = msg.get("text", "").strip()
        tg_id = msg.get("message_id")

        if text:
            bg.add_task(_process_message, text, tg_id)

    return {"ok": True}


async def _process_message(text: str, tg_message_id: int) -> None:
    from src.scheduler.jobs import handle_incoming_message
    try:
        await handle_incoming_message(text, tg_message_id)
    except Exception:
        log.exception("Error processing Telegram message")
        from src.bot.telegram_bot import send_message
        await send_message("Sorry, I hit an error processing that. Try again in a moment.")


# ---------------------------------------------------------------------------
# Strava OAuth callback
# ---------------------------------------------------------------------------

@router.get("/auth/strava/callback")
async def strava_oauth_callback(code: str, request: Request) -> dict:
    """Exchange authorization code for tokens and store them."""
    import httpx
    from src.data.database import async_session_factory
    from src.data.models import StravaToken

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    async with async_session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(select(StravaToken).limit(1))
        token_row = result.scalar_one_or_none()

        if token_row:
            token_row.access_token = data["access_token"]
            token_row.refresh_token = data["refresh_token"]
            token_row.expires_at = data["expires_at"]
        else:
            db.add(StravaToken(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=data["expires_at"],
            ))
        await db.commit()

    return {"status": "ok", "athlete": data.get("athlete", {}).get("username")}


# ---------------------------------------------------------------------------
# Admin / status endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def status() -> dict:
    from datetime import date
    from src.data.database import async_session_factory
    from src.metrics.calculator import compute_calendar_week_mileage, compute_workload
    from src.metrics.injury_risk import assess_risk
    from src.coach.prompts import weeks_to_race, training_phase

    async with async_session_factory() as db:
        snap = await compute_workload(db)
        cal_week = await compute_calendar_week_mileage(db)
        assessment = await assess_risk(db)
        wtr = weeks_to_race()
        phase = training_phase(wtr)

    return {
        "weeks_to_race": wtr,
        "training_phase": phase,
        "acwr": snap.acwr,
        "tsb": snap.tsb,
        "atl": snap.atl,
        "ctl": snap.ctl,
        "weekly_miles": snap.acute_miles,  # rolling 7-day, used for ACWR
        "four_week_miles": snap.chronic_miles,
        "calendar_week_miles": cal_week.this_week_miles,  # Mon-Sun, matches Strava
        "calendar_week_last_miles": cal_week.last_week_miles,
        "risk_severity": assessment.overall_severity,
        "active_flags": [f.name for f in assessment.flags],
    }


@router.post("/admin/sync-now")
async def trigger_sync(bg: BackgroundTasks) -> dict:
    from src.scheduler.jobs import job_daily_sync
    bg.add_task(job_daily_sync)
    return {"status": "sync started"}


@router.post("/admin/weekly-plan-now")
async def trigger_weekly_plan(bg: BackgroundTasks) -> dict:
    from src.scheduler.jobs import job_weekly_plan
    bg.add_task(job_weekly_plan)
    return {"status": "weekly plan generation started"}


@router.get("/admin/strava-auth-url")
async def strava_auth_url() -> dict:
    redirect_uri = f"{settings.webhook_url.rstrip('/')}/auth/strava/callback"
    url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={settings.strava_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=activity:read_all"
    )
    return {"auth_url": url}
