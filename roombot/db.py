"""DB 연결. 트랜잭션마다 app.now를 현재(가상) 시각으로 맞춰서 SQL 뷰도 같은 시계를 쓰게 함."""

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from roombot import clock
from roombot.config import settings

_pool: ConnectionPool | None = None


def _configure(conn: psycopg.Connection) -> None:
    conn.execute("SELECT set_config('TimeZone', %s, false)", (settings.timezone,))
    conn.commit()


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            configure=_configure,
            check=ConnectionPool.check_connection,   # DB가 재시작돼 끊긴 연결은 버리고 새로 연결
            open=True,
        )
    return _pool


def use_database(url: str) -> None:
    """다른 DB로 전환 (테스트용)."""
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = ConnectionPool(url, min_size=1, max_size=4, kwargs={"row_factory": dict_row},
                           configure=_configure, check=ConnectionPool.check_connection, open=True)


@contextmanager
def tx() -> Iterator[psycopg.Connection]:
    """트랜잭션 하나. 끝나면 커밋, 예외가 나면 롤백."""
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.now', %s, true)", (clock.now().isoformat(),))
            yield conn
