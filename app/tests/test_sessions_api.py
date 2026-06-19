from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import TikTokSession
from app.routers.sessions import create_session, update_session
from app.schemas.sessions import TikTokSessionCreate, TikTokSessionUpdate


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_create_session_stores_cookie_tokens():
    db = _session()

    session = create_session(
        TikTokSessionCreate(
            sessionid="sessionid-1234567890",
            tt_csrf_token="csrf-token-1234567890",
            ms_token="ms-token-1234567890",
        ),
        db,
    )

    saved_session = db.get(TikTokSession, session.id)
    assert saved_session.sessionid == "sessionid-1234567890"
    assert saved_session.tt_csrf_token == "csrf-token-1234567890"
    assert saved_session.ms_token == "ms-token-1234567890"
    assert saved_session.masked_sessionid == "sessio...567890"
    assert saved_session.masked_tt_csrf_token == "csrf-t...567890"
    assert saved_session.masked_ms_token == "ms-tok...567890"


def test_update_session_can_rotate_cookie_tokens():
    db = _session()
    session = TikTokSession(
        sessionid="old-sessionid",
        tt_csrf_token="old-csrf-token",
        ms_token="old-ms-token",
        is_active=True,
        is_valid=True,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    updated_session = update_session(
        session.id,
        TikTokSessionUpdate(
            sessionid="new-sessionid-1234567890",
            tt_csrf_token="new-csrf-token-1234567890",
            ms_token="new-ms-token-1234567890",
        ),
        db,
    )

    assert updated_session.sessionid == "new-sessionid-1234567890"
    assert updated_session.tt_csrf_token == "new-csrf-token-1234567890"
    assert updated_session.ms_token == "new-ms-token-1234567890"
