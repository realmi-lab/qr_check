"""현재 시각. APP_NOW가 있으면 그 시각에서 시작해 실제 시간만큼 흐르는 가상 시계."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from roombot.config import settings

TZ = ZoneInfo(settings.timezone)

_offset = timedelta(0)
_fixed: datetime | None = None


def _real_now() -> datetime:
    return datetime.now(TZ)


def start_virtual(at: datetime) -> None:
    """지금부터 at 시각인 것처럼 시간이 흐름."""
    global _offset, _fixed
    _fixed = None
    _offset = at.astimezone(TZ) - _real_now()


def freeze(at: datetime | None) -> None:
    """시각을 고정 (테스트용). None이면 고정 해제."""
    global _fixed
    _fixed = at.astimezone(TZ) if at else None


def now() -> datetime:
    if _fixed is not None:
        return _fixed
    return _real_now() + _offset


if settings.app_now:
    start_virtual(datetime.fromisoformat(settings.app_now))
