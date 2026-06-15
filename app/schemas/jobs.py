from datetime import datetime

from app.schemas.base import ORMBase


class PipelineLogRead(ORMBase):
    id: int
    job_id: int | None = None
    source_id: int | None = None
    log_level: str | None = None
    message: str
    error_type: str | None = None
    error_details: str | None = None
    created_at: datetime | None = None


class PipelineJobRead(ORMBase):
    id: int
    job_type: str
    source_id: int | None = None
    session_id: int | None = None
    status: str
    posts_found: int
    posts_new: int
    items_total: int
    items_updated: int
    items_failed: int
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PipelineJobDetail(PipelineJobRead):
    logs: list[PipelineLogRead] = []
