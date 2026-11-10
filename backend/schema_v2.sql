BEGIN IMMEDIATE;
CREATE TABLE simulation_batches (
 id TEXT PRIMARY KEY,
 start_day TEXT NOT NULL,
 days INTEGER NOT NULL,
 excluded_employee_ids TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (app_now())
);
CREATE TABLE simulation_reservations (
 reservation_id INTEGER PRIMARY KEY REFERENCES reservations(id) ON DELETE CASCADE,
 batch_id TEXT NOT NULL REFERENCES simulation_batches(id)
);
CREATE INDEX simulation_batch_idx ON simulation_reservations(batch_id);

DROP VIEW v_reservations;
CREATE VIEW v_reservations AS
SELECT r.*, EXISTS(SELECT 1 FROM simulation_reservations s WHERE s.reservation_id=r.id) AS is_simulated, room_label(ro.name,ro.floor) AS room_name,
 ro.floor AS room_floor, ro.code AS room_code, org.name AS organizer_name,
 (SELECT min(c.checked_in_at) FROM checkins c WHERE c.reservation_id=r.id) AS first_checkin_at,
 nc.cancelled_at AS noshow_cancelled_at,
 CASE WHEN uc.reservation_id IS NOT NULL THEN '취소'
 WHEN nc.reservation_id IS NOT NULL THEN '노쇼 취소'
 WHEN r.end_at <= app_now() THEN '종료'
 WHEN r.start_at <= app_now() AND EXISTS(SELECT 1 FROM simulation_reservations s WHERE s.reservation_id=r.id) THEN '가상 일정'
 WHEN EXISTS (SELECT 1 FROM checkins c WHERE c.reservation_id=r.id) THEN '사용중'
 WHEN r.start_at > app_now() THEN '예정'
 WHEN app_now() < shift_time(r.start_at,600) THEN '체크인 대기'
 ELSE '노쇼 처리 대기' END AS state
FROM reservations r JOIN rooms ro ON ro.id=r.room_id
JOIN employees org ON org.id=r.organizer_id
LEFT JOIN noshow_cancellations nc ON nc.reservation_id=r.id
LEFT JOIN reservation_cancellations uc ON uc.reservation_id=r.id;

DROP VIEW v_room_status_now;
CREATE VIEW v_room_status_now AS
SELECT ro.id AS room_id,ro.code,room_label(ro.name,ro.floor) AS room_name,
 ro.name AS short_name,ro.floor,ro.capacity,
 CASE WHEN cur.id IS NULL THEN
 CASE WHEN nxt.start_at <= shift_time(app_now(),600) THEN '곧 시작' ELSE '비어있음' END
 WHEN EXISTS(SELECT 1 FROM simulation_reservations s WHERE s.reservation_id=cur.id) THEN '가상 일정'
 WHEN EXISTS(SELECT 1 FROM checkins c WHERE c.reservation_id=cur.id) THEN '사용중'
 WHEN app_now() < shift_time(cur.start_at,600) THEN '체크인 대기'
 ELSE '노쇼 처리 대기' END AS state,
 cur.id AS current_reservation_id,cur.title AS current_title,cur.start_at AS current_start_at,
 cur.end_at AS current_end_at,org.name AS current_organizer,
 nxt.id AS next_reservation_id,nxt.title AS next_title,nxt.start_at AS next_start_at
FROM rooms ro
LEFT JOIN reservations cur ON cur.id=(
 SELECT r.id FROM reservations r WHERE r.room_id=ro.id AND r.end_at>app_now()
 AND (r.start_at<=app_now() OR (r.start_at<=shift_time(app_now(),600)
 AND EXISTS(SELECT 1 FROM checkins c WHERE c.reservation_id=r.id)))
 AND NOT EXISTS(SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id=r.id)
 AND NOT EXISTS(SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id=r.id)
 ORDER BY r.start_at DESC,r.id LIMIT 1)
LEFT JOIN employees org ON org.id=cur.organizer_id
LEFT JOIN reservations nxt ON nxt.id=(
 SELECT r.id FROM reservations r WHERE r.room_id=ro.id AND r.start_at>app_now()
 AND NOT EXISTS(SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id=r.id)
 AND NOT EXISTS(SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id=r.id)
 ORDER BY r.start_at,r.id LIMIT 1)
WHERE ro.is_active;

PRAGMA user_version=2;
COMMIT;
