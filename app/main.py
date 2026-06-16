from fastapi import FastAPI

from app import models
from app.database import engine
from app.routers import analytics, jobs, posts, sessions, sources


models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="TikTok Data API", version="0.1.0")

app.include_router(sources.router)
app.include_router(posts.router)
app.include_router(jobs.router)
app.include_router(analytics.router)
app.include_router(sessions.router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
