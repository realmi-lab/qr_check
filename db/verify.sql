-- 샘플 데이터 검증용 쿼리
SET TIME ZONE 'Asia/Seoul';

\echo '== 1. 기본 건수 =='
SELECT (SELECT count(*) FROM daou.rooms)                 AS rooms,
       (SELECT count(*) FROM daou.employees)             AS employees,
       (SELECT count(*) FROM daou.reservations)          AS reservations,
       (SELECT count(*) FROM daou.reservation_attendees) AS attendees,
       (SELECT count(*) FROM bot.checkins)               AS checkins,
       (SELECT count(*) FROM bot.noshow_cancellations)   AS noshow_cancels;

\echo '== 2. 날짜별 점유율 (09~18시 기준) =='
SELECT (r.start_at AT TIME ZONE 'Asia/Seoul')::date            AS day,
       to_char((r.start_at AT TIME ZONE 'Asia/Seoul')::date, 'Dy') AS dow,
       count(*)                                                  AS meetings,
       round(100.0 * sum(extract(epoch FROM r.end_at - r.start_at) / 60)
             / ((SELECT count(*) FROM daou.rooms) * 540), 1)     AS occupancy_pct
FROM daou.reservations r
GROUP BY 1
ORDER BY 1;

\echo '== 3. 전체 점유율 =='
SELECT round(100.0 * sum(extract(epoch FROM end_at - start_at) / 60)
             / ((SELECT count(*) FROM daou.rooms) * 540
                * (SELECT count(DISTINCT (start_at AT TIME ZONE 'Asia/Seoul')::date) FROM daou.reservations)), 1)
       AS occupancy_pct
FROM daou.reservations;

\echo '== 4. 지금 시각 기준 예약 상태 (계산값) =='
SELECT bot.app_now() AS now;
SELECT CASE WHEN (start_at AT TIME ZONE 'Asia/Seoul')::date < (bot.app_now() AT TIME ZONE 'Asia/Seoul')::date THEN '1.지난 날'
            WHEN (start_at AT TIME ZONE 'Asia/Seoul')::date = (bot.app_now() AT TIME ZONE 'Asia/Seoul')::date THEN '2.오늘'
            ELSE '3.이후' END AS period,
       state,
       count(*)
FROM bot.v_reservations
GROUP BY 1, 2
ORDER BY 1, 2;

\echo '== 5. 정합성 검사 (모두 0이어야 정상) =='
SELECT
  (SELECT count(*) FROM bot.checkins c
    WHERE NOT EXISTS (SELECT 1 FROM daou.reservation_attendees a
                      WHERE a.reservation_id = c.reservation_id AND a.employee_id = c.employee_id)) AS checkin_by_non_attendee,
  (SELECT count(*) FROM bot.checkins c
    JOIN daou.reservations r ON r.id = c.reservation_id
    WHERE c.checked_in_at <  r.start_at - bot.checkin_opens_before()
       OR c.checked_in_at >= r.start_at + bot.noshow_grace()
       OR c.checked_in_at >  bot.app_now())                                            AS checkin_outside_window,
  (SELECT count(*) FROM bot.noshow_cancellations n
    JOIN bot.checkins c ON c.reservation_id = n.reservation_id)                         AS checkin_and_noshow,
  (SELECT count(*) FROM bot.checkins c
    JOIN daou.reservations r ON r.id = c.reservation_id
    WHERE c.room_id <> r.room_id)                                                       AS checkin_wrong_room,
  (SELECT count(*) FROM (SELECT r.id FROM daou.reservations r
                         JOIN daou.rooms ro ON ro.id = r.room_id
                         JOIN daou.reservation_attendees a ON a.reservation_id = r.id
                         GROUP BY r.id, ro.capacity HAVING count(*) > ro.capacity) t)        AS over_capacity,
  (SELECT count(*) FROM daou.reservation_attendees a
    JOIN daou.reservations r  ON r.id  = a.reservation_id
    JOIN daou.reservation_attendees a2 ON a2.employee_id = a.employee_id AND a2.reservation_id > a.reservation_id
    JOIN daou.reservations r2 ON r2.id = a2.reservation_id
    WHERE tstzrange(r.start_at, r.end_at) && tstzrange(r2.start_at, r2.end_at))        AS person_double_booked,
  (SELECT count(*) - count(DISTINCT name) FROM daou.employees)                         AS duplicate_names;

\echo '== 6. 체크인 확인 정보 샘플 (체크인한 사람 / 회의실명 / 체크인 시간) =='
SELECT checked_in_by, checked_in_by_department, room_name,
       to_char(checked_in_at, 'MM-DD HH24:MI') AS checked_in_at,
       title,
       to_char(start_at, 'HH24:MI') || '~' || to_char(end_at, 'HH24:MI') AS time_range
FROM bot.v_checkin_receipts
ORDER BY checked_in_at DESC
LIMIT 5;

\echo '== 7. 지금 회의실 현황 =='
SELECT room_name, state, current_title, current_organizer,
       to_char(current_end_at, 'HH24:MI') AS until,
       to_char(next_start_at, 'MM-DD HH24:MI') AS next_start
FROM bot.v_room_status_now
ORDER BY room_id;
