"""Slack home and modal rendering. Event handling lives in backend/slack_app.py."""
import json
from datetime import datetime, timedelta
from backend import clock, commands, rooms
from frontend import style
from frontend.blocks import Reply, button, context, divider, header, section

FINDER_ID = "finder"

def modal(title: str, reply: Reply, callback_id: str = "info") -> dict:
    return {"type": "modal", "callback_id": callback_id,
            "title": {"type": "plain_text", "text": title[:24], "emoji": True},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": reply.blocks[:100]}


def finder_view(initial: datetime | None = None, minutes: int = 60, notice: str = "") -> dict:
    start = initial or rooms.default_start()
    if initial is None:
        if start.time() < rooms.OPEN_TIME:
            start = datetime.combine(start.date(), rooms.OPEN_TIME, clock.TZ)
        elif start.time() >= rooms.CLOSE_TIME:
            start = datetime.combine(start.date() + timedelta(days=1), rooms.OPEN_TIME, clock.TZ)
    dur_opts = [("30분", "30"), ("1시간", "60"), ("1시간 30분", "90"), ("2시간", "120"), ("3시간", "180")]
    time_opts = [(f"{h:02d}:{m:02d}", f"{h:02d}:{m:02d}") for h in range(9,18) for m in (0,30)]
    if start.strftime("%H:%M") not in {v for _,v in time_opts}:
        time_opts.append((start.strftime("%H:%M"), start.strftime("%H:%M")))
        time_opts.sort()
    if str(minutes) not in {v for _,v in dur_opts}:
        dur_opts.append((f"{minutes}분", str(minutes)))

    def opt(label, value):
        return {"text": {"type": "plain_text", "text": label}, "value": value}

    blocks = [
        {"type": "section", "block_id": "date", "text": {"type": "mrkdwn", "text": "날짜"},
         "accessory": {"type": "datepicker", "action_id": "finder_date", "initial_date": start.date().isoformat()}},
        {"type": "section", "block_id": "time", "text": {"type": "mrkdwn", "text": "시작"},
         "accessory": {"type": "static_select", "action_id": "finder_time",
                     "options": [opt(*o) for o in time_opts], "initial_option": opt(start.strftime("%H:%M"),start.strftime("%H:%M"))}},
        {"type": "section", "block_id": "dur", "text": {"type": "mrkdwn", "text": "사용 시간"},
         "accessory": {"type": "static_select", "action_id": "finder_dur",
                     "options": [opt(*o) for o in dur_opts],
                     "initial_option": opt(*next(o for o in dur_opts if o[1]==str(minutes)))}},
    ]
    if notice:
        blocks.append(section(notice))
    end = start + timedelta(minutes=minutes)
    if start < clock.now():
        blocks.append(section("지난 시간이에요. 시작 시간을 다시 선택해 주세요."))
    elif end.date() != start.date() or end.time() > rooms.CLOSE_TIME:
        blocks.append(section("사용 시간이 18시를 넘어요. 시작이나 사용 시간을 바꿔 주세요."))
    else:
        found = rooms.free_rooms(start,end)
        blocks.extend([divider(),context(f"*{commands.span(start,end)} · 예약 가능 {len(found)}곳*")])
        for room in found[:40]:
            value=json.dumps({'room':room['id'],'start':start.isoformat(),'minutes':minutes},separators=(',',':'))
            blocks.append(section(f"*{room['label']}* · {room['capacity']}인",
                                  accessory=button("예약",f"book_room:{room['id']}",value)))
        if not found:
            blocks.append(section("예약 가능한 미팅룸이 없어요. 시간을 바꿔 주세요."))
    return {"type":"modal","callback_id":FINDER_ID,
            "title":{"type":"plain_text","text":"미팅룸"},
            "close":{"type":"plain_text","text":"닫기"},"blocks":[b for b in blocks if b]}


def booking_view(value: str) -> dict:
    data=json.loads(value)
    room=next(r for r in rooms.all_rooms() if r['id']==data['room'])
    start=datetime.fromisoformat(data['start'])
    end=start+timedelta(minutes=data['minutes'])
    return {"type":"modal","callback_id":"booking_form","private_metadata":value,
            "title":{"type":"plain_text","text":"회의실 예약"},
            "submit":{"type":"plain_text","text":"예약"},"close":{"type":"plain_text","text":"취소"},
            "blocks":[section(f"*{room['label']}* · {commands.fmt_day(start.date())} {commands.span(start,end)}"),
                      {"type":"input","block_id":"title","label":{"type":"plain_text","text":"회의 제목"},
                       "element":{"type":"plain_text_input","action_id":"title","max_length":200}},
                      context("예약 후 `/룸 참석자 예약번호 사번`으로 참석자를 추가할 수 있어요.")]}


def home_view(employee: dict | None) -> dict:
    now = clock.now()
    rows = rooms.status_now()
    free_now = sum(1 for r in rows if r["state"] == "비어있음")
    blocks: list[dict] = [
        header("회의실봇"),
        context(f"{style.DATE} *{commands.fmt_day(now.date())} {commands.hm(now)}* 기준   지금 비어 있는 회의실 *{free_now}곳*"),
        commands.quick_actions(),
    ]
    if employee:
        blocks += commands.next_meeting_block(employee)
    else:
        blocks += commands._not_linked().blocks
    return {"type": "home", "blocks": [b for b in blocks if b][:100]}
