"""FastAPI app entrypoint with scheduler setup."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from src.api.routes import router
from src.data.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("Database initialized")

    from src.scheduler.jobs import job_biweekly_recalibration, job_daily_sync, job_weekly_plan

    # Daily sync: every day at 21:00 (after most evening runs are uploaded)
    scheduler.add_job(
        job_daily_sync,
        CronTrigger(hour=21, minute=0),
        id="daily_sync",
        replace_existing=True,
    )

    # Weekly plan: every Sunday at 19:00
    scheduler.add_job(
        job_weekly_plan,
        CronTrigger(day_of_week="sun", hour=19, minute=0),
        id="weekly_plan",
        replace_existing=True,
    )

    # Biweekly recalibration: every other Sunday at 19:30
    # APScheduler week='*/2' fires on even ISO weeks; adjust start_date to control parity
    scheduler.add_job(
        job_biweekly_recalibration,
        CronTrigger(day_of_week="sun", hour=19, minute=30, week="*/2"),
        id="biweekly_recalibration",
        replace_existing=True,
    )

    scheduler.start()
    log.info("Scheduler started (daily_sync, weekly_plan, biweekly_recalibration)")

    yield

    scheduler.shutdown()
    log.info("Scheduler stopped")


app = FastAPI(title="RunCoach", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/")
async def root() -> dict:
    return {"service": "RunCoach", "status": "running"}
