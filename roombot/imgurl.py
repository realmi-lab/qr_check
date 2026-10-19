"""슬랙에 넣을 그림 주소 (서명 + 만료). 서명이 맞는 주소만 그림을 돌려줘서, 주소를 모르는 사람은 일정을 볼 수 없음."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

from roombot import clock
from roombot.config import settings

TTL = 7 * 86400   # 7일


def _sig(kind: str, params: dict) -> str:
    msg = kind + "?" + urlencode(sorted((k, str(v)) for k, v in params.items() if k != "s"))
    return hmac.new(settings.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def url(kind: str, **params) -> str | None:
    """슬랙이 가져갈 수 있는 https 주소일 때만 만듦 (localhost면 None → 그림 없이 글자만)."""
    if not settings.public_base_url.startswith("https://"):
        return None
    params = {k: v for k, v in params.items() if v is not None}
    params["v"] = int(clock.now().timestamp() // 300)   # 5분마다 새 그림 (슬랙 캐시 갱신)
    params["e"] = int(time.time()) + TTL
    params["s"] = _sig(kind, params)
    return f"{settings.public_base_url}/img/{kind}.png?{urlencode(params)}"


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
    """그림 블록의 출처. 슬랙에 올릴 수 있으면 slack_file(터널 주소가 바뀌어도 안 깨짐), 아니면 서명된 주소."""
    from roombot import slackfiles
    params = {k: v for k, v in params.items() if v is not None}
    if slackfiles.can_upload():
        key = (kind, tuple(sorted((k, str(v)) for k, v in params.items())), int(clock.now().timestamp() // 300))
        fid = slackfiles.cached(key)
        if not fid and slackfiles.quick():
            # 기다릴 수 없음 → 뒤에서 올려 두고 이번엔 주소 그림
            import threading
            threading.Thread(target=lambda: (png := render(kind, params)) and slackfiles.upload(key, png, alt or kind),
                             daemon=True).start()
        elif not fid:
            png = render(kind, params)
            fid = png and slackfiles.upload(key, png, alt or kind)
        if fid:
            return {"slack_file": {"id": fid}}
    u = url(kind, **params)
    return {"image_url": u} if u else None


def verify(kind: str, params: dict) -> bool:
    try:
        if int(params.get("e", 0)) < time.time():
            return False
    except ValueError:
        return False
    return hmac.compare_digest(_sig(kind, params), params.get("s", ""))
