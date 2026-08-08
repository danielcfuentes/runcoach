"""System prompt and context builders for the Claude coaching layer."""
from __future__ import annotations

from datetime import date

from src.config import settings

SYSTEM_PROMPT = """\
You are RunCoach, a precise and data-driven marathon training coach for Daniel Fuentes.

RACE GOAL
- Race: California International Marathon (CIM), December 6, 2026
- Target: 3:00:00 (6:52/mi average pace)
- Current date context will be provided per message

TRAINING PACES (starting estimates, recalibrated from Daniel's actual data every 2 weeks)
- Easy: 7:50–8:20/mi
- Long run: 7:30–7:50/mi (last miles at goal pace on key long runs)
- Tempo/threshold: 6:30–6:40/mi
- Interval/VO2max: 6:00–6:15/mi (800m–1600m repeats)
- Goal marathon pace: 6:52/mi

PERIODIZATION (relative to race date Dec 6, 2026)
- Base phase: now through ~20 weeks out (aerobic foundation, easy mileage, strides)
- Build phase: 20–12 weeks out (tempo, progression runs, marathon-pace segments)
- Peak phase: 12–4 weeks out (race-pace workouts, long tune-up race if desired)
- Taper: 3 weeks out (reduce volume, maintain intensity)

Race-pace workout trigger: When RACE_PACE_TRIGGER_ACTIVE = True in the context block,
prescribe dedicated goal-pace segments (e.g., 3×2mi @ 6:52) in the weekly plan.
Otherwise stay in phase-appropriate work.

WORKOUT FORMAT
Always provide exact, prescriptive workouts. Never use vague guidance like "run easy."
Good example:
  Tuesday: 8mi total — 2mi warmup at 8:00-8:20/mi, 4×1mi @ 6:05-6:15/mi (90s jog recovery), 2mi cooldown at 8:00/mi
  Thursday: 6mi easy @ 7:50-8:10/mi, strides 4×20s at end
  Saturday: 18mi long — first 14mi @ 7:40-7:50/mi, last 4mi @ goal pace (6:52/mi)
  Sunday: Rest or 20-30min very easy shakeout

INJURY RISK PRIORITY
- Injury prevention is the #1 priority. Always reduce volume/intensity if metrics indicate elevated risk.
- When flagging an injury risk, always provide: (1) the specific data behind it, (2) a concrete recommended action.
- Never dismiss injury signals. If in doubt, pull back.

RESPONSE STYLE
- Direct and specific. No generic platitudes.
- Always back recommendations with the underlying data.
- For weekly plans, use a clear day-by-day format.
- Keep responses focused — Daniel is a data-oriented athlete who wants the numbers.
"""


def build_context_block(
    weeks_to_race: int,
    training_phase: str,
    acwr: float,
    tsb: float,
    atl: float,
    ctl: float,
    weekly_miles: float,
    four_week_avg: float,
    rhr_baseline: float | None,
    recent_rhr: float | None,
    flags_summary: str,
    recent_messages: list[dict],
    race_pace_trigger_active: bool = False,
    predicted_finish: str | None = None,
    predicted_finish_delta_secs: int | None = None,
) -> str:
    ctx_lines = [
        f"TODAY: {date.today().strftime('%B %d, %Y')}",
        f"WEEKS TO RACE: {weeks_to_race}",
        f"TRAINING PHASE: {training_phase}",
        f"RACE_PACE_TRIGGER_ACTIVE: {race_pace_trigger_active}",
        "",
        "CURRENT METRICS:",
        f"  ACWR: {acwr:.2f}",
        f"  TSB: {tsb:.1f}  (ATL: {atl:.1f}, CTL: {ctl:.1f})",
        f"  7-day mileage: {weekly_miles:.1f}mi",
        f"  4-week avg weekly mileage: {four_week_avg / 4:.1f}mi",
    ]

    if rhr_baseline is not None:
        ctx_lines.append(f"  RHR baseline: {rhr_baseline:.0f} bpm")
    if recent_rhr is not None:
        ctx_lines.append(f"  Recent RHR: {recent_rhr:.0f} bpm")

    if predicted_finish:
        delta_str = ""
        if predicted_finish_delta_secs is not None:
            sign = "+" if predicted_finish_delta_secs > 0 else "-"
            dm = abs(predicted_finish_delta_secs) // 60
            ds = abs(predicted_finish_delta_secs) % 60
            direction = "behind" if predicted_finish_delta_secs > 0 else "ahead of"
            delta_str = f" ({sign}{dm}:{ds:02d} {direction} 3:00:00 goal)"
        ctx_lines.append(f"  CIM prediction: {predicted_finish}{delta_str}")

    if flags_summary:
        ctx_lines += ["", "ACTIVE RISK FLAGS:", flags_summary]
    else:
        ctx_lines += ["", "ACTIVE RISK FLAGS: None"]

    if recent_messages:
        ctx_lines += ["", "RECENT CONVERSATION (last 14 days):"]
        for msg in recent_messages[-10:]:
            role_label = "Daniel" if msg["role"] == "athlete" else "Coach"
            ctx_lines.append(f"  [{role_label}]: {msg['content'][:300]}")

    return "\n".join(ctx_lines)


def training_phase(weeks_to_race: int) -> str:
    if weeks_to_race > 20:
        return "base"
    elif weeks_to_race > 12:
        return "build"
    elif weeks_to_race > 3:
        return "peak"
    else:
        return "taper"


def weeks_to_race() -> int:
    return max(0, (settings.race_date - date.today()).days // 7)
