"""로컬 예약 등록·취소·참석자 관리. 기록은 삭제하지 않는다."""

import sqlite3
from datetime import datetime

from backend import clock
from backend.db import tx


def reserve(room_id: int, organizer_id: int, start: datetime, end: datetime,
            title: str, attendee_ids: list[int] | None = None) -> int:
    if not title.strip() or len(title.strip()) > 200:
        raise ValueError('회의 제목은 1~200자로 입력해 주세요.')
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError('시간대가 있는 시작·종료 시각을 확인해 주세요.')
    if start < clock.now():
        raise ValueError('지난 시간은 예약할 수 없어요.')
    people = sorted({organizer_id, *(attendee_ids or [])})
    try:
        with tx(write=True) as conn:
            if not conn.execute('SELECT id FROM rooms WHERE id=? AND is_active', (room_id,)).fetchone():
                raise ValueError('사용 가능한 회의실을 찾지 못했어요.')
            found = conn.execute('SELECT id FROM employees WHERE id IN (SELECT value FROM json_each(?))', (people,)).fetchall()
            if len(found) != len(people):
                raise ValueError('등록된 직원만 참석자로 추가할 수 있어요.')
            rid = conn.execute('INSERT INTO reservations(room_id,organizer_id,start_at,end_at,title) '
                               'VALUES(?,?,?,?,?) RETURNING id', (room_id,organizer_id,start,end,title.strip())).fetchone()['id']
            conn.executemany('INSERT INTO reservation_attendees(reservation_id,employee_id,is_organizer) VALUES(?,?,?)',
                             [(rid, person, person == organizer_id) for person in people])
            return rid
    except sqlite3.IntegrityError as exc:
        if 'room reservation overlap' in str(exc):
            raise ValueError('그 시간에 다른 예약이 있어요. 빈 회의실을 다시 찾아 주세요.') from exc
        raise


def _authorized(conn, reservation_id: int, employee_id: int, is_admin: bool):
    row = conn.execute('SELECT * FROM v_reservations WHERE id=?', (reservation_id,)).fetchone()
    if not row:
        raise ValueError('예약을 찾지 못했어요.')
    if row['organizer_id'] != employee_id and not is_admin:
        raise ValueError('예약자 또는 관리자만 변경할 수 있어요.')
    if row['state'] in ('취소', '노쇼 취소'):
        raise ValueError('이미 취소된 예약이에요.')
    if row['end_at'] <= clock.now():
        raise ValueError('종료된 예약은 변경할 수 없어요.')
    return row


def cancel(reservation_id: int, employee_id: int, *, is_admin: bool = False, reason: str = '예약자 취소'):
    with tx(write=True) as conn:
        _authorized(conn, reservation_id, employee_id, is_admin)
        conn.execute('INSERT INTO reservation_cancellations(reservation_id,cancelled_by,reason) VALUES(?,?,?)',
                     (reservation_id,employee_id,reason))


def add_attendee(reservation_id: int, employee_id: int, attendee_id: int, *, is_admin: bool = False):
    with tx(write=True) as conn:
        _authorized(conn, reservation_id, employee_id, is_admin)
        if not conn.execute('SELECT id FROM employees WHERE id=?', (attendee_id,)).fetchone():
            raise ValueError('등록된 직원을 찾지 못했어요.')
        conn.execute('INSERT INTO reservation_attendees(reservation_id,employee_id,is_organizer) VALUES(?,?,0) '
                     'ON CONFLICT DO NOTHING', (reservation_id,attendee_id))
