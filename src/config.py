from datetime import date
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://runcoach:runcoach@localhost:5432/runcoach"

    # Strava
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_refresh_token: str = ""

    # Garmin
    garmin_email: str = ""
    garmin_password: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    webhook_url: str = ""

    # Race goal
    race_date: date = date(2026, 12, 6)
    goal_finish_seconds: int = 10800  # 3:00:00

    # Training paces (seconds per mile)
    pace_easy_min: int = 470   # 7:50/mi
    pace_easy_max: int = 500   # 8:20/mi
    pace_long_min: int = 450   # 7:30/mi
    pace_long_max: int = 470   # 7:50/mi
    pace_tempo_min: int = 390  # 6:30/mi
    pace_tempo_max: int = 400  # 6:40/mi
    pace_interval_min: int = 360  # 6:00/mi
    pace_interval_max: int = 375  # 6:15/mi
    pace_goal_marathon: int = 412  # 6:52/mi

    # Injury thresholds
    acwr_elevated: float = 1.3
    acwr_high_risk: float = 1.5
    rhr_drift_bpm: int = 5
    rhr_drift_days: int = 2
    tsb_negative_days: int = 10
    weekly_volume_spike_caution: float = 0.20
    weekly_volume_spike_high: float = 0.30
    consistency_gap_days: int = 4


settings = Settings()
