"""슬랙봇: /룸(/room) 명령어, 버튼·메뉴, 미팅룸 창, 앱 홈 대시보드 (Socket Mode라 공개 서버 주소 없이 동작)."""

import json
import logging
import os
import re
import time as time_mod
from datetime import date, datetime, time, timedelta

from slack_bolt import App
from slack_sdk.errors import SlackApiError

from roombot import clock, commands, rooms, style
from roombot.blocks import Reply, actions, button, context, divider, header, section
from roombot.config import settings

log = logging.getLogger(__name__)

FINDER_ID = "finder"


# ---------------------------------------------------------------
# 사용자 / 화면 공통
# ---------------------------------------------------------------

_unmatched: dict[str, float] = {}   # 직원과 매칭되지 않은 슬랙 사용자 → 마지막 확인 시각
UNMATCHED_RETRY = 600               # 10분 뒤에 다시 확인 (프로필 이메일을 고쳤을 수 있으니)


def employee_for(client, slack_user_id: str) -> dict | None:
    """슬랙 사용자 → 등록된 직원. 처음엔 슬랙 프로필 이메일로 찾고 연결해 둠."""
    emp = rooms.employee_by_slack_user(slack_user_id)
    if emp:
        return emp
    if time_mod.monotonic() - _unmatched.get(slack_user_id, -UNMATCHED_RETRY) < UNMATCHED_RETRY:
        return None
    try:
        email = client.users_info(user=slack_user_id)["user"]["profile"].get("email")
    except SlackApiError as e:
        log.warning("users.info 실패: %s", e.response.get("error"))
        return None
    emp = rooms.employee_by_email(email) if email else None
    if emp:
        rooms.link_slack_user(emp["id"], slack_user_id)
        _unmatched.pop(slack_user_id, None)
    else:
        _unmatched[slack_user_id] = time_mod.monotonic()
    return emp


_notifier_cache = {}


def _notifier(client):
    """버튼 처리에서 쓸 알림 보내는 곳 (같은 슬랙 클라이언트 재사용)."""
    from roombot.notifier import SlackNotifier
    if "n" not in _notifier_cache:
        _notifier_cache["n"] = SlackNotifier(client)
    return _notifier_cache["n"]


def modal(title: str, reply: Reply, callback_id: str = "info") -> dict:
    return {"type": "modal", "callback_id": callback_id,
            "title": {"type": "plain_text", "text": title[:24], "emoji": True},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": reply.blocks[:100]}


def show(body: dict, client, respond, make, title: str, replace: bool = False) -> None:
    """버튼을 누른 곳에 맞게 결과 보여주기: 홈 탭 → 창, 창 안 → 창 위에 쌓기, 메시지 → 메시지.
    make(surface) 로 화면에 맞는 답을 만듦 (창에서는 표를 쓸 수 없어서 2칸 목록)."""
    container = body.get("container", {})
    trigger = body.get("trigger_id")
    if container.get("type") == "view":
        view = body.get("view") or {}
        from roombot import slackfiles
        with slackfiles.no_wait():          # 창은 3초 안에 열어야 함 (trigger_id 만료)
            reply = make("modal")
        if view.get("type") == "modal":
            if replace:
                client.views_update(view_id=view["id"], view=modal(title, reply))
            else:
                client.views_push(trigger_id=trigger, view=modal(title, reply))
        else:
            client.views_open(trigger_id=trigger, view=modal(title, reply))
        return
    respond(**make("message").slack(), response_type="ephemeral", replace_original=replace)


def _parse_room_value(value: str) -> tuple[dict | None, date]:
    code, _, iso = value.partition("|")
    found = [r for r in rooms.all_rooms() if r["code"] == code]
    return (found[0] if found else None), (date.fromisoformat(iso) if iso else clock.now().date())


# ---------------------------------------------------------------
# 미팅룸 창
# ---------------------------------------------------------------

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


def _finder_values(view: dict) -> tuple[datetime, timedelta]:
    values=view["state"]["values"]
    day=date.fromisoformat(values["date"]["finder_date"]["selected_date"])
    at=time.fromisoformat(values["time"]["finder_time"]["selected_option"]["value"])
    duration=timedelta(minutes=int(values["dur"]["finder_dur"]["selected_option"]["value"]))
    return datetime.combine(day,at,clock.TZ),duration


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


# ---------------------------------------------------------------
# 앱 홈 대시보드
# ---------------------------------------------------------------

def home_view(employee: dict | None) -> dict:
    now = clock.now()
    rows = rooms.status_now()
    free_now = sum(1 for r in rows if r["state"] == "비어있음")
    blocks: list[dict] = [
        header("회의실봇"),
        context(f"{style.DATE} *{commands.fmt_day(now.date())} {commands.hm(now)}* 기준   지금 비어 있는 회의실 *{free_now}곳*"),
        actions(button("미팅룸", "open_finder", style="primary"),
                button("전체 일정", "show_now"), button("내 예약", "show_my")),
    ]
    if employee:
        blocks += commands.next_meeting_block(employee)
    else:
        blocks += commands._not_linked().blocks
    return {"type": "home", "blocks": [b for b in blocks if b][:100]}


# ---------------------------------------------------------------
# 앱
# ---------------------------------------------------------------

def build_app() -> App:
    # SLACK_CLIENT_ID/SECRET 환경 변수가 있으면 Bolt가 여러 워크스페이스용 OAuth 모드로 바뀌어
    # 봇 토큰을 무시함. 이 둘은 QR 웹의 Slack 로그인에만 쓰므로(settings에 이미 읽어 둠) Bolt에서는 숨김.
    for key in ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"):
        os.environ.pop(key, None)
    from roombot import slackfiles
    slackfiles.install_guard()
    app = App(token=settings.slack_bot_token)

    @app.command("/room")
    @app.command("/룸")
    def room_command(ack, command, respond, client):
        ack()
        emp = employee_for(client, command["user_id"])
        reply = commands.handle(command.get("text", ""), emp, command["user_id"])
        if reply.open_finder:
            client.views_open(trigger_id=command["trigger_id"], view=finder_view())
            return
        respond(**reply.slack(), response_type="ephemeral")

    @app.event("app_home_opened")
    def home_opened(event, client):
        if event.get("tab") != "home":
            return
        client.views_publish(user_id=event["user"], view=home_view(employee_for(client, event["user"])))

    @app.event("message")
    def dm_message(event, say, client):
        # 봇 DM에 명령어를 그냥 적어도 동작 (예: "now", "BTC 내일")
        if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype"):
            return
        emp = employee_for(client, event["user"])
        try:
            reply = commands.handle(event.get("text", ""), emp, event["user"])
        except Exception:
            log.exception("DM 처리 실패: %s", event.get("text"))
            say(text="⚠️ 처리 중 문제가 생겼어요. 잠시 후 다시 해 주세요.")   # 아무 답 없이 멈추지 않게
            return
        if reply.open_finder:
            reply = Reply().add(section("아래 버튼으로 찾기 창을 열어주세요."),
                                actions(button("미팅룸", "open_finder", style="primary")))
        say(**reply.slack())

    # ----- 버튼 / 메뉴 -----

    @app.action("room_pick")
    def room_pick(ack, body, client, respond):
        ack()
        room, day = _parse_room_value(body["actions"][0]["selected_option"]["value"])
        if room:
            show(body, client, respond, lambda sf: commands.schedule_reply(room, day, sf), room["label"])

    @app.action(re.compile(r"^room_schedule(:|$)"))
    def room_schedule(ack, body, client, respond):
        ack()
        room, day = _parse_room_value(body["actions"][0]["value"])
        if room:
            show(body, client, respond, lambda sf: commands.schedule_reply(room, day, sf), room["label"])

    @app.action(re.compile(r"^room_switch(:|$)"))
    def room_switch(ack, body, client, respond):
        # 일정표 아래 '회의실 일정 보기' 메뉴: 보고 있던 화면을 고른 회의실 일정으로 바꿈
        ack()
        room, day = _parse_room_value(body["actions"][0]["selected_option"]["value"])
        if room:
            show(body, client, respond, lambda sf: commands.schedule_reply(room, day, sf), room["label"], replace=True)

    @app.action(re.compile(r"^day_nav(:|$)"))
    def day_nav(ack, body, client, respond):
        ack()
        room, day = _parse_room_value(body["actions"][0]["value"])
        if room:
            show(body, client, respond, lambda sf: commands.schedule_reply(room, day, sf), room["label"], replace=True)

    @app.action(re.compile(r"^schedule_date:"))
    def schedule_date(ack, body, client, respond):
        ack()
        action = body['actions'][0]
        code = action['action_id'].split(':', 1)[1]
        if not action.get('selected_date'):
            return
        room, day = _parse_room_value(f"{code}|{action['selected_date']}")
        if room:
            show(body, client, respond, lambda sf: commands.schedule_reply(room, day, sf), room['label'], replace=True)

    @app.action(re.compile(r"^free_at(:|$)"))
    def free_at(ack, body, client, respond):
        ack()
        iso, minutes, cap = body["actions"][0]["value"].split("|")
        start, dur = datetime.fromisoformat(iso), timedelta(minutes=int(minutes))
        show(body, client, respond, lambda sf: commands.free_search(start, dur, int(cap), sf), "미팅룸")

    # ----- 회의실 채널의 체크인 패널 -----

    def slack_checkin(client, room: dict, slack_user_id: str, channel_id: str, how: str) -> None:
        """QR로 회의실 채널을 열고 [채널 참여](처음) 또는 패널의 [체크인](이미 참여한 사람)을 누르면 체크인.

        봇 DM·홈·명령어로는 체크인할 수 없음. 채널에서 내보내지 않음 (내보내면 슬랙이 '퇴장' 알림을 보냄).
        """
        from roombot import checkin, messages, room_channels
        emp = employee_for(client, slack_user_id)
        res = checkin.check_in(room, emp["id"], source_ip=None, user_agent=how) if emp else None
        log.info("채널 체크인 시도: %s · %s · %s → %s", room["code"], emp["name"] if emp else slack_user_id, how,
                 res.kind if res else "계정 연결 안 됨")
        reply = messages.checkin_reply(res) if emp else commands._not_linked()
        # 채널 안에서 바로 결과를 보여 줌 (안 보이면 '안 됐다'고 생각하고 다시 누름)
        try:
            client.chat_postEphemeral(channel=channel_id, user=slack_user_id, **reply.slack())
        except Exception:
            log.exception("채널 안내 실패")
        if settings.leave_after_checkin:
            # 다음에 QR을 찍어도 다시 [채널 참여] = 체크인이 되도록 채널에서 내보냄 (결과는 DM으로도 감)
            import threading

            def _kick():
                try:
                    client.conversations_kick(channel=channel_id, user=slack_user_id)
                except SlackApiError as e:
                    log.warning("채널 내보내기 실패: %s", e.response.get("error"))
            threading.Timer(5, _kick).start()
        notifier = _notifier(client)
        if not emp:
            _dm(client, slack_user_id, reply)
        else:
            messages.send_checkin_messages(notifier, res, emp["id"])   # 성공 확인증 · 예약자 알림 · 다른 회의실 안내
            if res.kind not in ("ok", "joined", "wrong_room"):
                _dm(client, slack_user_id, reply)                       # 채널 안내는 나가면 사라지므로 DM에도
            if res.kind in ("ok", "joined"):
                try:
                    room_channels.refresh_panel(client, room)      # 패널의 '지금' 상태를 바로 사용중으로
                except Exception:
                    log.exception("패널 갱신 실패")

    def _dm(client, slack_user_id: str, reply) -> None:
        try:
            client.chat_postMessage(channel=slack_user_id, unfurl_links=False, **reply.slack())
        except Exception:
            log.exception("체크인 안내 DM 실패")

    def room_of_channel(channel_id: str) -> dict | None:
        from roombot import room_channels
        rid = next((rid for rid, m in room_channels.mapping().items() if m["channel_id"] == channel_id), None)
        return next((r for r in rooms.all_rooms() if r["id"] == rid), None) if rid else None

    @app.action("panel_checkin")
    def panel_checkin(ack, body, client):
        """회의실 채널 패널의 [체크인] (이미 채널에 참여한 사람용, 체크인 시간에만 보임)."""
        ack()
        log.info("슬랙에서 받은 체크인 버튼 데이터: %s", json.dumps(body, ensure_ascii=False))
        code = body["actions"][0]["value"]
        room = next((r for r in rooms.all_rooms() if r["code"] == code), None)
        if room:
            slack_checkin(client, room, body["user"]["id"], body["channel"]["id"], "slack-button")

    @app.event("member_joined_channel")
    def joined_room_channel(event, client, context):
        """QR로 회의실 채널을 열고 '채널 참여'를 누르면 → 그것으로 체크인."""
        if event.get("user") == context.get("bot_user_id"):
            return
        log.info("슬랙에서 받은 채널 참여 데이터: %s", json.dumps(event, ensure_ascii=False))
        room = room_of_channel(event.get("channel", ""))
        if room:
            # 채널 참여만으로는 체크인하지 않음 → 체크인은 언제나 [✅ 체크인] 버튼 (헷갈리지 않게 한 가지 방법만)
            from roombot import room_channels
            _, due = room_channels._now_info(room)
            text = ("채널에 참여했어요. 체크인하려면 아래 *[✅ 체크인]* 을 눌러 주세요." if due
                    else "채널에 참여했어요. 체크인 시간(회의 시작 10분 전~10분 후)에 QR을 찍고 위 패널의 *[✅ 체크인]* 을 눌러 주세요.")
            blocks = [section(text)]
            if due:
                blocks.append(actions(button("✅ 체크인", "panel_checkin", room["code"], style="primary")))
            try:
                client.chat_postEphemeral(channel=event["channel"], user=event["user"], text=text, blocks=blocks)
            except Exception:
                log.exception("참여 안내 실패")

    @app.action("panel_schedule")
    def panel_schedule(ack, body, client, respond):
        ack()
        code = body["actions"][0]["value"]
        room = next((r for r in rooms.all_rooms() if r["code"] == code), None)
        if room:
            respond(**commands.schedule_reply(room, clock.now().date()).slack(),
                    response_type="ephemeral", replace_original=False)

    # ----- 관리자 강제 배정 (/룸 qwerasdf) -----

    @app.action("admin_force")
    def admin_force(ack, body, client, respond):
        ack()
        from roombot import admin, room_channels
        uid = body["user"]["id"]
        if not admin.is_admin(uid):
            return
        room, start, end, reason = admin.decode(body["actions"][0]["value"])
        if not room:
            return
        res, dms = admin.execute(room, start, end, reason, uid)
        respond(**res.slack(), response_type="ephemeral", replace_original=True)
        log.info("관리자 강제 배정: %s %s–%s (%s) by %s, 알림 %d명", room["code"], start, end, reason, uid, len(dms))
        _notifier(client).send_many(dms)
        if settings.room_channels:
            try:
                room_channels.refresh_panel(client, room)
            except Exception:
                log.exception("패널 갱신 실패")

    @app.action("admin_force_cancel")
    def admin_force_cancel(ack, respond):
        ack()
        respond(text="강제 배정을 그만뒀어요.", response_type="ephemeral", replace_original=True)

    @app.action("open_finder")
    def open_finder(ack, body, client):
        ack()
        view = body.get("view") or {}
        if view.get("type") == "modal":
            client.views_push(trigger_id=body["trigger_id"], view=finder_view())
        else:
            client.views_open(trigger_id=body["trigger_id"], view=finder_view())

    @app.action("show_now")
    def show_now(ack, body, client, respond):
        ack()
        show(body, client, respond, lambda sf: commands.status_reply(surface=sf), "회의실 현황")

    @app.action("show_my")
    def show_my(ack, body, client, respond):
        ack()
        emp = employee_for(client, body["user"]["id"])
        show(body, client, respond, lambda sf: commands.my_reply(emp, sf), "내 예약")

    @app.action("show_stats")
    def show_stats(ack, body, client, respond):
        ack()
        show(body, client, respond, lambda sf: commands.stats_reply(), "노쇼 통계")

    @app.action("refresh_home")
    def refresh_home(ack, body, client):
        ack()
        uid = body["user"]["id"]
        client.views_publish(user_id=uid, view=home_view(employee_for(client, uid)))

    @app.action(re.compile(r"^view_(board|room)$"))
    def view_in_browser(ack):
        ack()  # 버튼 URL은 Slack 내장 브라우저가 연다.

    # 조건 변경 즉시 같은 창의 빈방 목록을 갱신한다.
    @app.action(re.compile(r"^finder_(date|time|dur)$"))
    def finder_changed(ack, body, client):
        ack()
        try:
            start,duration=_finder_values(body['view'])
        except (KeyError,TypeError,ValueError):
            return
        client.views_update(view_id=body['view']['id'],hash=body['view'].get('hash'),
                            view=finder_view(start,int(duration.total_seconds()//60)))

    @app.action(re.compile(r"^book_room:"))
    def book_room(ack, body, client):
        ack()
        emp=employee_for(client,body['user']['id'])
        if not emp:
            client.views_push(trigger_id=body['trigger_id'],view=modal("직원 연결",commands._not_linked()))
            return
        client.views_push(trigger_id=body['trigger_id'],view=booking_view(body['actions'][0]['value']))

    @app.view("booking_form")
    def booking_submit(ack, body, view, client):
        from roombot import bookings
        emp=rooms.employee_by_slack_user(body['user']['id'])
        if not emp:
            ack(response_action="errors",errors={"title":"직원 연결을 먼저 확인해 주세요."})
            return
        try:
            data=json.loads(view['private_metadata'])
            start=datetime.fromisoformat(data['start'])
            title=view['state']['values']['title']['title']['value']
            rid=bookings.reserve(data['room'],emp['id'],start,start+timedelta(minutes=data['minutes']),title)
        except (ValueError,KeyError,TypeError) as exc:
            ack(response_action="errors",errors={"title":str(exc)})
            return
        reply=Reply(fallback=f"예약 완료 #{rid}").add(header("예약 완료"),
                  section(f"예약 *#{rid}* · *{title}*"),context(f"참석자 추가: `/룸 참석자 {rid} 사번`"))
        ack(response_action="update",view=modal("예약 완료",reply))

    return app
