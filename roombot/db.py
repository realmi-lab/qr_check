"""SQLite 저장소. UTC 정규화, 외래키, WAL, 쓰기 트랜잭션 직렬화."""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from roombot import clock
from roombot.config import settings

_path: Path | None = None
_ready: set[Path] = set()
_init_lock = threading.Lock()


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("시간대가 포함된 시각이 필요합니다.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


sqlite3.register_adapter(datetime, stamp)
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_adapter(list, json.dumps)


def database_path() -> Path:
    return (_path or Path(settings.sqlite_path)).expanduser().resolve()


def _row(cursor, values) -> dict:
    row = dict(zip((c[0] for c in cursor.description), values))
    for key, value in row.items():
        if value is None:
            continue
        if key.endswith("_at") and isinstance(value, str):
            row[key] = datetime.fromisoformat(value).astimezone(clock.TZ)
        elif key in ("equipment", "attendee_ids"):
            row[key] = json.loads(value)
    return row


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else database_path()
    conn = sqlite3.connect(target, timeout=15, isolation_level=None)
    conn.row_factory = _row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    current = stamp(clock.now())
    conn.create_function("app_now", 0, lambda: current)
    conn.create_function("shift_time", 2, lambda value, seconds:
                         stamp(datetime.fromisoformat(value) + timedelta(seconds=seconds)))
    conn.create_function("room_label", 2, lambda name, floor:
                         name if name[:1].isdigit() else f"{name} ({floor}층)", deterministic=True)
    return conn


def initialize() -> Path:
    path = database_path()
    with _init_lock:
        if path in _ready and path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        conn = connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()["user_version"]
            if version not in (0, 1):
                raise RuntimeError(f"지원하지 않는 SQLite 스키마 버전: {version}")
            if version == 0:
                conn.executescript(Path(__file__).with_name("schema.sql").read_text())
        finally:
            conn.close()
        _ready.add(path)
    return path


def use_database(path: str | Path) -> None:
    """테스트·관리 명령용 저장소 선택."""
    global _path
    if "://" in str(path):
        raise ValueError("SQLite 파일 경로를 지정하세요.")
    _path = Path(path).expanduser().resolve()
    from roombot.rooms import clear_cache
    clear_cache()


@contextmanager
def tx(*, write: bool = False):
    """조회는 읽기 스냅샷, 쓰기는 BEGIN IMMEDIATE로 경쟁을 직렬화한다."""
    initialize()
    conn = connect()
    try:
        if not write:
            conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def backup(destination: str | Path) -> Path:
    """WAL 내용까지 포함하는 온라인 백업. 기존 파일은 덮어쓰지 않는다."""
    initialize()
    target = Path(destination).expanduser().resolve()
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    try:
        source = connect()
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            source.close()
            dest.close()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target
