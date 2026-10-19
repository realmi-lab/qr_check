"""회의실마다 슬랙 채널 + 체크인 패널.

QR = 그 회의실 채널을 여는 슬랙 링크 (https://<워크스페이스>.slack.com/archives/<채널>)
  → 휴대폰 카메라로 찍으면 슬랙 앱에서 채널이 열림 → [채널 참여] = 체크인 (슬랙이 누가 어느 채널에 들어왔는지 알려줌).
처음 온 사람은 [채널 참여] = 체크인, 이미 참여한 사람은 체크인 시간에만 보이는 패널의 [체크인] 버튼.
봇 DM·홈·명령어로는 체크인할 수 없음. 채널에서 내보내지 않음 (내보내면 슬랙이 '퇴장' 알림을 보냄).
채널 주소는 바뀌지 않으므로 QR은 한 번만 인쇄하면 됨. 도메인·터널·로그인 화면이 필요 없음.

패널 메시지는 지금/다음 회의가 바뀔 때만 고침. 무료 플랜은 90일 넘은 메시지가 안 보이므로 30일마다 새로 올림.
"""

import hashlib
import json
import logging
from datetime import timedelta
from urllib.parse import urlencode

from slack_sdk.errors import SlackApiError

from roombot import clock, rooms, style
from roombot.blocks import actions, button, context, header, section
from roombot.config import settings
from roombot.db import tx

log = logging.getLogger(__name__)
REPOST_AFTER = timedelta(days=30)
TOPIC = "{label} 체크인"
PURPOSE = "체크인하시겠습니까? [채널 참여]를 누르면 체크인돼요. 유효시간은 회의 시작 10분 후까지입니다."


def channel_name(room: dict) -> str:
    """회의실-9a, 회의실-bitcoin … (슬랙 채널 이름은 소문자·공백 없음)"""
    base = room["code"] if room["name"][0].isdigit() else room["name"]
    return f"회의실-{base.lower()}"


def mapping() -> dict[int, dict]:
    with tx() as conn:
        return {r["room_id"]: r for r in conn.execute("SELECT * FROM bot.room_channels").fetchall()}


_workspace_url: str | None = None


def workspace_url() -> str | None:
    """https://realmihq.slack.com (봇 토큰으로 한 번 물어보고 기억)."""
    global _workspace_url
    if _workspace_url is None and settings.slack_bot_token:
        try:
            from slack_sdk import WebClient
            _workspace_url = WebClient(settings.slack_bot_token, timeout=10).auth_test()["url"].rstrip("/")
        except Exception:
            log.warning("워크스페이스 주소를 못 가져옴 → app_redirect 링크를 씀")
    return _workspace_url


def qr_link(channel_id: str) -> str:
    """QR에 넣을 채널 링크.

    https://<워크스페이스>.slack.com/archives/<채널> 은 휴대폰에서 슬랙 앱이 바로 열림 (유니버설 링크).
    slack.com/app_redirect 는 휴대폰 브라우저에서 슬랙 웹 로그인 화면으로 가 버려서 쓰지 않음 (주소를 못 알 때만).
    """
    base = workspace_url()
    if base:
        return f"{base}/archives/{channel_id}"
    return "https://slack.com/app_redirect?" + urlencode({"team": settings.slack_team_id or "", "channel": channel_id})


def _save(room_id: int, **cols) -> None:
    """있으면 고치고, 없으면 새로 (새로 넣을 때는 channel_id가 꼭 있어야 함)."""
    params = {"room_id": room_id, **cols}
    with tx() as conn:
        done = conn.execute("UPDATE bot.room_channels SET " + ", ".join(f"{k} = %({k})s" for k in cols)
                            + " WHERE room_id = %(room_id)s", params).rowcount
        if not done:
            conn.execute(f"INSERT INTO bot.room_channels (room_id, {', '.join(cols)}) "
                         f"VALUES (%(room_id)s, {', '.join(f'%({k})s' for k in cols)})", params)


def _find_channel(client, name: str) -> str | None:
    cursor = None
    while True:
        r = client.conversations_list(types="public_channel", exclude_archived=True, limit=200, cursor=cursor)
        for ch in r["channels"]:
            if ch["name"] == name:
                return ch["id"]
        cursor = r.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return None


def ensure_channel(client, room: dict) -> str:
    """회의실 채널이 없으면 만들고, 봇을 들어가게 하고, 주제를 정함."""
    known = mapping().get(room["id"])
    if known:
        return known["channel_id"]
    name = channel_name(room)
    try:
        cid = client.conversations_create(name=name)["channel"]["id"]
        log.info("채널 만듦: #%s", name)
    except SlackApiError as e:
        if e.response.get("error") != "name_taken":
            raise
        cid = _find_channel(client, name)
        if not cid:
            raise
        client.conversations_join(channel=cid)
    try:
        client.conversations_setTopic(channel=cid, topic=TOPIC.format(label=room["label"]))
        client.conversations_setPurpose(channel=cid, purpose=PURPOSE)
    except SlackApiError:
        pass
    _save(room["id"], channel_id=cid)
    return cid


# ---------------------------------------------------------------
# 체크인 패널
# ---------------------------------------------------------------

def _now_info(room: dict) -> tuple[list[str], bool]:
    """패널 글자: 지금 체크인할 회의가 있으면 '체크인하시겠습니까? 유효시간은 …까지입니다.'"""
    from roombot.checkin import CHECKIN_OPENS_BEFORE, NOSHOW_GRACE
    from roombot.commands import clip, hm, mins, span
    now = clock.now()
    today = [r for r in rooms.room_schedule(room["id"], now.date()) if r["end_at"] > now]
    from roombot import admin
    blocked = admin.block_ids([r["id"] for r in today])   # 관리자 배정은 체크인 받지 않음
    # 체크인 받을 회의: 시작 10분 전 ~ 시작 10분 후, 아직 아무도 체크인 안 함
    due = next((r for r in today if r["start_at"] - CHECKIN_OPENS_BEFORE <= now < r["start_at"] + NOSHOW_GRACE
                and not r["first_checkin_at"] and r["id"] not in blocked), None)
    using = next((r for r in reversed(today) if r["state"] == "사용중"), None)   # 겹치면 나중 회의
    if due:
        return ["*체크인하시겠습니까?*",
                f"유효시간은 *{hm(due['start_at'] + NOSHOW_GRACE)}*까지입니다. "
                f"(*{mins(due['start_at'] + NOSHOW_GRACE - now)}* 남음)",
                f"{clip(due['title'], 20)} · {due['organizer_name']}  ({span(due['start_at'], due['end_at'])})"], True
    lines = []
    if using:
        lines.append(f"{style.badge('사용중')}  {clip(using['title'], 20)} · {using['organizer_name']}"
                     f"  (~{hm(using['end_at'])})")
    nxt = next((r for r in today if r["start_at"] - CHECKIN_OPENS_BEFORE > now), None)
    if nxt and nxt["id"] in blocked:
        lines.append(f"다음 *{hm(nxt['start_at'])}–{hm(nxt['end_at'])}*  {clip(nxt['title'], 20)} (관리자 배정)")
    elif nxt:
        lines.append(f"다음 회의 *{hm(nxt['start_at'])}*  {clip(nxt['title'], 20)} — "
                     f"*{hm(nxt['start_at'] - CHECKIN_OPENS_BEFORE)}*부터 체크인할 수 있어요")
    else:
        lines.append("_오늘 남은 예약이 없어요_")
    return lines, False


def panel_blocks(room: dict) -> list[dict]:
    lines, due = _now_info(room)
    blocks = [
        header(f"{room['label']} 체크인"),
        section("\n".join(lines)),
        # 이미 채널에 참여한 사람은 [채널 참여]가 안 보이므로 체크인 시간에만 버튼을 보여 줌
        actions(button("✅ 체크인", "panel_checkin", room["code"], style="primary")) if due else None,
        context("*[✅ 체크인]* 을 누르면 체크인돼요 · 처음이면 *[채널 참여]* 를 먼저 누른 뒤 체크인 · 참석자 중 누구나") if due
        else context(f"{style.QR} 회의실에서 모바일 QR로 체크인 준비하세요"),
    ]
    return [b for b in blocks if b]


def _hash(blocks: list[dict]) -> str:
    return hashlib.sha1(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def refresh_panel(client, room: dict, force: bool = False) -> None:
    m = mapping().get(room["id"]) or {}
    cid = m.get("channel_id") or ensure_channel(client, room)
    blocks = panel_blocks(room)
    h = _hash(blocks)
    text = f"{room['label']} 체크인 — 버튼을 눌러 체크인하세요"
    old_ts, posted = m.get("panel_ts"), m.get("panel_posted_at")
    stale = not posted or clock.now() - posted > REPOST_AFTER
    if old_ts and not stale and not force:
        if h == m.get("panel_hash"):
            return
        try:
            client.chat_update(channel=cid, ts=old_ts, text=text, blocks=blocks)
            _save(room["id"], panel_hash=h)
            return
        except SlackApiError as e:
            log.warning("패널 고치기 실패 (%s): %s — 새로 올림", room["code"], e.response.get("error"))
    # 새로 올리고 고정, 예전 패널은 지움
    ts = client.chat_postMessage(channel=cid, text=text, blocks=blocks)["ts"]
    try:
        client.pins_add(channel=cid, timestamp=ts)
    except SlackApiError:
        pass
    if old_ts:
        try:
            client.chat_delete(channel=cid, ts=old_ts)
        except SlackApiError:
            pass
    _save(room["id"], channel_id=cid, panel_ts=ts, panel_posted_at=clock.now(), panel_hash=h)


def setup_all(client) -> int:
    """회의실 20개 모두 채널과 패널 준비 (여러 번 해도 안전)."""
    n = 0
    for room in rooms.all_rooms():
        try:
            ensure_channel(client, room)
            refresh_panel(client, room)
            n += 1
        except SlackApiError as e:
            log.warning("회의실 채널 준비 실패 (%s): %s", room["code"], e.response.get("error"))
    return n


def refresh_all(client) -> None:
    """스케줄러가 30초마다: 지금/다음 회의가 바뀐 패널만 고침."""
    for room_id, m in mapping().items():
        room = next((r for r in rooms.all_rooms() if r["id"] == room_id), None)
        if room:
            try:
                refresh_panel(client, room)
            except SlackApiError as e:
                log.warning("패널 갱신 실패 (%s): %s", room["code"], e.response.get("error"))
