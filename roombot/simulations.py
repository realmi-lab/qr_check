"""명시적으로 요청한 가상 일정 생성·삭제. 알림·노쇼·실제 통계에서 제외한다."""

import argparse
from datetime import date, datetime, time, timedelta
import json
import random
import uuid

from roombot import clock
from roombot.db import tx

TITLES = ['팀 미팅', '프로젝트 리뷰', '기획 회의', '개발 점검', '디자인 리뷰', '주간 공유', '업무 협의', '운영 점검']


def seed_week(excluded: list[int], start_day: date, days: int = 7, per_room: int = 4) -> dict:
    if not excluded or not 1 <= days <= 31 or not 1 <= per_room <= 6:
        raise ValueError('제외할 직원과 1~31일, 회의실당 1~6건을 지정하세요.')
    batch = uuid.uuid4().hex
    rng = random.Random(batch)
    counts = {}
    with tx(write=True) as conn:
        all_people = {row['id'] for row in conn.execute('SELECT id FROM employees').fetchall()}
        if not set(excluded) <= all_people:
            raise ValueError('제외할 직원 정보를 확인하세요.')
        people = sorted(all_people - set(excluded))
        rooms = conn.execute('SELECT * FROM rooms WHERE is_active ORDER BY floor,id').fetchall()
        conn.execute('INSERT INTO simulation_batches(id,start_day,days,excluded_employee_ids) VALUES(?,?,?,?)',
                     (batch,start_day,days,excluded))
        for offset in range(days):
            day = start_day + timedelta(days=offset)
            if day.isoweekday() >= 6 or conn.execute('SELECT 1 FROM holidays WHERE holiday_date=?',(day,)).fetchone():
                continue
            counts[day.isoformat()] = 0
            for room in rooms:
                candidates = [(minute,duration) for minute in range(9*60,18*60,30) for duration in (30,60,90)
                              if minute+duration<=18*60 and not (minute<13*60 and minute+duration>12*60)]
                rng.shuffle(candidates)
                inserted = 0
                for minute,duration in candidates:
                    if inserted >= per_room:
                        break
                    start = datetime.combine(day,time(),clock.TZ)+timedelta(minutes=minute)
                    end = start+timedelta(minutes=duration)
                    if start <= clock.now():
                        continue
                    overlapping = conn.execute('SELECT r.id FROM reservations r WHERE r.room_id=? AND r.start_at<? AND r.end_at>? '
                        'AND NOT EXISTS(SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id=r.id) '
                        'AND NOT EXISTS(SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id=r.id)',
                        (room['id'],end,start)).fetchone()
                    if overlapping:
                        continue
                    busy = {row['employee_id'] for row in conn.execute(
                        'SELECT a.employee_id FROM reservation_attendees a JOIN reservations r ON r.id=a.reservation_id '
                        'WHERE r.start_at<? AND r.end_at>? '
                        'AND NOT EXISTS(SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id=r.id) '
                        'AND NOT EXISTS(SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id=r.id)',(end,start)).fetchall()}
                    available = [person for person in people if person not in busy]
                    if not available:
                        continue
                    count = rng.randint(1,min(room['capacity'],8,len(available)))
                    attendees = rng.sample(available,count)
                    rid = conn.execute('INSERT INTO reservations(room_id,title,organizer_id,start_at,end_at) VALUES(?,?,?,?,?) RETURNING id',
                                       (room['id'],'[가상] '+rng.choice(TITLES),attendees[0],start,end)).fetchone()['id']
                    conn.executemany('INSERT INTO reservation_attendees(reservation_id,employee_id,is_organizer) VALUES(?,?,?)',
                                     [(rid,person,person==attendees[0]) for person in attendees])
                    conn.execute('INSERT INTO simulation_reservations(reservation_id,batch_id) VALUES(?,?)',(rid,batch))
                    inserted += 1
                    counts[day.isoformat()] += 1
    return {'batch':batch,'excluded_employee_ids':excluded,'start_day':start_day.isoformat(),'days':days,
            'by_day':counts,'reservations':sum(counts.values())}


def clear_batch(batch: str) -> int:
    with tx(write=True) as conn:
        count = conn.execute('SELECT count(*) AS n FROM simulation_reservations WHERE batch_id=?',(batch,)).fetchone()['n']
        conn.execute('DELETE FROM reservations WHERE id IN (SELECT reservation_id FROM simulation_reservations WHERE batch_id=?)',(batch,))
        conn.execute('DELETE FROM simulation_batches WHERE id=?',(batch,))
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exclude-employee',type=int,action='append')
    parser.add_argument('--start',type=date.fromisoformat,default=clock.now().date()+timedelta(days=1))
    parser.add_argument('--days',type=int,default=7)
    parser.add_argument('--per-room',type=int,default=4)
    parser.add_argument('--clear-batch')
    args = parser.parse_args()
    if args.clear_batch:
        print(json.dumps({'deleted':clear_batch(args.clear_batch)}))
    else:
        if not args.exclude_employee:
            parser.error('--exclude-employee로 본인 직원을 명시하세요.')
        print(json.dumps(seed_week(args.exclude_employee,args.start,args.days,args.per_room),ensure_ascii=False))


if __name__ == '__main__':
    main()
