"""관리자 전용: 회의실 강제 배정 (/룸 qwerasdf).

  /룸 qwerasdf                                  사용법
  /룸 qwerasdf <회의실> [날짜] <시각> <길이> [사유]   미리보기 → [🚧 강제 배정] 누르면 실행
      예) /룸 qwerasdf 9A 10/20 14:00 2h 공사

실행하면
  1) 그 시간에 겹치는 예약을 취소 (다우오피스에서 취소하는 것처럼 예약을 지우고, 내용은 bot.admin_cancellations에 남김)
  2) 관리자 예약을 넣음 (제목 '🚧 사유', 노쇼·리마인더 대상 아님, 시작하면 자동으로 사용중)
  3) 취소된 예약의 참석자 모두에게 슬랙 DM으로 알림

관리자는 .env 의 ADMIN_SLACK_USER (쉼표로 여러 명). 관리자가 아니면 이 명령은 없는 것처럼 동작함.
"""

from datetime import datetime, timedelta

from roombot import clock, rooms, style
from roombot.blocks import Reply, actions, button, context, divider, header, section
from roombot.config import settings
from roombot.db import tx

KEYWORD = "qwerasdf"
RED, AMBER = "#e5484d", "#f5a524"


def is_admin(slack_user_id: str | None) -> bool:
    admins = {a.strip() for a in (settings.admin_slack_user or "").split(",") if a.strip()}
    return bool(slack_user_id) and slack_user_id in admins


def _hm(dt: datetime) -> str:
    return dt.astimezone(clock.TZ).strftime("%H:%M")


def _when(a: datetime, b: datetime) -> str:
    from roombot.commands import fmt_day
    return f"{fmt_day(a.astimezone(clock.TZ).date())} *{_hm(a)}–{_hm(b)}*"


def usage() -> Reply:
    return Reply(fallback="관리자 강제 배정 사용법").add(
        header("🚧 관리자 강제 배정"),
        section("`/룸 qwerasdf <회의실> [날짜] <시각> <길이> [사유]`\n"
                "예) `/룸 qwerasdf 9A 10/20 14:00 2h 공사`\n"
                "예) `/룸 qwerasdf BTC 내일 9시 하루 공사` (하루 = 09:00–18:00)"),
        context("겹치는 예약은 취소되고 참석자에게 슬랙 DM으로 알려요. 먼저 미리보기를 보여 주고, [🚧 강제 배정]을 눌러야 실행돼요."))


def parse(args: list[str]) -> tuple[dict | None, datetime | None, datetime | None, str, str | None]:
    """→ (회의실, 시작, 끝, 사유, 오류 문구)"""
    from roombot.commands import parse_date, parse_duration, parse_time
    if not args:
        return None, None, None, "", None
    found = rooms.find_rooms(args[0])
    if len(found) != 1:
        return None, None, None, "", f"'{args[0]}' 회의실을 찾지 못했어요 (예: 9A, BTC, bitcoin)."
    room, rest = found[0], args[1:]
    day = parse_date(rest[0]) if rest else None
    if day:
        rest = rest[1:]
    day = day or clock.now().date()
    if rest and rest[0] in ("하루", "종일", "allday"):
        start = datetime.combine(day, datetime.min.time().replace(hour=9), clock.TZ)
        return room, start, start + timedelta(hours=9), " ".join(rest[1:]) or "관리자 사용", None
    t = parse_time(rest[0]) if rest else None
    if not t:
        return None, None, None, "", "시작 시각을 적어 주세요 (예: 14:00, 9시)."
    dur = parse_duration(rest[1]) if len(rest) > 1 else None
    if not dur:
        return None, None, None, "", "사용 시간을 적어 주세요 (예: 2h, 30m, 1시간 30분은 1h30m)."
    start = datetime.combine(day, t, clock.TZ)
    return room, start, start + dur, " ".join(rest[2:]) or "관리자 사용", None


def overlapping(room_id: int, start: datetime, end: datetime) -> list[dict]:
    with tx() as conn:
        rows = conn.execute(
            """SELECT r.id, r.title, r.start_at, r.end_at, r.organizer_id, e.name AS organizer_name,
                      EXISTS (SELECT 1 FROM bot.admin_blocks b WHERE b.reservation_id = r.id) AS is_block
               FROM daou.reservations r JOIN daou.employees e ON e.id = r.organizer_id
               WHERE r.room_id = %s AND tstzrange(r.start_at, r.end_at) && tstzrange(%s, %s)
                 AND NOT EXISTS (SELECT 1 FROM bot.noshow_cancellations n WHERE n.reservation_id = r.id)
               ORDER BY r.start_at""",
            (room_id, start, end)).fetchall()
    att = rooms.attendees_of([r["id"] for r in rows])
    for r in rows:
        r["attendees"] = att.get(r["id"], [])
    return rows


def encode(room: dict, start: datetime, end: datetime, reason: str) -> str:
    return f"{room['code']}|{start.isoformat()}|{end.isoformat()}|{reason}"[:2000]


def decode(value: str) -> tuple[dict | None, datetime, datetime, str]:
    code, s, e, reason = value.split("|", 3)
    room = next((r for r in rooms.all_rooms() if r["code"] == code), None)
    return room, datetime.fromisoformat(s), datetime.fromisoformat(e), reason


def preview(args: list[str]) -> Reply:
    room, start, end, reason, err = parse(args)
    if err:
        return Reply(fallback=err).add(section(f"{style.WARN} {err}"), context("사용법: `/룸 qwerasdf`"))
    if not room:
        return usage()
    if end <= clock.now():
        return Reply(fallback="이미 지난 시간이에요").add(section(f"{style.WARN} 이미 지난 시간이에요."))
    rows = overlapping(room["id"], start, end)
    people = sum(len(r["attendees"]) for r in rows if not r["is_block"])
    r = Reply(fallback=f"🚧 {room['label']} 강제 배정 미리보기", color=AMBER).add(
        header("🚧 강제 배정 미리보기"),
        section(f"{style.ROOM} *{room['label']}*  ·  {_when(start, end)}\n사유: *{reason}*"))
    if rows:
        lines = [f"• {_when(x['start_at'], x['end_at'])}  {x['title']} · {x['organizer_name']}"
                 + ("  _(관리자 배정)_" if x["is_block"] else f"  ({len(x['attendees'])}명)") for x in rows]
        r.add(divider(), section(f"*취소될 예약 {len(rows)}건* · 알림 받을 사람 {people}명\n" + "\n".join(lines)))
    else:
        r.add(context("겹치는 예약이 없어요. 관리자 예약만 들어가요."))
    r.add(actions(button("🚧 강제 배정", "admin_force", encode(room, start, end, reason), style="danger"),
                  button("그만두기", "admin_force_cancel")))
    return r


def execute(room: dict, start: datetime, end: datetime, reason: str, admin_slack_user: str) -> tuple[Reply, list]:
    """강제 배정 실행. → (관리자에게 보여 줄 결과, [(직원, 알림)])"""
    from roombot.commands import free_value
    admin_emp = rooms.employee_by_slack_user(admin_slack_user)
    if not admin_emp:
        return Reply().add(section(f"{style.WARN} 관리자 슬랙 계정이 다우오피스 직원과 연결돼 있지 않아요.")), []
    now = clock.now()
    with tx() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(42, %s)", (room["id"],))   # 같은 회의실 동시 실행 방지
        rows = conn.execute(
            """SELECT r.* FROM daou.reservations r
               WHERE r.room_id = %s AND tstzrange(r.start_at, r.end_at) && tstzrange(%s, %s)
               FOR UPDATE""", (room["id"], start, end)).fetchall()
        att = {}
        for a in conn.execute(
                """SELECT a.reservation_id, e.* FROM daou.reservation_attendees a
                   JOIN daou.employees e ON e.id = a.employee_id WHERE a.reservation_id = ANY(%s)""",
                ([r["id"] for r in rows],)).fetchall():
            att.setdefault(a["reservation_id"], []).append(a)
        noshow = {x["reservation_id"] for x in conn.execute(
            "SELECT reservation_id FROM bot.noshow_cancellations WHERE reservation_id = ANY(%s)",
            ([r["id"] for r in rows],)).fetchall()}
        blocks = {x["reservation_id"] for x in conn.execute(
            "SELECT reservation_id FROM bot.admin_blocks WHERE reservation_id = ANY(%s)",
            ([r["id"] for r in rows],)).fetchall()}
        cancelled = []
        for r in rows:
            if r["id"] not in noshow and r["id"] not in blocks:
                conn.execute(
                    """INSERT INTO bot.admin_cancellations
                         (reservation_id, room_id, title, organizer_id, start_at, end_at, attendee_ids, reason, cancelled_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (r["id"], r["room_id"], r["title"], r["organizer_id"], r["start_at"], r["end_at"],
                     [a["id"] for a in att.get(r["id"], [])], reason, now))
                cancelled.append(r)
        # 다우오피스에서 취소 = 예약이 사라짐 (노쇼로 이미 취소된 예약, 예전 관리자 배정도 같이 정리)
        conn.execute("DELETE FROM daou.reservations WHERE id = ANY(%s)", ([r["id"] for r in rows],))
        bid = conn.execute(
            """INSERT INTO daou.reservations (room_id, title, organizer_id, start_at, end_at)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (room["id"], f"🚧 {reason}", admin_emp["id"], start, end)).fetchone()["id"]
        conn.execute("INSERT INTO daou.reservation_attendees VALUES (%s, %s, true)", (bid, admin_emp["id"]))
        conn.execute("INSERT INTO bot.admin_blocks (reservation_id, reason, admin_slack_user) VALUES (%s, %s, %s)",
                     (bid, reason, admin_slack_user))
        conn.execute("UPDATE bot.admin_cancellations SET block_id = %s WHERE reservation_id = ANY(%s)",
                     (bid, [r["id"] for r in cancelled]))

    dms = []
    for r in cancelled:
        dur = min(r["end_at"] - max(r["start_at"], now), timedelta(hours=2))
        find_at = max(r["start_at"], now)
        msg = Reply(fallback=f"❌ {room['label']} 예약이 취소됐어요 ({reason})", color=RED).add(
            header("회의실 예약이 취소됐어요"),
            section(f"{style.ROOM} *{room['label']}*  ·  {_when(r['start_at'], r['end_at'])}\n*{r['title']}*"),
            section(f"{style.WARN} 사유: *{reason}*\n관리자가 이 시간에 회의실을 써야 해서 예약을 취소했어요."),
            context("다른 회의실이 필요하면 아래에서 찾아 다우오피스에서 다시 예약해 주세요."),
            actions(button(f"{style.FIND} 그 시간 빈 회의실 찾기", "free_at",
                           free_value(find_at, max(dur, timedelta(minutes=30)), len(att.get(r["id"], [])) or 1),
                           style="primary")))
        dms += [(a, msg) for a in att.get(r["id"], [])]

    lines = [f"• {_when(r['start_at'], r['end_at'])}  {r['title']} ({len(att.get(r['id'], []))}명)" for r in cancelled]
    res = Reply(fallback=f"🚧 {room['label']} 강제 배정 완료", color=RED).add(
        header("🚧 강제 배정 완료"),
        section(f"{style.ROOM} *{room['label']}*  ·  {_when(start, end)}\n사유: *{reason}*"),
        section(f"*취소한 예약 {len(cancelled)}건* · 알림 보낸 사람 {len(dms)}명\n" + "\n".join(lines))
        if cancelled else context("취소한 예약은 없어요."))
    return res, dms


def block_ids(reservation_ids: list[int]) -> set[int]:
    if not reservation_ids:
        return set()
    with tx() as conn:
        return {r["reservation_id"] for r in conn.execute(
            "SELECT reservation_id FROM bot.admin_blocks WHERE reservation_id = ANY(%s)",
            (reservation_ids,)).fetchall()}
