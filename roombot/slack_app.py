"""슬랙봇: /룸(/room) 명령어, 버튼·메뉴, 빈 회의실 찾기 창, 앱 홈 대시보드 (Socket Mode라 공개 서버 주소 없이 동작)."""

import json
import logging
import os
import re
import time as time_mod
from datetime import date, datetime, time, timedelta

from slack_bolt import App
from slack_sdk.errors import SlackApiError

from roombot import clock, commands, imgurl, rooms, style
from roombot.blocks import Reply, actions, button, context, divider, header, image, section
from roombot.config import settings

log = logging.getLogger(__name__)

FINDER_ID = "finder"


# ---------------------------------------------------------------
# 사용자 / 화면 공통
# ---------------------------------------------------------------

_unmatched: dict[str, float] = {}   # 직원과 매칭되지 않은 슬랙 사용자 → 마지막 확인 시각
UNMATCHED_RETRY = 600               # 10분 뒤에 다시 확인 (프로필 이메일을 고쳤을 수 있으니)


def employee_for(client, slack_user_id: str) -> dict | None:
    """슬랙 사용자 → 다우오피스 직원. 처음엔 슬랙 프로필 이메일로 찾고 연결해 둠."""
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
# 빈 회의실 찾기 창
# ---------------------------------------------------------------

def finder_view(initial: datetime | None = None, minutes: int = 60, cap: int = 1) -> dict:
    start = initial or rooms.default_start()
    dur_opts = [("30분", "30"), ("1시간", "60"), ("1시간 30분", "90"), ("2시간", "120"), ("3시간", "180")]
    cap_opts = [("상관없음", "1")] + [(f"{n}명 이상", str(n)) for n in (2, 4, 6, 8, 10, 12, 16, 20)]

    def opt(t, v):
        return {"text": {"type": "plain_text", "text": t}, "value": v}

    return {
        "type": "modal", "callback_id": FINDER_ID,
        "title": {"type": "plain_text", "text": "빈 회의실 찾기"},
        "submit": {"type": "plain_text", "text": "찾기"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [
            {"type": "input", "block_id": "date", "label": {"type": "plain_text", "text": "날짜"},
             "element": {"type": "datepicker", "action_id": "v", "initial_date": start.date().isoformat()}},
            {"type": "input", "block_id": "time", "label": {"type": "plain_text", "text": "시작 시간"},
             "element": {"type": "timepicker", "action_id": "v", "initial_time": start.strftime("%H:%M")}},
            {"type": "input", "block_id": "dur", "label": {"type": "plain_text", "text": "사용 시간"},
             "element": {"type": "static_select", "action_id": "v",
                         "options": [opt(t, v) for t, v in dur_opts],
                         "initial_option": opt(*next((o for o in dur_opts if o[1] == str(minutes)), dur_opts[1]))}},
            {"type": "input", "block_id": "cap", "label": {"type": "plain_text", "text": "인원"},
             "element": {"type": "static_select", "action_id": "v",
                         "options": [opt(t, v) for t, v in cap_opts],
                         "initial_option": opt(*next((o for o in cap_opts if o[1] == str(cap)), cap_opts[0]))}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "노쇼로 취소된 예약은 빈 것으로 계산해요."}]},
        ],
    }


def _finder_values(view: dict) -> tuple[datetime, timedelta, int]:
    v = view["state"]["values"]
    d = date.fromisoformat(v["date"]["v"]["selected_date"])
    t = time.fromisoformat(v["time"]["v"]["selected_time"])
    return (datetime.combine(d, t, clock.TZ), timedelta(minutes=int(v["dur"]["v"]["selected_option"]["value"])),
            int(v["cap"]["v"]["selected_option"]["value"]))


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
        actions(button(f"{style.FIND} 빈 회의실 찾기", "open_finder", style="primary"),
                button(f"{style.MY} 내 예약", "show_my"), button(f"{style.STATS} 노쇼 통계", "show_stats"),
                button(f"{style.REFRESH} 새로고침", "refresh_home")),
        divider(),
    ]
    if employee:
        blocks += [context(f"{style.PERSON} *{employee['name']}*님 · {employee['department']}")]
        blocks += commands.next_meeting_block(employee)
    else:
        blocks += commands._not_linked().blocks
    blocks += [divider(),
               section(f"*지금 회의실 현황*   {commands.status_counts(rows)}"),
               image(imgurl.src("board", "회의실 현황판", d=now.date().isoformat()), "회의실 현황판 — 회의실별 오늘 예약 막대"),
               actions(commands.room_menu(now.date())),
               *commands.status_tables(rows, now.date())]
    blocks += [divider(),
               context("상태 표시   " + style.legend("사용중", "체크인 대기", "노쇼 처리 대기", "곧 시작", "비어있음")),
               context(f"{style.QR} 회의 시작 10분 전부터 회의실 입구 QR로 체크인 · 시작 후 10분 안에 체크인이 없으면 자동 취소"),
               context("`/룸 지금` 현황 · `/룸 9A` 일정표 · `/룸 빈방 오후 2시 1시간 6명` · `/룸 22층` · `/룸 통계` · `/룸` 도움말")]
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
                                actions(button(f"{style.FIND} 빈 회의실 찾기", "open_finder", style="primary")))
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

    @app.action(re.compile(r"^free_at(:|$)"))
    def free_at(ack, body, client, respond):
        ack()
        iso, minutes, cap = body["actions"][0]["value"].split("|")
        start, dur = datetime.fromisoformat(iso), timedelta(minutes=int(minutes))
        show(body, client, respond, lambda sf: commands.free_search(start, dur, int(cap), sf), "빈 회의실")

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

    # ----- 빈 회의실 찾기 창 제출 → 같은 창을 결과로 바꿈 -----

    @app.view(FINDER_ID)
    def finder_submit(ack, view):
        start, dur, cap = _finder_values(view)
        if start.date() < clock.now().date():
            ack(response_action="errors", errors={"date": "지난 날짜예요."})
            return
        ack(response_action="update", view=modal("빈 회의실", commands.free_search(start, dur, cap, "modal")))

    return app
