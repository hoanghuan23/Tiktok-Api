import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models
from app.core.config import get_settings
from app.core.logging_config import configure_application_logging
from app.database import engine
from app.routers import analytics, jobs, posts, sessions, sources
from app.services.scheduler_service import run_scheduler_forever


configure_application_logging()
logger = logging.getLogger("tiktok_api.app")

models.Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "Ung dung TikTok Data API da khoi dong | scheduler_enabled=%s interval_seconds=%s",
        settings.scheduler_enabled,
        settings.scheduler_interval_seconds,
    )
    if settings.scheduler_enabled:
        app.state.scheduler_task = asyncio.create_task(run_scheduler_forever())
    yield
    task = getattr(app.state, "scheduler_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Ung dung TikTok Data API da dung")


app = FastAPI(title="TikTok Data API", version="0.1.0", lifespan=lifespan)

app.include_router(sources.router)
app.include_router(posts.router)
app.include_router(jobs.router)
app.include_router(analytics.router)
app.include_router(sessions.router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
