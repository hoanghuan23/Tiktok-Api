from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/tiktok.db"
    ms_token: str | None = None
    tiktok_headless: bool = False
    tiktok_browser: str = "chromium"
    tiktok_sleep_after: int = 3
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 60
    scheduler_source_batch_size: int = 15
    scheduler_post_batch_size: int = 30
    metric_num_workers: int = 1
    metric_max_retries: int = 3
    metric_retry_delay_seconds: int = 30
    metric_request_delay_seconds: float = 3
    metric_timeout_seconds: int = 8
    metric_impersonate: str = "chrome124"
    ytdlp_request_delay_seconds: float = 5
    ytdlp_extractor_retries: int = 3
    ytdlp_proxy_url: str | None = None
    ytdlp_cookie_file: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
