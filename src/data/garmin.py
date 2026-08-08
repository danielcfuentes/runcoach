"""Garmin Connect sync via python-garminconnect — RHR, HRV, daily stats."""
import asyncio
from datetime import date, timedelta
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.config import settings
from src.data.models import DailyMetric


def _sync_fetch_garmin(start_date: date, end_date: date) -> list[dict]:
    """Blocking garminconnect calls — run in thread pool."""
    try:
        from garminconnect import Garmin
    except ImportError:
        return []

    client = Garmin(settings.garmin_email, settings.garmin_password)
    client.login()

    results = []
    current = start_date
    while current <= end_date:
        day_str = current.strftime("%Y-%m-%d")
        try:
            stats = client.get_stats(day_str)
            hrv_data = None
            try:
                hrv_resp = client.get_hrv_data(day_str)
                if hrv_resp and isinstance(hrv_resp, dict):
                    hrv_data = hrv_resp.get("hrvSummary", {}).get("lastNight")
            except Exception:
                pass

            results.append({
                "date": current,
                "resting_hr": stats.get("restingHeartRate"),
                "hrv_ms": hrv_data,
                "sleep_hours": (stats.get("sleepingSeconds") or 0) / 3600 or None,
            })
        except Exception:
            results.append({"date": current, "resting_hr": None, "hrv_ms": None, "sleep_hours": None})

        current += timedelta(days=1)

    return results


async def sync_daily_metrics(db: AsyncSession, days_back: int = 7) -> int:
    end = date.today()
    start = end - timedelta(days=days_back - 1)

    loop = asyncio.get_event_loop()
    garmin_rows = await loop.run_in_executor(None, partial(_sync_fetch_garmin, start, end))

    updated = 0
    for row in garmin_rows:
        from datetime import datetime, timezone
        day_dt = datetime.combine(row["date"], datetime.min.time()).replace(tzinfo=timezone.utc)

        result = await db.execute(
            select(DailyMetric).where(DailyMetric.date == day_dt)
        )
        metric = result.scalar_one_or_none()

        if metric is None:
            metric = DailyMetric(date=day_dt)
            db.add(metric)

        if row["resting_hr"] is not None:
            metric.resting_hr = row["resting_hr"]
        if row["hrv_ms"] is not None:
            metric.hrv_ms = row["hrv_ms"]
        if row["sleep_hours"] is not None:
            metric.sleep_hours = row["sleep_hours"]

        updated += 1

    await db.flush()
    return updated
