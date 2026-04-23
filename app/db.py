"""SQLAlchemy engine and metadata.

SQLite is tuned for our workload with WAL journaling, NORMAL sync, and enforced
foreign keys. WAL lets readers and the single writer coexist without blocking,
which matters when the web process and the in-process scheduler both touch the DB.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, MetaData, create_engine, event
from sqlalchemy.engine import Engine

from app.config import get_settings

metadata = MetaData()

_engine: Engine | None = None


def _apply_sqlite_pragmas(dbapi_conn, _conn_record) -> None:
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
    finally:
        cur.close()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.db_absolute_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            settings.db_url,
            future=True,
            connect_args={"check_same_thread": False},
        )
        event.listen(_engine, "connect", _apply_sqlite_pragmas)
    return _engine


@contextmanager
def connection() -> Iterator[Connection]:
    """Short-lived connection with auto-commit on success, rollback on exception."""
    with get_engine().begin() as conn:
        yield conn
