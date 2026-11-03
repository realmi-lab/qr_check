"""회의실·예약 조회 (슬랙 명령어와 홈 탭에서 사용)."""

import re
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime

from roombot import clock
from roombot.db import tx

OPEN_TIME = dtime(9, 0)
CLOSE_TIME = dtime(18, 0)


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, dtime(0), clock.TZ)
    return start, start + timedelta(days=1)


# ---------------------------------------------------------------
# 회의실 / 직원 찾기
# ---------------------------------------------------------------

_rooms_cache: tuple[float, list[dict]] | None = None


def all_rooms() -> list[dict]:
    """회의실 목록 (거의 안 바뀌므로 1분간 캐시)."""
    global _rooms_cache
    now = time.monotonic()
    if _rooms_cache and now - _rooms_cache[0] < 60:
        return _rooms_cache[1]
    with tx() as conn:
        rows = conn.execute(
            "SELECT *, bot.room_label(name, floor) AS label FROM daou.rooms WHERE is_active ORDER BY floor, id"
        ).fetchall()
    _rooms_cache = (now, rows)
    return rows


def clear_cache() -> None:
    global _rooms_cache
    _rooms_cache = None


def find_rooms(query: str) -> list[dict]:
    """회의실 찾기: 코드(9A, BTC), 이름(Bitcoin), 이름 일부(bit), 층(22층). 정확히 맞으면 그 하나만."""
    q = _norm(query)
    if not q:
        return []
    rooms = all_rooms()
    exact = [r for r in rooms if q in (_norm(r["code"]), _norm(r["name"]), _norm(r["label"]))]
    if exact:
        return exact
    m = re.fullmatch(r"(\d{1,2})(?:층|f)", q)
    if m:
        return [r for r in rooms if r["floor"] == int(m[1])]
    return [r for r in rooms if q in _norm(r["name"]) or q in _norm(r["code"])]


def floors() -> list[int]:
    return sorted({r["floor"] for r in all_rooms()})


def employee_by_id(employee_id: int) -> dict | None:
    with tx() as conn:
        return conn.execute("SELECT * FROM daou.employees WHERE id = %s", (employee_id,)).fetchone()


def employee_by_email(email: str) -> dict | None:
    """슬랙 이메일로 직원 찾기. 이메일이 똑같으면 그 사람, 아니면 @ 앞 아이디로 찾음
    (슬랙은 hong@company.co.kr, 다우오피스는 hong@company.com 처럼 도메인만 다른 경우). 아이디가 겹치면 못 찾은 것으로."""
    with tx() as conn:
        row = conn.execute("SELECT * FROM daou.employees WHERE lower(email) = lower(%s)", (email,)).fetchone()
        if row:
            return row
        same_id = conn.execute(
            "SELECT * FROM daou.employees WHERE lower(split_part(email, '@', 1)) = lower(split_part(%s, '@', 1)) LIMIT 2",
            (email,)).fetchall()
        return same_id[0] if len(same_id) == 1 else None


def employee_by_emp_no(emp_no: str) -> dict | None:
    with tx() as conn:
        return conn.execute("SELECT * FROM daou.employees WHERE upper(emp_no) = upper(%s)", (emp_no,)).fetchone()


def employee_by_slack_user(slack_user_id: str) -> dict | None:
    with tx() as conn:
        return conn.execute(
            """SELECT e.* FROM bot.slack_users s JOIN daou.employees e ON e.id = s.employee_id
               WHERE s.slack_user_id = %s""",
            (slack_user_id,),
        ).fetchone()


def slack_user_of(employee_id: int) -> str | None:
    with tx() as conn:
        row = conn.execute("SELECT slack_user_id FROM bot.slack_users WHERE employee_id = %s",
                           (employee_id,)).fetchone()
    return row["slack_user_id"] if row else None


def link_slack_user(employee_id: int, slack_user_id: str) -> None:
    """슬랙 계정과 직원 연결 (한 슬랙 계정은 한 직원에만)."""
    with tx() as conn:
        conn.execute("DELETE FROM bot.slack_users WHERE slack_user_id = %s AND employee_id <> %s",
                     (slack_user_id, employee_id))
        conn.execute(
            """INSERT INTO bot.slack_users (employee_id, slack_user_id) VALUES (%s, %s)
               ON CONFLICT (employee_id) DO UPDATE SET slack_user_id = EXCLUDED.slack_user_id, linked_at = now()""",
            (employee_id, slack_user_id),
        )


# ---------------------------------------------------------------
# 현황 / 일정 / 빈 회의실 / 내 예약
# ---------------------------------------------------------------

def status_now() -> list[dict]:
    with tx() as conn:
        return conn.execute("SELECT * FROM bot.v_room_status_now ORDER BY room_id").fetchall()


def attendees_of(reservation_ids: list[int]) -> dict[int, list[dict]]:
    if not reservation_ids:
        return {}
    with tx() as conn:
        rows = conn.execute(
            """SELECT a.reservation_id, a.is_organizer, e.id, e.name, e.department
               FROM daou.reservation_attendees a JOIN daou.employees e ON e.id = a.employee_id
               WHERE a.reservation_id = ANY(%s)
               ORDER BY a.reservation_id, a.is_organizer DESC, e.name""",
            (reservation_ids,),
        ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["reservation_id"], []).append(r)
    return out


def room_schedule(room_id: int, day: date) -> list[dict]:
    """하루 예약 목록 (상태, 첫 체크인 정보, 참석자 포함)."""
    return room_schedules([room_id], day)[room_id]


def room_schedules(room_ids: list[int], day: date, *, include_attendees: bool = True) -> dict[int, list[dict]]:
    """여러 회의실의 하루 일정을 한 번에 조회."""
    schedules: dict[int, list[dict]] = {room_id: [] for room_id in room_ids}
    if not schedules:
        return schedules
    start, end = day_bounds(day)
    with tx() as conn:
        rows = conn.execute(
            """SELECT v.*, ck.checked_in_by
               FROM bot.v_reservations v
               LEFT JOIN LATERAL (
                 SELECT checked_in_by FROM bot.v_checkin_receipts c
                 WHERE c.reservation_id = v.id ORDER BY c.checked_in_at LIMIT 1
               ) ck ON true
               WHERE v.room_id = ANY(%s) AND v.start_at >= %s AND v.start_at < %s
                 AND v.state <> '노쇼 취소'          -- 노쇼로 취소된 회의는 일정에서 아예 지움 (그 시간은 빈 시간)
               ORDER BY v.room_id, v.start_at, v.id""",
            (list(schedules), start, end),
        ).fetchall()
    att = attendees_of([r["id"] for r in rows]) if include_attendees and rows else {}
    for r in rows:
        r["attendees"] = att.get(r["id"], [])
        schedules[r["room_id"]].append(r)
    return schedules


def free_windows(schedule: list[dict], day: date) -> list[tuple[datetime, datetime]]:
    """09~18시 중 예약이 없는 시간대 (노쇼 취소된 예약은 빈 것으로 봄). 지난 시간은 뺌."""
    cursor = datetime.combine(day, OPEN_TIME, clock.TZ)
    close = datetime.combine(day, CLOSE_TIME, clock.TZ)
    cursor = max(cursor, _ceil_30(clock.now()))
    out = []
    for r in schedule:
        if r["state"] == "노쇼 취소":
            continue
        if r["start_at"] > cursor:
            out.append((cursor, min(r["start_at"], close)))
        cursor = max(cursor, r["end_at"])
    if cursor < close:
        out.append((cursor, close))
    return [(a, b) for a, b in out if b > a]


def _ceil_30(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    extra = (30 - dt.minute % 30) % 30
    return dt + timedelta(minutes=extra)


def default_start() -> datetime:
    """빈 회의실 찾기의 기본 시작 시각: 지금 이후 가장 가까운 30분 단위."""
    return _ceil_30(clock.now())


def free_rooms(start: datetime, end: datetime, min_capacity: int = 1) -> list[dict]:
    """start~end 동안 비어 있는 회의실 (노쇼 취소된 예약은 빈 것으로 봄)."""
    with tx() as conn:
        return conn.execute(
            """SELECT ro.*, bot.room_label(ro.name, ro.floor) AS label,
                      (SELECT min(r.start_at) FROM daou.reservations r
                        WHERE r.room_id = ro.id AND r.start_at >= %(end)s
                          AND NOT EXISTS (SELECT 1 FROM bot.noshow_cancellations n WHERE n.reservation_id = r.id)
                      ) AS next_start_at
               FROM daou.rooms ro
               WHERE ro.is_active AND ro.capacity >= %(cap)s
                 AND NOT EXISTS (
                   SELECT 1 FROM daou.reservations r
                   WHERE r.room_id = ro.id
                     AND tstzrange(r.start_at, r.end_at) && tstzrange(%(start)s, %(end)s)
                     AND NOT EXISTS (SELECT 1 FROM bot.noshow_cancellations n WHERE n.reservation_id = r.id))
               ORDER BY ro.capacity, ro.floor, ro.id""",
            {"start": start, "end": end, "cap": min_capacity},
        ).fetchall()


def my_reservations(employee_id: int, days: int = 7) -> list[dict]:
    """오늘부터 며칠간 내가 참석하는 예약 (지난 회의 포함, 오늘 것은 상태 표시)."""
    start, _ = day_bounds(clock.now().date())
    end = start + timedelta(days=days + 1)
    with tx() as conn:
        rows = conn.execute(
            """SELECT v.*, a.is_organizer AS i_am_organizer, ck.checked_in_by,
                      EXISTS (SELECT 1 FROM bot.checkins c
                              WHERE c.reservation_id = v.id AND c.employee_id = %(emp)s) AS i_checked_in
               FROM bot.v_reservations v
               JOIN daou.reservation_attendees a ON a.reservation_id = v.id AND a.employee_id = %(emp)s
               LEFT JOIN LATERAL (
                 SELECT checked_in_by FROM bot.v_checkin_receipts c
                 WHERE c.reservation_id = v.id ORDER BY c.checked_in_at LIMIT 1
               ) ck ON true
               WHERE v.start_at >= %(start)s AND v.start_at < %(end)s
                 AND v.state <> '노쇼 취소'          -- 노쇼로 취소된 회의는 내 예약에서도 지움
               ORDER BY v.start_at""",
            {"emp": employee_id, "start": start, "end": end},
        ).fetchall()
    return rows


def is_holiday(day: date) -> str | None:
    """공휴일 이름, 주말이면 '주말', 아니면 None."""
    with tx() as conn:
        row = conn.execute("SELECT name FROM daou.holidays WHERE holiday_date = %s", (day,)).fetchone()
    if row:
        return row["name"]
    if day.isoweekday() >= 6:
        return "주말"
    return None


# ---------------------------------------------------------------
# 통계 / 아침 브리핑
# ---------------------------------------------------------------

def stats(days: int = 7) -> dict:
    """최근 며칠(오늘 포함) 노쇼 규칙이 적용된 예약 기준 체크인·노쇼 통계."""
    start, _ = day_bounds(clock.now().date() - timedelta(days=days - 1))
    with tx() as conn:
        from_row = conn.execute("SELECT value FROM bot.system_settings WHERE key = 'noshow_enforced_from'").fetchone()
        enforced = datetime.fromisoformat(from_row["value"]) if from_row else clock.now()
        since = max(start, enforced)
        params = {"since": since, "now": clock.now()}
        total = conn.execute(
            """SELECT count(*) FILTER (WHERE state IN ('사용중', '종료') AND first_checkin_at IS NOT NULL) AS used,
                      count(*) FILTER (WHERE state = '노쇼 취소')                                         AS noshow,
                      count(*)                                                                            AS total
               FROM bot.v_reservations
               WHERE start_at >= %(since)s AND start_at + bot.noshow_grace() <= %(now)s""",
            params,
        ).fetchone()
        by_room = conn.execute(
            """SELECT room_name, count(*) FILTER (WHERE state = '노쇼 취소') AS noshow, count(*) AS total
               FROM bot.v_reservations
               WHERE start_at >= %(since)s AND start_at + bot.noshow_grace() <= %(now)s
               GROUP BY room_name HAVING count(*) FILTER (WHERE state = '노쇼 취소') > 0
               ORDER BY noshow DESC, room_name LIMIT 5""",
            params,
        ).fetchall()
        by_org = conn.execute(
            """SELECT organizer_name, count(*) AS noshow
               FROM bot.v_reservations
               WHERE start_at >= %(since)s AND state = '노쇼 취소'
               GROUP BY organizer_name ORDER BY noshow DESC, organizer_name LIMIT 5""",
            params,
        ).fetchall()
    return {"since": since, "days": days, **total, "by_room": by_room, "by_organizer": by_org}


def linked_users() -> list[dict]:
    """슬랙 계정이 연결된 직원 (아침 브리핑 대상)."""
    with tx() as conn:
        return conn.execute(
            """SELECT e.*, s.slack_user_id FROM bot.slack_users s JOIN daou.employees e ON e.id = s.employee_id"""
        ).fetchall()
