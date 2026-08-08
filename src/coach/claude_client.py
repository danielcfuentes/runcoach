"""Claude API client — generates workout plans, injury alerts, chat responses."""
from __future__ import annotations

import anthropic

from src.config import settings
from src.coach.prompts import SYSTEM_PROMPT

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _call(messages: list[dict], max_tokens: int = 2048) -> str:
    response = get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return response.content[0].text


def generate_weekly_plan(context: str) -> str:
    prompt = (
        f"{context}\n\n"
        "Generate next week's specific training plan. Include:\n"
        "1. Day-by-day workouts with exact paces and distances\n"
        "2. Key focus for the week given current training phase\n"
        "3. Any adjustments based on current metrics (ACWR, TSB, flags)\n"
        "4. CIM finish time trajectory — current prediction vs. 3:00:00 goal and what closes the gap\n"
        "5. If RACE_PACE_TRIGGER_ACTIVE is True, include at least one race-pace segment workout\n"
        "6. One sentence on where we are in the CIM build\n\n"
        "Be prescriptive. Give exact workouts, not categories."
    )
    return _call([{"role": "user", "content": prompt}], max_tokens=1500)


def generate_injury_alert(context: str, flags_detail: str) -> str:
    prompt = (
        f"{context}\n\n"
        f"TRIGGERED FLAGS:\n{flags_detail}\n\n"
        "Write an immediate injury risk alert for Daniel. Include:\n"
        "1. What's flagged and why (the actual numbers)\n"
        "2. The specific risk this poses\n"
        "3. Exact recommended action for the next 48 hours (which runs to modify and how)\n"
        "4. What to watch to know it's resolving\n\n"
        "Be direct. No sugarcoating, but no panic either. Just the data and the action."
    )
    return _call([{"role": "user", "content": prompt}], max_tokens=600)


def generate_daily_checkin(context: str, activity_summary: str) -> str:
    prompt = (
        f"{context}\n\n"
        f"TODAY'S ACTIVITY:\n{activity_summary}\n\n"
        "Write a brief post-run check-in (3-5 sentences max). Cover:\n"
        "- Whether today's run looked on target\n"
        "- Any notable metrics (HR, pace vs. expected, cadence if relevant)\n"
        "- One forward-looking note if relevant\n\n"
        "Keep it concise — this is a daily text, not a full analysis."
    )
    return _call([{"role": "user", "content": prompt}], max_tokens=300)


def generate_recalibration_update(
    context: str,
    actual_tempo_pace_str: str | None,
    actual_long_pace_str: str | None,
    predicted_finish: str | None,
    predicted_delta_secs: int | None,
) -> str:
    details = []
    if actual_tempo_pace_str:
        details.append(f"Actual tempo pace: {actual_tempo_pace_str}")
    if actual_long_pace_str:
        details.append(f"Actual long run pace: {actual_long_pace_str}")
    if predicted_finish and predicted_delta_secs is not None:
        sign = "+" if predicted_delta_secs > 0 else "-"
        dm = abs(predicted_delta_secs) // 60
        ds = abs(predicted_delta_secs) % 60
        direction = "behind" if predicted_delta_secs > 0 else "ahead of"
        details.append(f"CIM prediction: {predicted_finish} ({sign}{dm}:{ds:02d} {direction} goal)")

    prompt = (
        f"{context}\n\n"
        "BIWEEKLY PACE ZONE RECALIBRATION\n"
        + "\n".join(details) + "\n\n"
        "Write a brief biweekly recalibration update (4-6 sentences). Cover:\n"
        "1. Whether the actual paces suggest the current training zones need adjustment\n"
        "2. What the finish time prediction says about trajectory toward 3:00:00\n"
        "3. The most important thing to focus on in the next 2 weeks given current phase\n\n"
        "Be specific with pace numbers. No fluff."
    )
    return _call([{"role": "user", "content": prompt}], max_tokens=400)


def handle_chat(context: str, user_message: str, history: list[dict]) -> str:
    messages = []
    for msg in history[-8:]:
        messages.append({
            "role": "user" if msg["role"] == "athlete" else "assistant",
            "content": msg["content"],
        })
    messages.append({"role": "user", "content": f"{context}\n\n{user_message}"})
    return _call(messages, max_tokens=800)


def predict_finish_time(
    recent_long_run_pace_sec_per_km: float,
    recent_tempo_pace_sec_per_km: float,
    weekly_miles: float,
) -> tuple[int, str]:
    """Returns (predicted_finish_seconds, formatted_time) using Riegel-based estimation."""
    MARATHON_KM = 42.195
    TEMPO_DIST_KM = 10.0
    LONG_RUN_KM = 30.0

    tempo_10k_secs = recent_tempo_pace_sec_per_km * TEMPO_DIST_KM
    predicted_from_tempo = tempo_10k_secs * (MARATHON_KM / TEMPO_DIST_KM) ** 1.06

    long_run_secs = recent_long_run_pace_sec_per_km * LONG_RUN_KM
    predicted_from_long = long_run_secs * (MARATHON_KM / LONG_RUN_KM) ** 1.06

    predicted = max(predicted_from_tempo, predicted_from_long)
    if weekly_miles >= 50:
        predicted *= 0.98
    elif weekly_miles >= 40:
        predicted *= 0.99

    predicted = int(predicted)
    hours = predicted // 3600
    minutes = (predicted % 3600) // 60
    seconds = predicted % 60

    return predicted, f"{hours}:{minutes:02d}:{seconds:02d}"
