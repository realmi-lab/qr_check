"""QR 체크인과 노쇼 자동취소.

규칙
  - 체크인은 시작 10분 전부터 가능, 참석자 중 누구나 QR로 체크인하면 인정
  - 시작 후 10분 안에 아무도 체크인하지 않으면 노쇼로 자동 취소 (체크인은 시작 10분 전 ~ 10분 후)
  - 이미 사용 중인 회의에 늦게 온 참석자도 체크인 기록은 남김

체크인과 자동취소는 BEGIN IMMEDIATE 트랜잭션으로 직렬화한다.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from roombot import clock
from roombot.config import settings
from roombot.db import tx

CHECKIN_OPENS_BEFORE = timedelta(minutes=10)
NOSHOW_GRACE = timedelta(minutes=10)
NOSHOW_WARNING_AFTER = timedelta(minutes=5)   # 시작 5분 후에도 체크인이 없으면 경고 (5분 남음)


@dataclass
class CheckinResult:
    # ok          : 첫 체크인 성공 → 회의실 사용중
    # joined      : 이미 사용 중인 회의에 추가로 체크인
    # already     : 이 사람은 이미 체크인함
    # wrong_room  : 이 회의실은 내 예약이 아님. 지금 다른 회의실(elsewhere)에 내 회의가 있음 → 그쪽으로 안내
    # not_attendee: 지금 예약의 참석자가 아님 (my_next: 오늘 이 회의실의 내 다음 예약)
    # too_early   : 내 다음 예약의 체크인 시간 전
    # too_late    : 시작 후 10분이 지나 노쇼 처리됨 / 처리 예정
    # no_reservation : 지금 체크인할 예약이 없음 (reservation: 오늘 다음 예약)
    kind: str
    room: dict
    reservation: dict | None = None
    receipt: dict | None = None
    opens_at: datetime | None = None
    my_next: dict | None = None
    elsewhere: dict | None = None   # 다른 회의실에 잡힌 오늘 내 예약 (room_name, room_code 포함)
    notify: list[dict] = field(default_factory=list)   # 체크인 알림을 받을 사람 (예약자 등)


# ---------------------------------------------------------------
# QR
# ---------------------------------------------------------------

def _derived_token(code: str) -> str:
    """회의실 코드 + QR_SECRET 으로 계산한 토큰. DB를 다시 만들어도 같은 값 → 인쇄한 QR이 계속 동작."""
    return hmac.new(settings.qr_secret.encode(), code.upper().encode(), hashlib.sha256).hexdigest()[:24]


def _token_of(row: dict) -> str:
    # room_qr_tokens 에 값이 있으면 그 회의실만 새 토큰 (QR 교체용)
    return row.get("override_token") or _derived_token(row["code"])


def room_for_qr(room_code: str, token: str) -> dict | None:
    """QR 안의 회의실 코드와 토큰이 맞으면 회의실을 돌려줌."""
    with tx() as conn:
        row = conn.execute(
            """SELECT ro.*, room_label(ro.name, ro.floor) AS label, t.token AS override_token
               FROM rooms ro LEFT JOIN room_qr_tokens t ON t.room_id = ro.id
               WHERE upper(ro.code) = upper(?) AND ro.is_active""",
            (room_code,),
        ).fetchone()
    if not row or not hmac.compare_digest(_token_of(row), token or ""):
        return None
    row.pop("override_token")
    return row


def qr_tokens() -> list[dict]:
    with tx() as conn:
        rows = conn.execute(
            """SELECT ro.id, ro.code, ro.name, room_label(ro.name, ro.floor) AS label,
                      ro.floor, ro.capacity, ro.equipment, t.token AS override_token
               FROM rooms ro LEFT JOIN room_qr_tokens t ON t.room_id = ro.id
               WHERE ro.is_active ORDER BY ro.floor, ro.id"""
        ).fetchall()
    for r in rows:
        r["token"] = _token_of(r)
        r.pop("override_token")
    return rows


# ---------------------------------------------------------------
# 체크인
# ---------------------------------------------------------------

def check_in(room: dict, employee_id: int, source_ip: str | None = None,
             user_agent: str | None = None) -> CheckinResult:
    now = clock.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    with tx(write=True) as conn:
        # 1) 지금 체크인 대상이 될 수 있는 예약을 잠금 (시작 10분 전 ~ 종료 전)
        locked = conn.execute(
            """SELECT r.id FROM reservations r
               WHERE r.room_id = ? AND r.start_at <= ? AND r.end_at > ?
                 AND NOT EXISTS (SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id = r.id)
                 AND NOT EXISTS (SELECT 1 FROM simulation_reservations s WHERE s.reservation_id = r.id)
               ORDER BY r.start_at
              """,
            (room["id"], now + CHECKIN_OPENS_BEFORE, now),
        ).fetchall()
        ids = [r["id"] for r in locked]

        # 2) 잠근 뒤 새로 조회: 노쇼 취소 여부, 체크인 여부, 내가 참석자인지
        cands = [] if not ids else conn.execute(
            """SELECT r.*,
                      (SELECT e.name FROM employees e WHERE e.id = r.organizer_id)              AS organizer_name,
                      (EXISTS (SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id = r.id)
                       OR EXISTS (SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id = r.id)) AS cancelled,
                      EXISTS (SELECT 1 FROM checkins c WHERE c.reservation_id = r.id)             AS in_use,
                      EXISTS (SELECT 1 FROM checkins c
                              WHERE c.reservation_id = r.id AND c.employee_id = ?)                    AS mine,
                      EXISTS (SELECT 1 FROM reservation_attendees a
                              WHERE a.reservation_id = r.id AND a.employee_id = ?)                    AS attendee
               FROM reservations r WHERE r.id IN (SELECT value FROM json_each(?)) ORDER BY r.start_at""",
            (employee_id, employee_id, ids),
        ).fetchall()
        live = [c for c in cands if not c["cancelled"]]
        mine = [c for c in live if c["attendee"]]

        if not mine:
            # 지금 체크인할 내 예약은 없음
            cancelled_mine = [c for c in cands if c["cancelled"] and c["attendee"]]
            if cancelled_mine:
                return CheckinResult("too_late", room, reservation=cancelled_mine[0])

            # 오늘 이 회의실의 내 다음 예약 / 누구든 다음 예약
            later = conn.execute(
                """SELECT r.*,
                          EXISTS (SELECT 1 FROM reservation_attendees a
                                  WHERE a.reservation_id = r.id AND a.employee_id = ?) AS attendee
                   FROM reservations r
                   WHERE r.room_id = ? AND r.start_at > ?
                     AND r.start_at < ?
                     AND NOT EXISTS (SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id = r.id)
                     AND NOT EXISTS (SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id = r.id)
                 AND NOT EXISTS (SELECT 1 FROM simulation_reservations s WHERE s.reservation_id = r.id)
                   ORDER BY r.start_at""",
                (employee_id, room["id"], now, tomorrow),
            ).fetchall()
            my_next = next((r for r in later if r["attendee"]), None)
            opens_at = my_next["start_at"] - CHECKIN_OPENS_BEFORE if my_next else None

            # 다른 회의실에 잡힌 내 예약: 지금 체크인할 예약이 있으면 그 회의실로 안내, 없으면 오늘 다음 예약
            elsewhere = conn.execute(
                """SELECT r.*, room_label(ro.name, ro.floor) AS room_name, ro.code AS room_code,
                          (r.start_at <= :opens_at) AS checkin_open
                   FROM reservations r
                   JOIN rooms ro ON ro.id = r.room_id
                   JOIN reservation_attendees a ON a.reservation_id = r.id AND a.employee_id = :emp
                   WHERE r.room_id <> :room AND r.end_at > :now
                     AND r.start_at < :tomorrow
                     AND NOT EXISTS (SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id = r.id)
                     AND NOT EXISTS (SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id = r.id)
                 AND NOT EXISTS (SELECT 1 FROM simulation_reservations s WHERE s.reservation_id = r.id)
                   ORDER BY r.start_at LIMIT 1""",
                {"opens_at": now + CHECKIN_OPENS_BEFORE, "now": now, "tomorrow": tomorrow, "emp": employee_id, "room": room["id"]},
            ).fetchone()

            if elsewhere and elsewhere["checkin_open"] and not my_next:
                # 지금 다른 회의실에서 회의가 있는데 엉뚱한 회의실 QR을 찍음 → 내 회의실로 안내
                return CheckinResult("wrong_room", room, reservation=live[0] if live else None, elsewhere=elsewhere)
            if live:
                # 다른 사람의 예약이 진행 중 (내 예약이 나중에 있으면 그 체크인 시각도 알려줌)
                return CheckinResult("not_attendee", room, reservation=live[0], my_next=my_next, opens_at=opens_at,
                                     elsewhere=elsewhere)
            if my_next:
                return CheckinResult("too_early", room, reservation=my_next, opens_at=opens_at)
            return CheckinResult("no_reservation", room, reservation=later[0] if later else None, elsewhere=elsewhere)

        # 내 예약 중에서 고르기: 첫 체크인이 필요한 예약(유예 시간 안) → 이미 사용 중인 예약 → 늦은 예약
        need_first = [c for c in mine if not c["in_use"] and now < c["start_at"] + NOSHOW_GRACE]
        in_use = [c for c in mine if c["in_use"]]
        if need_first:
            res, kind = need_first[0], "ok"
        elif in_use:
            res = in_use[0]
            kind = "already" if res["mine"] else "joined"
        else:
            return CheckinResult("too_late", room, reservation=mine[0])

        if kind != "already":
            conn.execute(
                """INSERT INTO checkins (reservation_id, employee_id, room_id, checked_in_at, method, source_ip, user_agent)
                   VALUES (?, ?, ?, ?, 'qr', ?, ?)
                   ON CONFLICT (reservation_id, employee_id) DO NOTHING""",
                (res["id"], employee_id, room["id"], now, source_ip, (user_agent or "")[:300]),
            )
        if kind == "ok":
            conn.execute(
                """INSERT INTO notification_log (reservation_id, kind, sent_at) VALUES (?, 'checkin_done', ?)
                   ON CONFLICT DO NOTHING""",
                (res["id"], now),
            )

        receipt = conn.execute(
            """SELECT * FROM v_checkin_receipts WHERE reservation_id = ? AND checkin_id = (
                 SELECT id FROM checkins WHERE reservation_id = ? AND employee_id = ?)""",
            (res["id"], res["id"], employee_id),
        ).fetchone()

        # 첫 체크인이면 예약자에게도 알림 (본인이 예약자가 아닐 때)
        notify = []
        if kind == "ok" and res["organizer_id"] != employee_id:
            notify = conn.execute("SELECT * FROM employees WHERE id = ?", (res["organizer_id"],)).fetchall()

    return CheckinResult(kind, room, reservation=res, receipt=receipt, notify=notify)


# ---------------------------------------------------------------
# 노쇼 처리 (스케줄러가 1분마다 호출)
# ---------------------------------------------------------------

def noshow_enforced_from() -> datetime:
    """노쇼 규칙 적용 시작 시각. 처음 호출될 때 지금 시각으로 기록."""
    with tx(write=True) as conn:
        conn.execute(
            """INSERT INTO system_settings (key, value) VALUES ('noshow_enforced_from', ?)
               ON CONFLICT (key) DO NOTHING""",
            (clock.now().isoformat(),),
        )
        row = conn.execute("SELECT value FROM system_settings WHERE key = 'noshow_enforced_from'").fetchone()
    return datetime.fromisoformat(row["value"]).astimezone(clock.TZ)


def _with_details(conn, reservation_ids: list[int]) -> list[dict]:
    if not reservation_ids:
        return []
    rows = conn.execute(
        """SELECT v.* FROM v_reservations v WHERE v.id IN (SELECT value FROM json_each(?)) ORDER BY v.start_at, v.id""",
        (reservation_ids,),
    ).fetchall()
    att = conn.execute(
        """SELECT a.reservation_id, a.is_organizer, e.*
           FROM reservation_attendees a JOIN employees e ON e.id = a.employee_id
           WHERE a.reservation_id IN (SELECT value FROM json_each(?)) ORDER BY a.is_organizer DESC, e.name""",
        (reservation_ids,),
    ).fetchall()
    for r in rows:
        r["attendees"] = [a for a in att if a["reservation_id"] == r["id"]]
    return rows


def process_noshows() -> list[dict]:
    """시작 후 10분이 지나도 체크인이 없는 진행 중 예약을 자동 취소. 취소한 예약 목록을 돌려줌."""
    now = clock.now()
    enforced_from = noshow_enforced_from()
    with tx(write=True) as conn:
        # 관리자 강제 배정(공사 등)은 시작하면 자동으로 사용중
        conn.execute(
            """INSERT INTO checkins (reservation_id, employee_id, room_id, checked_in_at, method)
               SELECT r.id, r.organizer_id, r.room_id, r.start_at, 'admin'
               FROM admin_blocks b JOIN reservations r ON r.id = b.reservation_id
               WHERE r.start_at <= ? AND r.end_at > ?
                 AND NOT EXISTS (SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id=r.id)
               ON CONFLICT DO NOTHING""",
            (now, now),
        )
        locked = conn.execute(
            """SELECT r.id FROM reservations r
               WHERE r.start_at >= ?
                 AND r.start_at <= ? AND r.end_at > ?
                 AND NOT EXISTS (SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id = r.id)
                     AND NOT EXISTS (SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id = r.id)
                 AND NOT EXISTS (SELECT 1 FROM simulation_reservations s WHERE s.reservation_id = r.id)
                 AND NOT EXISTS (SELECT 1 FROM admin_blocks b WHERE b.reservation_id = r.id)
              """,
            (enforced_from, now - NOSHOW_GRACE, now),
        ).fetchall()
        ids = [r["id"] for r in locked]
        if not ids:
            return []
        # 잠근 뒤 다시 확인: 그 사이 체크인이 들어왔으면 취소하지 않음
        cancelled = conn.execute(
            """INSERT INTO noshow_cancellations (reservation_id, cancelled_at)
               SELECT r.id, ? FROM reservations r
               WHERE r.id IN (SELECT value FROM json_each(?))
                 AND NOT EXISTS (SELECT 1 FROM checkins c WHERE c.reservation_id = r.id)
               ON CONFLICT DO NOTHING
               RETURNING reservation_id""",
            (now, ids),
        ).fetchall()
        cancelled_ids = [r["reservation_id"] for r in cancelled]
        if cancelled_ids:
            conn.execute(
                """INSERT INTO notification_log (reservation_id, kind, sent_at)
                   SELECT value, 'auto_cancelled', ? FROM json_each(?) WHERE 1 ON CONFLICT DO NOTHING""",
                (now, cancelled_ids),
            )
        return _with_details(conn, cancelled_ids)


def due_reminders() -> list[tuple[str, dict]]:
    """보낼 알림 목록 (보낸 것으로 기록까지 함).
       checkin_open   : 시작 10분 전 ~ 시작, 아직 체크인 없음 → 참석자 전원
       noshow_warning : 시작 5분 후 ~ 10분, 아직 체크인 없음 → 참석자 전원
    """
    now = clock.now()
    enforced_from = noshow_enforced_from()
    out: list[tuple[str, dict]] = []
    with tx(write=True) as conn:
        for kind, lo, hi in (
            ("checkin_open", -CHECKIN_OPENS_BEFORE, timedelta(0)),
            ("noshow_warning", NOSHOW_WARNING_AFTER, NOSHOW_GRACE),
        ):
            rows = conn.execute(
                """INSERT INTO notification_log (reservation_id, kind, sent_at)
                   SELECT r.id, :kind, :now FROM reservations r
                   WHERE r.start_at >= :from
                     AND r.start_at <= :latest AND r.start_at > :earliest
                     AND NOT EXISTS (SELECT 1 FROM checkins c WHERE c.reservation_id = r.id)
                     AND NOT EXISTS (SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id = r.id)
                     AND NOT EXISTS (SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id = r.id)
                 AND NOT EXISTS (SELECT 1 FROM simulation_reservations s WHERE s.reservation_id = r.id)
                     AND NOT EXISTS (SELECT 1 FROM admin_blocks b WHERE b.reservation_id = r.id)
                   ON CONFLICT DO NOTHING
                   RETURNING reservation_id""",
                {"kind": kind, "now": now, "from": enforced_from - CHECKIN_OPENS_BEFORE, "latest": now - lo, "earliest": now - hi},
            ).fetchall()
            for r in _with_details(conn, [x["reservation_id"] for x in rows]):
                out.append((kind, r))
    return out
