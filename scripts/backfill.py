#!/usr/bin/env python3
"""Backfill historical data — pull last N days from Strava and Garmin."""
import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90

    from src.data.database import init_db, async_session_factory
    from src.data.strava import sync_activities
    from src.data.garmin import sync_daily_metrics
    from src.metrics.calculator import persist_daily_metrics

    await init_db()

    print(f"Backfilling last {days} days...")

    async with async_session_factory() as db:
        activities = await sync_activities(db, days_back=days)
        print(f"Strava: {len(activities)} new activities")

        garmin_count = await sync_daily_metrics(db, days_back=days)
        print(f"Garmin: {garmin_count} days updated")

        await persist_daily_metrics(db)
        print("Daily metrics computed")

        await db.commit()

    print("Backfill complete.")


asyncio.run(main())
