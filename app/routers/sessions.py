from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TikTokSession
from app.schemas.sessions import TikTokSessionCreate, TikTokSessionRead, TikTokSessionUpdate


router = APIRouter(prefix="/sessions", tags=["sessions"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@router.post("", response_model=TikTokSessionRead, status_code=status.HTTP_201_CREATED)
def create_session(payload: TikTokSessionCreate, db: Session = Depends(get_db)) -> TikTokSession:
    if payload.deactivate_existing and payload.is_active:
        db.query(TikTokSession).filter(TikTokSession.is_active.is_(True)).update(
            {TikTokSession.is_active: False},
            synchronize_session=False,
        )

    session = TikTokSession(
        sessionid=payload.sessionid,
        tt_csrf_token=payload.tt_csrf_token,
        ms_token=payload.ms_token,
        is_active=payload.is_active,
        is_valid=payload.is_valid,
        expires_at=_naive_utc(payload.expires_at) or (_now() + timedelta(days=30)),
        created_at=_now(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("", response_model=list[TikTokSessionRead])
def list_sessions(
    is_active: bool | None = Query(default=None),
    is_valid: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[TikTokSession]:
    query = db.query(TikTokSession)
    if is_active is not None:
        query = query.filter(TikTokSession.is_active.is_(is_active))
    if is_valid is not None:
        query = query.filter(TikTokSession.is_valid.is_(is_valid))
    return query.order_by(TikTokSession.id.desc()).all()


@router.get("/{session_id}", response_model=TikTokSessionRead)
def get_session(session_id: int, db: Session = Depends(get_db)) -> TikTokSession:
    session = db.get(TikTokSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/{session_id}", response_model=TikTokSessionRead)
def update_session(
    session_id: int,
    payload: TikTokSessionUpdate,
    db: Session = Depends(get_db),
) -> TikTokSession:
    session = db.get(TikTokSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "expires_at" in update_data:
        update_data["expires_at"] = _naive_utc(update_data["expires_at"])
    for field, value in update_data.items():
        setattr(session, field, value)

    db.commit()
    db.refresh(session)
    return session
