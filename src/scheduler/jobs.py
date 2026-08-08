"""APScheduler jobs — daily sync, injury check, weekly plan, biweekly recalibration."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from src.bot.telegram_bot import send_message
from src.coach.claude_client import (
    generate_daily_checkin,
    generate_injury_alert,
    generate_recalibration_update,
    generate_weekly_plan,
    predict_finish_time,
)
from src.coach.prompts import build_context_block, training_phase, weeks_to_race
from src.data.database import async_session_factory
from src.data.garmin import sync_daily_metrics
from src.data.strava import sync_activities
from src.metrics.calculator import (
    check_race_pace_trigger,
    compute_recent_paces,
    compute_rhr_status,
    compute_workload,
    persist_daily_metrics,
)
from src.metrics.injury_risk import RiskAssessment, assess_risk, save_alert
from src.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Failure notification wrapper
# ---------------------------------------------------------------------------

def notify_on_failure(job_name: str):
    """Decorator: sends a Telegram alert if the job raises."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                log.exception(f"{job_name} failed")
                try:
                    await send_message(
                        f"⚠️ *{job_name} failed*\n`{type(exc).__name__}: {str(exc)[:200]}`"
                    )
                except Exception:
                    pass
                raise
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

async def _load_recent_messages(db) -> list[dict]:
    from sqlalchemy import select
    from src.data.models import CoachMessage

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    result = await db.execute(
        select(CoachMessage)
        .where(CoachMessage.created_at >= cutoff)
        .order_by(CoachMessage.created_at)
        .limit(20)
    )
    return [{"role": m.role, "content": m.content} for m in result.scalars().all()]


async def _compute_finish_prediction(db) -> tuple[str | None, int | None]:
    """Returns (formatted_time | None, delta_seconds | None)."""
    paces = await compute_recent_paces(db)
    if not paces.long_run_pace_sec_per_km or not paces.tempo_pace_sec_per_km:
        return None, None

    snap = await compute_workload(db)
    pred_secs, formatted = predict_finish_time(
        paces.long_run_pace_sec_per_km,
        paces.tempo_pace_sec_per_km,
        snap.acute_miles,
    )
    delta = pred_secs - settings.goal_finish_seconds
    return formatted, delta


async def _build_context(db, assessment: RiskAssessment | None = None) -> tuple[str, dict]:
    snap = await compute_workload(db)
    rhr = await compute_rhr_status(db)
    wtr = weeks_to_race()
    phase = training_phase(wtr)
    race_pace_active = await check_race_pace_trigger(db)
    predicted_finish, predicted_delta = await _compute_finish_prediction(db)

    flags_summary = ""
    if assessment and assessment.flags:
        flags_summary = "\n".join(
            f"  - [{f.severity.upper()}] {f.name}: {f.description}" for f in assessment.flags
        )

    recent_messages = await _load_recent_messages(db)

    ctx = build_context_block(
        weeks_to_race=wtr,
        training_phase=phase,
        acwr=snap.acwr,
        tsb=snap.tsb,
        atl=snap.atl,
        ctl=snap.ctl,
        weekly_miles=snap.acute_miles,
        four_week_avg=snap.chronic_miles,
        rhr_baseline=rhr.baseline_bpm if rhr else None,
        recent_rhr=rhr.recent_avg_bpm if rhr else None,
        flags_summary=flags_summary,
        recent_messages=recent_messages,
        race_pace_trigger_active=race_pace_active,
        predicted_finish=predicted_finish,
        predicted_finish_delta_secs=predicted_delta,
    )
    return ctx, {
        "snap": snap, "phase": phase, "wtr": wtr,
        "race_pace_active": race_pace_active,
        "predicted_finish": predicted_finish,
        "predicted_delta": predicted_delta,
    }


async def _save_coach_message(db, content: str, message_type: str, tg_response: dict | None = None) -> None:
    from src.data.models import CoachMessage
    msg = CoachMessage(
        role="coach",
        message_type=message_type,
        content=content,
        telegram_message_id=tg_response.get("result", {}).get("message_id") if tg_response else None,
    )
    db.add(msg)
    await db.flush()


async def _save_athlete_message(db, content: str, tg_message_id: int | None = None) -> None:
    from src.data.models import CoachMessage
    msg = CoachMessage(
        role="athlete",
        message_type="chat",
        content=content,
        telegram_message_id=tg_message_id,
    )
    db.add(msg)
    await db.flush()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@notify_on_failure("daily_sync")
async def job_daily_sync() -> None:
    """Runs daily — syncs Strava + Garmin, recomputes metrics, checks injury risk."""
    log.info("Starting daily sync job")
    async with async_session_factory() as db:
        # 1. Sync data sources
        new_strava = await sync_activities(db, days_back=2)
        log.info(f"Strava: {len(new_strava)} new activities")

        await sync_daily_metrics(db, days_back=2)
        log.info("Garmin daily metrics synced")

        # 2. Recompute today's metrics
        await persist_daily_metrics(db)

        # Commit the data sync now, independent of steps below. Steps 3-5 call
        # out to Claude/Telegram, which can fail (rate limits, billing, network) —
        # that must never roll back activities/metrics that already synced cleanly.
        await db.commit()
        log.info("Data sync committed (activities + daily metrics)")

        # 3. Assess injury risk
        assessment = await assess_risk(db)
        log.info(f"Risk assessment: {assessment.overall_severity}, {len(assessment.flags)} flags")

        # 4. Immediate alert if high risk or 2+ elevated
        if assessment.should_send_immediate_alert():
            ctx, meta = await _build_context(db, assessment)
            flags_text = "\n".join(f"• {f.description}" for f in assessment.flags)
            alert_text = generate_injury_alert(ctx, flags_text)

            formatted = f"🚨 *INJURY RISK ALERT*\n\n{alert_text}"
            tg_resp = await send_message(formatted)
            alert = await save_alert(db, assessment, alert_text)
            alert.sent_via_telegram = True
            await _save_coach_message(db, formatted, "alert", tg_resp)
            log.warning(f"Sent injury alert: {assessment.overall_severity}")

        # 5. Daily check-in if new activities
        if new_strava:
            latest = new_strava[-1]
            dist_km = (latest.get("distance") or 0) / 1000
            pace_sec = latest.get("moving_time", 0) / dist_km if dist_km > 0 else 0
            pace_min, pace_s = int(pace_sec // 60), int(pace_sec % 60)
            cadence = latest.get("average_cadence")
            cadence_str = f"{cadence:.0f} spm" if cadence else "N/A"

            activity_summary = (
                f"Name: {latest.get('name', 'Run')}\n"
                f"Distance: {dist_km:.2f}km\n"
                f"Pace: {pace_min}:{pace_s:02d}/km\n"
                f"HR: {latest.get('average_heartrate', 'N/A')} bpm avg\n"
                f"Cadence: {cadence_str}\n"
                f"Moving time: {latest.get('moving_time', 0) // 60}min\n"
            )
            ctx, _ = await _build_context(db)
            checkin = generate_daily_checkin(ctx, activity_summary)
            tg_resp = await send_message(checkin)
            await _save_coach_message(db, checkin, "daily_checkin", tg_resp)

        await db.commit()
        log.info("Daily sync job complete")


@notify_on_failure("weekly_plan")
async def job_weekly_plan() -> None:
    """Runs Sunday evening — generates and sends next week's plan."""
    log.info("Starting weekly plan job")
    async with async_session_factory() as db:
        assessment = await assess_risk(db)
        ctx, meta = await _build_context(db, assessment)
        plan_text = generate_weekly_plan(ctx)

        from src.data.models import WeeklyPlan

        now = datetime.now(timezone.utc)
        days_since_monday = now.weekday()
        next_monday = (now - timedelta(days=days_since_monday)) + timedelta(days=7)

        plan = WeeklyPlan(
            week_start=next_monday,
            weeks_to_race=meta["wtr"],
            training_phase=meta["phase"],
            acwr_at_generation=meta["snap"].acwr,
            tsb_at_generation=meta["snap"].tsb,
            predicted_finish_seconds=(
                meta["snap"].acwr  # placeholder — actual value computed below
            ),
            narrative=plan_text,
            sent_at=now,
        )
        # Store actual predicted finish if available
        if meta["predicted_finish"] and meta["predicted_delta"] is not None:
            from src.config import settings as s
            pred_secs = s.goal_finish_seconds + meta["predicted_delta"]
            plan.predicted_finish_seconds = pred_secs

        db.add(plan)

        header = f"📅 *Weekly Training Plan — Week of {next_monday.strftime('%B %d')}*\n\n"
        full_message = header + plan_text
        tg_resp = await send_message(full_message)
        await _save_coach_message(db, full_message, "weekly_plan", tg_resp)

        await db.commit()
        log.info("Weekly plan sent")


@notify_on_failure("biweekly_recalibration")
async def job_biweekly_recalibration() -> None:
    """Runs every 2 weeks — recalibrates pace zones and reports CIM trajectory."""
    log.info("Starting biweekly recalibration job")
    async with async_session_factory() as db:
        from src.metrics.calculator import SEC_PER_KM_TO_SEC_PER_MI, _sec_per_km_to_pace_str

        paces = await compute_recent_paces(db)
        ctx, meta = await _build_context(db)

        tempo_pace_str = _sec_per_km_to_pace_str(paces.tempo_pace_sec_per_km) if paces.tempo_pace_sec_per_km else None
        long_pace_str = _sec_per_km_to_pace_str(paces.long_run_pace_sec_per_km) if paces.long_run_pace_sec_per_km else None

        narrative = generate_recalibration_update(
            ctx,
            actual_tempo_pace_str=tempo_pace_str,
            actual_long_pace_str=long_pace_str,
            predicted_finish=meta["predicted_finish"],
            predicted_delta_secs=meta["predicted_delta"],
        )

        # Build the Telegram message with raw numbers + Claude narrative
        lines = ["📊 *Biweekly Pace Zone Update*\n"]
        if tempo_pace_str:
            lines.append(f"Actual tempo pace (last 28 days): *{tempo_pace_str}*")
        if long_pace_str:
            lines.append(f"Actual long run pace (last 28 days): *{long_pace_str}*")
        if meta["predicted_finish"] and meta["predicted_delta"] is not None:
            delta = meta["predicted_delta"]
            sign = "+" if delta > 0 else "-"
            dm, ds = abs(delta) // 60, abs(delta) % 60
            direction = "behind" if delta > 0 else "ahead of"
            lines.append(f"CIM prediction: *{meta['predicted_finish']}* ({sign}{dm}:{ds:02d} {direction} goal)\n")
        lines.append(narrative)

        full_message = "\n".join(lines)
        tg_resp = await send_message(full_message)
        await _save_coach_message(db, full_message, "recalibration", tg_resp)

        await db.commit()
        log.info("Biweekly recalibration sent")


async def handle_incoming_message(text: str, tg_message_id: int | None = None) -> str:
    """Process an inbound Telegram message from Daniel and return Claude's response."""
    async with async_session_factory() as db:
        try:
            await _save_athlete_message(db, text, tg_message_id)
            assessment = await assess_risk(db)
            ctx, _ = await _build_context(db, assessment)
            history = await _load_recent_messages(db)

            from src.coach.claude_client import handle_chat
            response = handle_chat(ctx, text, history)

            tg_resp = await send_message(response)
            await _save_coach_message(db, response, "chat", tg_resp)
            await db.commit()
            return response
        except Exception:
            log.exception("Failed to handle incoming message")
            await db.rollback()
            raise
