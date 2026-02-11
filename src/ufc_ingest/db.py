"""Database engine and session helpers."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import Engine
from .config import cfg

_engine: Engine = None
_Session = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(cfg.database_url, future=True)
    return _engine


def get_session():
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _Session()
