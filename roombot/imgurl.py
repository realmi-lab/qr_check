"""슬랙에 넣을 그림 주소 (서명 + 만료). 서명이 맞는 주소만 그림을 돌려줘서, 주소를 모르는 사람은 일정을 볼 수 없음."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

from roombot import clock
from roombot.config import settings

TTL = 7 * 86400   # 7일
IMAGE_VERSION = "mobile-v3"   # 표시 변경 후에는 이전 이미지 캐시를 재사용하지 않음


def _sig(kind: str, params: dict) -> str:
    msg = kind + "?" + urlencode(sorted((k, str(v)) for k, v in params.items() if k != "s"))
    return hmac.new(settings.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def url(kind: str, **params) -> str | None:
    """슬랙이 가져갈 수 있는 https 주소일 때만 만듦 (localhost면 None → 그림 없이 글자만)."""
    base = settings.image_base_url or settings.public_base_url
    if not base.startswith("https://"):
        return None
    params = {k: v for k, v in params.items() if v is not None}
    params["rev"] = IMAGE_VERSION
    params["v"] = int(clock.now().timestamp() // 300)   # 5분마다 새 그림 (슬랙 캐시 갱신)
    params["e"] = int(time.time()) + TTL
    params["s"] = _sig(kind, params)
    return f"{base}/img/{kind}.png?{urlencode(params)}"


def viewer(kind: str, **params) -> str | None:
    source = url(kind, **params)
    return source.replace(f"/img/{kind}.png?", f"/view/{kind}?") if source else None


def render(kind: str, params: dict) -> bytes | None:
    """그림 만들기 (웹 주소로 줄 때와 슬랙에 올릴 때 같이 씀)."""
    from datetime import date

    from roombot import charts, rooms
    try:
        day = date.fromisoformat(str(params.get("d", "")))
    except ValueError:
        return None
    if kind == "room":
        found = [r for r in rooms.all_rooms() if r["code"] == params.get("r")]
        return charts.room_day_png(found[0], day) if found else None
    if kind == "board":
        f = str(params.get("f") or "")
        return charts.board_png(day, only_floor=int(f) if f.isdigit() else None)
    return None


def src(kind: str, alt: str = "", **params) -> dict | None:
    """뷰어 권한과 무관하게 가져올 수 있는 서명된 HTTPS 이미지 사용.

    채널에 공유하지 않은 봇 전용 slack_file은 사용자에게 깨져 보일 수 있어 쓰지 않는다.
    HTTPS 경로가 준비되지 않았으면 이미지 블록을 생략하고 본문을 제공한다.
    """
    source = url(kind, **params)
    return {"image_url": source} if source else None


def verify(kind: str, params: dict) -> bool:
    try:
        if int(params.get("e", 0)) < time.time():
            return False
    except ValueError:
        return False
    return hmac.compare_digest(_sig(kind, params), params.get("s", ""))
