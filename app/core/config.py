from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/tiktok.db"
    ms_token: str | None = None
    tiktok_headless: bool = True
    tiktok_browser: str = "chromium"
    tiktok_sleep_after: int = 3
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 200
    scheduler_source_batch_size: int = 20
    scheduler_post_batch_size: int = 50

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
