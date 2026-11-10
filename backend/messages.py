"""체크인 화면 문구와 슬랙 알림(Block Kit)."""

from dataclasses import dataclass, field
from datetime import timedelta

from backend import clock
from frontend import style
from backend.config import settings
from frontend.blocks import Reply, actions, button, context, divider, header, icon, section
from backend.checkin import NOSHOW_GRACE, CheckinResult
from backend.commands import clip, fmt_day, free_value, hm, mins, room_value, span

GRACE_MIN = int(NOSHOW_GRACE.total_seconds() // 60)

# 알림 왼쪽 색 띠 (상태색과 같은 팔레트)
GREEN, RED, AMBER, BLUE, BRAND = "#30a46c", "#e5484d", "#f5a524", "#3e63dd", "#8e6bc9"


# ---------------------------------------------------------------
# QR 체크인 화면 (웹)
# ---------------------------------------------------------------

@dataclass
class CheckinView:
    ok: bool
    kind: str                     # 화면 아이콘 종류: ok / info / wait / denied / late / free / error
    title: str
    rows: list[tuple[str, str]] = field(default_factory=list)   # (항목, 값)
    note: str = ""
    lead: str = ""                # 제목 바로 아래 크게 보여줄 안내


def _when(dt) -> str:
    return f"{fmt_day(dt.date())} {hm(dt)}"


def _deadline_text(r: dict, now) -> str:
    """체크인은 N분 안에 해주세요 (HH:MM까지)."""
    deadline = r["start_at"] + NOSHOW_GRACE
    return f"체크인은 {mins(deadline - now)} 안에 해주세요 ({hm(deadline)}까지)."


def receipt_rows(rc: dict) -> list[tuple[str, str]]:
    """체크인 확인 정보: 체크인한 사람 / 회의실 / 체크인 시간 + 회의 정보."""
    return [
        ("체크인한 사람", f"{rc['checked_in_by']} ({rc['checked_in_by_department']})"),
        ("회의실", rc["room_name"]),
        ("체크인 시간", _when(rc["checked_in_at"])),
        ("회의", rc["title"]),
        ("회의 시간", span(rc["start_at"], rc["end_at"])),
        ("예약자", rc["organizer_name"]),
    ]


def _now_in_room(room: dict) -> str:
    """이 회의실의 지금 예약 (없으면 다음 예약)."""
    from backend import rooms
    now = clock.now()
    sched = [r for r in rooms.room_schedule(room["id"], now.date()) if r["state"] != "노쇼 취소" and r["end_at"] > now]
    cur = next((r for r in sched if r["start_at"] <= now), None)
    if cur:
        return f"{cur['title']} · {cur['organizer_name']} ({span(cur['start_at'], cur['end_at'])})"
    nxt = next(iter(sched), None)
    return f"없음 (다음 {hm(nxt['start_at'])} {nxt['title']})" if nxt else "없음 (오늘 남은 예약 없음)"


def _mine(r: dict | None, room_label: str) -> str:
    if not r:
        return "오늘 남은 내 예약 없음"
    where = r.get("room_name") or room_label
    return f"{r['title']} · {where} ({span(r['start_at'], r['end_at'])})"


def three_rows(room: dict, mine: dict | None) -> list[tuple[str, str]]:
    """체크인 결과 맨 위 3줄: 체크인 회의실 / 지금 예약 / 내 다음 예약."""
    label = room.get("label") or room["name"]
    return [("체크인 회의실", label), ("지금 예약", _now_in_room(room)), ("내 다음 예약", _mine(mine, label))]


def checkin_view(res: CheckinResult) -> CheckinView:
    room, r = res.room, res.reservation
    label = room.get("label") or room["name"]
    now = clock.now()
    if res.kind == "ok":
        lead = ""
        if res.receipt["checked_in_at"] < r["start_at"]:
            lead = f"회의 시작 {mins(r['start_at'] - res.receipt['checked_in_at'])} 전에 미리 체크인했어요."
            note = "지금부터 사용중으로 표시되고, 노쇼로 취소되지 않아요."
        else:
            note = "회의실이 사용중으로 바뀌었어요."
        return CheckinView(True, "ok", "체크인 완료", receipt_rows(res.receipt), note + " 슬랙으로도 확인 메시지를 보냈어요.",
                           lead=lead)
    if res.kind == "joined":
        return CheckinView(True, "ok", "참석 확인 완료", receipt_rows(res.receipt),
                           "이미 다른 참석자가 체크인한 회의예요. 참석 기록을 남겼어요.")
    if res.kind == "already":
        return CheckinView(True, "info", "이미 체크인했어요", receipt_rows(res.receipt))
    if res.kind == "wrong_room":
        e = res.elsewhere
        return CheckinView(False, "denied", "체크인할 수 없어요",
                           three_rows(room, e),
                           "예약한 회의실로 가서 그 회의실 입구의 QR을 찍어 주세요. 슬랙으로도 안내를 보냈어요.",
                           lead="여기는 예약한 회의실이 아니에요. 예약한 회의실을 다시 찾아주세요. "
                                + _deadline_text(e, now))
    if res.kind == "not_attendee":
        rows = three_rows(room, res.my_next or res.elsewhere)
        if res.my_next:
            # 이 회의실에 내 회의가 나중에 있음: 지금은 다른 회의 중
            m = res.my_next
            return CheckinView(False, "wait", "아직 체크인 시간이 아니에요",
                               rows + [("체크인 가능", f"{hm(res.opens_at)}부터")],
                               f"{hm(res.opens_at)}이 되면 QR을 다시 찍고 회의실 채널의 [✅ 체크인]을 눌러 주세요.",
                               lead=f"지금은 다른 회의 중이에요. 내 회의 시작 {mins(m['start_at'] - now)} 전입니다. "
                                    f"{mins(res.opens_at - now)} 후에 체크인하세요.")
        if res.elsewhere:
            note = "내 회의는 위 회의실에서 시작 10분 전부터 체크인할 수 있어요."
        else:
            note = "오늘 잡힌 내 예약이 없어요. 이 회의실을 쓰려면 회의실봇에서 먼저 예약해 주세요."
        # 예약자 대신 온 경우 (휴가 등): 회의실봇에서 참석자로 추가되면 바로 체크인할 수 있음
        who = f"예약자({r['organizer_name']})" if r.get("organizer_name") else "예약자"
        note += (f" {who} 대신 왔다면, 회의실봇에서 이 예약의 참석자로 추가해 달라고 하세요. "
                 "추가되면 QR을 다시 찍어 바로 체크인할 수 있어요.")
        return CheckinView(False, "denied", "체크인할 수 없어요", rows, note,
                           lead="이 회의실의 예약자·참석자가 아니에요. 예약한 회의실을 다시 찾아주세요.")
    if res.kind == "too_early":
        return CheckinView(False, "wait", "아직 체크인 시간이 아니에요",
                           three_rows(room, r) + [("체크인 가능", f"{hm(res.opens_at)}부터")],
                           lead=f"회의 시작 {mins(r['start_at'] - now)} 전입니다. {mins(res.opens_at - now)} 후에 체크인하세요.",
                           note=f"시작 10분 전부터 체크인할 수 있어요. {hm(res.opens_at)}이 되면 QR을 다시 찍고 "
                           "회의실 채널의 [✅ 체크인]을 눌러 주세요.")
    if res.kind == "too_late":
        return CheckinView(False, "late", "체크인 시간이 지났어요",
                           three_rows(room, res.elsewhere) + [("취소된 예약", f"{r['title']} ({span(r['start_at'], r['end_at'])})")],
                           f"시작 후 {GRACE_MIN}분 안에 체크인하지 않아 노쇼로 자동 취소됐어요. 계속 쓰려면 회의실봇에서 다시 예약해 주세요.")
    return CheckinView(False, "free", "체크인할 수 없어요", three_rows(room, res.elsewhere),
                       "이 시간에 회의실을 쓰려면 회의실봇에서 먼저 예약해 주세요.",
                       lead="지금 이 회의실에 잡힌 내 예약이 없어요. 예약한 회의실을 다시 찾아주세요.")


def checkin_page_text(res: CheckinResult) -> tuple[str, str]:
    v = checkin_view(res)
    body = "\n".join(f"  {k:<8} {val}" for k, val in v.rows)
    return v.title, body + (f"\n  {v.note}" if v.note else "")


# ---------------------------------------------------------------
# 슬랙 DM / 채널 메시지
# ---------------------------------------------------------------

def _receipt_fields(rc: dict) -> list[str]:
    return [f"*{k}*\n{v}" for k, v in receipt_rows(rc)]


def _res_line(r: dict) -> str:
    return (f"{style.ROOM} *{r['room_name']}*\n"
            f"{style.DATE} *{fmt_day(r['start_at'].date())} {span(r['start_at'], r['end_at'])}*   {r['title']}")


def _schedule_btn(r: dict) -> dict | None:
    return button("회의실 일정표", "room_schedule", room_value(r["room_code"], r["start_at"].date())) if r.get("room_code") else None


def dm_checkin_done(rc: dict) -> Reply:
    return Reply(fallback=f"✅ {rc['room_name']} 체크인 완료 ({hm(rc['checked_in_at'])})", color=GREEN).add(
        header("회의실 체크인 완료"),
        section("\n".join(f"*{k}* : {v}" for k, v in receipt_rows(rc))),
        context("이 회의는 이제 노쇼로 취소되지 않아요."
                + (" 회의실 채널에서는 자동으로 나갔어요. 다시 [채널 참여]를 누르지 않아도 돼요."
                   if settings.leave_after_checkin else "")))


def dm_checkin_to_organizer(rc: dict) -> Reply:
    return Reply(fallback=f"✅ {rc['checked_in_by']}님이 {rc['room_name']} 체크인", color=GREEN).add(
        header(f"{rc['checked_in_by']}님이 체크인했어요"),
        section("", fields=_receipt_fields(rc)),
        context("예약하신 회의가 사용중으로 확인됐어요. 노쇼로 취소되지 않아요."))


def dm_wrong_room(res: CheckinResult) -> Reply:
    """다른 회의실 QR을 찍었을 때: 내 예약 회의실로 안내."""
    e = res.elsewhere
    scanned = res.room.get("label") or res.room["name"]
    return Reply(fallback=f"⛔ {scanned}은(는) 예약한 회의실이 아니에요 → {e['room_name']}", color=RED).add(
        header("예약한 회의실이 아니에요"),
        section("\n".join(f"*{k}* : {v}" for k, v in three_rows(res.room, e))),
        context(f"{style.QR} *{e['room_name']}* 입구의 QR을 찍어 체크인해 주세요   "
                f"{style.DEADLINE} {hm(e['start_at'] + NOSHOW_GRACE)}까지 아무도 체크인하지 않으면 자동 취소"),
        actions(button("회의실 일정표", "room_schedule", room_value(e["room_code"], e["start_at"].date()))))


def dm_checkin_open(r: dict) -> Reply:
    deadline = hm(r["start_at"] + NOSHOW_GRACE)
    return Reply(fallback=f"🔔 {r['room_name']} {hm(r['start_at'])} 회의 — QR 체크인해 주세요", color=BLUE).add(
        header("곧 회의가 시작돼요"),
        section(_res_line(r), accessory=_schedule_btn(r)),
        section(f"{style.DEADLINE} *{_deadline_text(r, clock.now())}*"),
        context(f"{style.QR} 회의실 입구 QR을 휴대폰으로 찍어 체크인하세요 · 참석자 중 누구나 OK",
                f"*{deadline}*까지 아무도 체크인하지 않으면 자동 취소돼요"))


def dm_noshow_warning(r: dict) -> Reply:
    deadline = hm(r["start_at"] + NOSHOW_GRACE)
    return Reply(fallback=f"⚠️ {r['room_name']} 아직 체크인 전 — {deadline}까지", color=AMBER).add(
        header("아직 아무도 체크인하지 않았어요"),
        section(_res_line(r)),
        section(f"{style.DEADLINE} *{_deadline_text(r, clock.now())}*"),
        context(f"*{deadline}*까지 QR 체크인하지 않으면 예약이 취소되고 다른 사람이 쓸 수 있게 돼요."))


def dm_auto_cancelled(r: dict) -> Reply:
    now = clock.now()
    remaining = max(r["end_at"] - now, timedelta(minutes=30))
    return Reply(fallback=f"❌ {r['room_name']} 예약이 노쇼로 취소됐어요", color=RED).add(
        header("노쇼로 예약이 취소됐어요"),
        section(_res_line(r)),
        context(f"시작 후 {GRACE_MIN}분 안에 체크인이 없어 자동 취소했어요. 계속 쓰려면 회의실봇에서 다시 예약해 주세요."),
        actions(button(f"{style.FIND} 지금 빈 회의실 찾기", "free_at", free_value(now, min(remaining, timedelta(hours=2)), 1),
                       style="primary")))


def channel_checked_in(rc: dict) -> Reply:
    return Reply(fallback=f"{rc['room_name']} 체크인 · {rc['checked_in_by']}", color=BRAND).add(
        context(f"{icon('사용중')} *{rc['room_name']}* 사용 시작   {style.PERSON} {rc['checked_in_by']} "
                f"({rc['checked_in_by_department']}) {hm(rc['checked_in_at'])} 체크인   {rc['title']} *{span(rc['start_at'], rc['end_at'])}*"))


def channel_room_freed(r: dict) -> Reply:
    return Reply(fallback=f"🟢 {r['room_name']} 지금 비었어요 (노쇼)", color=GREEN).add(
        section(f"{icon('비어있음')} *{r['room_name']}* 지금 비었어요   *~{hm(r['end_at'])}*\n"
                f"_{r['title']}_ 예약이 노쇼로 자동 취소됐어요.",
                accessory=_schedule_btn(r)))


def dm_morning_brief(employee: dict, meetings: list[dict]) -> Reply:
    today = clock.now().date()
    lines = [f"{icon(m['state'])} *{span(m['start_at'], m['end_at'])}*   {clip(m['title'], 24)}   {style.ROOM} {m['room_name']}"
             + ("  ·  예약자" if m["i_am_organizer"] else "") for m in meetings]
    return Reply(fallback=f"☀️ 오늘 회의 {len(meetings)}건", color=BRAND).add(
        header(f"{employee['name']}님, 오늘 회의 {len(meetings)}건"),
        context(f"{fmt_day(today)} · 시작 10분 전부터 회의실 QR로 체크인하세요 · 시작 10분 후까지 아무도 체크인 안 하면 자동 취소"),
        section("\n".join(lines[:20])),
        actions(button(f"{style.MY} 내 예약 자세히", "show_my"), button(f"{style.FIND} 빈 회의실 찾기", "open_finder")))


# ---------------------------------------------------------------
# QR로 회의실 채널에 들어와 체크인했을 때 (봇 DM으로 보내는 결과)
# ---------------------------------------------------------------

_VIEW_COLOR = {"ok": GREEN, "info": BLUE, "wait": AMBER, "denied": RED, "late": RED, "free": BLUE}


def checkin_reply(res: CheckinResult) -> Reply:
    v = checkin_view(res)
    r = Reply(fallback=v.title, color=_VIEW_COLOR.get(v.kind, BLUE))
    r.add(header(v.title))
    if v.lead:
        r.add(section(f"*{v.lead}*"))
    if v.rows:
        r.add(section("\n".join(f"*{k}* : {val}" for k, val in v.rows)))   # 한 줄에 하나씩
    if v.note:
        r.add(context(v.note.replace("슬랙으로도 확인 메시지를 보냈어요.", "봇 DM으로도 확인 메시지를 보냈어요.")))
    if settings.leave_after_checkin:
        r.add(context("잠시 후 이 채널에서 자동으로 나가요. 화면에 [채널 참여]가 다시 보여도 누르지 않아도 돼요."
                      if v.ok else "잠시 후 이 채널에서 자동으로 나가요."))
    return r


def send_checkin_messages(notifier, res: CheckinResult, employee_id: int) -> None:
    """체크인 후 알림: 본인 DM(확인증) · 예약자 DM · 공지 채널. 다른 회의실이면 내 회의실 안내 DM."""
    from backend import rooms
    me = rooms.employee_by_id(employee_id)
    if res.kind == "wrong_room":
        if me:
            notifier.dm(me, dm_wrong_room(res))
        return
    if res.kind not in ("ok", "joined") or not res.receipt:
        return
    if me:
        notifier.dm(me, dm_checkin_done(res.receipt))
    for org in res.notify:
        notifier.dm(org, dm_checkin_to_organizer(res.receipt))
    if res.kind == "ok":
        notifier.channel(channel_checked_in(res.receipt))
