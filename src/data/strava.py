"""Strava OAuth 2.0 sync — fetches activities and stores them."""
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.data.models import Activity, StravaToken

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

RUN_TYPES = {"Run", "TrailRun", "VirtualRun", "Treadmill"}


async def _get_valid_token(db: AsyncSession) -> str:
    result = await db.execute(select(StravaToken).limit(1))
    token_row = result.scalar_one_or_none()

    now = int(time.time())
    if token_row and token_row.expires_at > now + 300:
        return token_row.access_token

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": token_row.refresh_token if token_row else settings.strava_refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if token_row:
        token_row.access_token = data["access_token"]
        token_row.refresh_token = data["refresh_token"]
        token_row.expires_at = data["expires_at"]
    else:
        token_row = StravaToken(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )
        db.add(token_row)

    await db.flush()
    return token_row.access_token


async def sync_activities(db: AsyncSession, days_back: int = 7) -> list[dict]:
    token = await _get_valid_token(db)
    after_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())

    headers = {"Authorization": f"Bearer {token}"}
    fetched: list[dict] = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                headers=headers,
                params={"after": after_ts, "per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            fetched.extend(batch)
            page += 1

    runs = [a for a in fetched if a.get("type") in RUN_TYPES or a.get("sport_type") in RUN_TYPES]
    saved: list[dict] = []

    for raw in runs:
        # Dedup by strava_id
        result = await db.execute(
            select(Activity).where(Activity.strava_id == raw["id"]).limit(1)
        )
        if result.scalar_one_or_none():
            continue

        start = datetime.fromisoformat(raw["start_date"].replace("Z", "+00:00"))
        distance_m = raw.get("distance", 0)
        moving_secs = raw.get("moving_time", 0)
        pace_sec_per_km = (moving_secs / (distance_m / 1000)) if distance_m > 0 else None

        activity = Activity(
            strava_id=raw["id"],
            source="strava",
            external_id=str(raw["id"]),
            name=raw.get("name", ""),
            activity_type=raw.get("sport_type") or raw.get("type", "Run"),
            start_time=start,
            elapsed_seconds=raw.get("elapsed_time"),
            moving_seconds=moving_secs,
            distance_meters=distance_m,
            elevation_gain_meters=raw.get("total_elevation_gain"),
            average_pace_sec_per_km=pace_sec_per_km,
            average_hr=raw.get("average_heartrate"),
            max_hr=raw.get("max_heartrate"),
            average_cadence=raw.get("average_cadence"),
            average_watts=raw.get("average_watts") if raw.get("device_watts") else None,
            suffer_score=raw.get("suffer_score"),
            raw_data=raw,
        )
        db.add(activity)
        saved.append(raw)

    await db.flush()
    return saved
