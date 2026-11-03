-- SQLite schema v1. All stored timestamps are UTC ISO 8601 with microseconds.
BEGIN IMMEDIATE;

CREATE TABLE rooms (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  code        text NOT NULL UNIQUE,
  name        text NOT NULL UNIQUE,
  floor       INTEGER  NOT NULL,
  capacity    INTEGER  NOT NULL CHECK (capacity > 0),
  equipment   TEXT NOT NULL DEFAULT '[]',
  is_active   INTEGER NOT NULL DEFAULT true
);

CREATE TABLE employees (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  emp_no      text NOT NULL UNIQUE,
  name        text NOT NULL,
  email       text NOT NULL UNIQUE,
  department  text NOT NULL,
  position    text NOT NULL
);

CREATE TABLE holidays (
  holiday_date TEXT PRIMARY KEY,
  name         text NOT NULL
);

CREATE TABLE reservations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id       INTEGER  NOT NULL REFERENCES rooms(id),
  title         text NOT NULL,
  organizer_id  INTEGER  NOT NULL REFERENCES employees(id),
  start_at      TEXT NOT NULL,
  end_at        TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (app_now()),
  CHECK (end_at > start_at)
);

CREATE TABLE reservation_attendees (
  reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
  employee_id    INTEGER    NOT NULL REFERENCES employees(id),
  is_organizer   INTEGER NOT NULL DEFAULT false,
  PRIMARY KEY (reservation_id, employee_id)
);

CREATE TABLE slack_users (
  employee_id   INTEGER  PRIMARY KEY REFERENCES employees(id),
  slack_user_id text NOT NULL UNIQUE,
  linked_at     TEXT NOT NULL DEFAULT (app_now())
);

CREATE TABLE room_qr_tokens (
  room_id    INTEGER  PRIMARY KEY REFERENCES rooms(id),
  token      text NOT NULL UNIQUE,
  rotated_at TEXT NOT NULL DEFAULT (app_now())
);

CREATE TABLE checkins (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
  employee_id    INTEGER    NOT NULL REFERENCES employees(id),
  room_id        INTEGER    NOT NULL REFERENCES rooms(id),
  checked_in_at  TEXT NOT NULL DEFAULT (app_now()),
  method         text NOT NULL DEFAULT 'qr',
  source_ip      TEXT,
  user_agent     text,
  UNIQUE (reservation_id, employee_id)
);

CREATE TABLE noshow_cancellations (
  reservation_id    INTEGER PRIMARY KEY REFERENCES reservations(id) ON DELETE CASCADE,
  cancelled_at      TEXT NOT NULL DEFAULT (app_now()),
  legacy_synced_at TEXT
);

CREATE TABLE room_channels (
  room_id         INTEGER  PRIMARY KEY REFERENCES rooms(id),
  channel_id      text NOT NULL UNIQUE,
  panel_ts        text,
  panel_posted_at TEXT,
  panel_hash      text
);

CREATE TABLE slack_images (
  file_id    text PRIMARY KEY,
  kind       text NOT NULL,
  created_at TEXT NOT NULL DEFAULT (app_now())
);

CREATE TABLE system_settings (
  key        text PRIMARY KEY,
  value      text NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (app_now())
);

CREATE TABLE notification_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
  kind           text NOT NULL,
  sent_at        TEXT NOT NULL DEFAULT (app_now()),
  UNIQUE (reservation_id, kind)
);

CREATE TABLE IF NOT EXISTS admin_blocks (
  reservation_id   INTEGER PRIMARY KEY REFERENCES reservations(id) ON DELETE CASCADE,
  reason           text NOT NULL,
  admin_slack_user text NOT NULL,
  created_at       TEXT NOT NULL DEFAULT (app_now())
);

CREATE TABLE IF NOT EXISTS admin_cancellations (
  reservation_id   INTEGER PRIMARY KEY,
  block_id         INTEGER REFERENCES admin_blocks(reservation_id) ON DELETE SET NULL,
  room_id          INTEGER  NOT NULL REFERENCES rooms(id),
  title            text NOT NULL,
  organizer_id     INTEGER  NOT NULL REFERENCES employees(id),
  start_at         TEXT NOT NULL,
  end_at           TEXT NOT NULL,
  attendee_ids     TEXT NOT NULL DEFAULT '[]',
  reason           text NOT NULL,
  cancelled_at     TEXT NOT NULL DEFAULT (app_now()),
  legacy_synced_at TEXT
);

CREATE INDEX reservations_start_idx ON reservations (start_at);

CREATE INDEX reservations_organizer_idx ON reservations (organizer_id);

CREATE INDEX reservation_attendees_employee_idx ON reservation_attendees (employee_id);

CREATE INDEX checkins_room_time_idx ON checkins (room_id, checked_in_at);

CREATE INDEX slack_images_created_idx ON slack_images (created_at);

CREATE INDEX reservations_room_time_idx ON reservations (room_id, start_at);

CREATE INDEX reservations_room_end_idx  ON reservations (room_id, end_at);


CREATE TABLE reservation_cancellations (
 reservation_id INTEGER PRIMARY KEY REFERENCES reservations(id) ON DELETE CASCADE,
 cancelled_at TEXT NOT NULL DEFAULT (app_now()),
 cancelled_by INTEGER NOT NULL REFERENCES employees(id),
 reason TEXT NOT NULL
);

CREATE VIEW v_reservations AS
SELECT r.*, room_label(ro.name,ro.floor) AS room_name,
 ro.floor AS room_floor, ro.code AS room_code, org.name AS organizer_name,
 (SELECT min(c.checked_in_at) FROM checkins c WHERE c.reservation_id=r.id) AS first_checkin_at,
 nc.cancelled_at AS noshow_cancelled_at,
 CASE WHEN uc.reservation_id IS NOT NULL THEN '취소'
 WHEN nc.reservation_id IS NOT NULL THEN '노쇼 취소'
 WHEN r.end_at <= app_now() THEN '종료'
 WHEN EXISTS (SELECT 1 FROM checkins c WHERE c.reservation_id=r.id) THEN '사용중'
 WHEN r.start_at > app_now() THEN '예정'
 WHEN app_now() < shift_time(r.start_at,600) THEN '체크인 대기'
 ELSE '노쇼 처리 대기' END AS state
FROM reservations r JOIN rooms ro ON ro.id=r.room_id
JOIN employees org ON org.id=r.organizer_id
LEFT JOIN noshow_cancellations nc ON nc.reservation_id=r.id
LEFT JOIN reservation_cancellations uc ON uc.reservation_id=r.id;

CREATE VIEW v_checkin_receipts AS
SELECT c.id AS checkin_id,c.reservation_id,e.name AS checked_in_by,
 e.department AS checked_in_by_department,room_label(ro.name,ro.floor) AS room_name,
 c.checked_in_at,r.title,r.start_at,r.end_at,org.name AS organizer_name,c.method
FROM checkins c JOIN employees e ON e.id=c.employee_id JOIN rooms ro ON ro.id=c.room_id
JOIN reservations r ON r.id=c.reservation_id JOIN employees org ON org.id=r.organizer_id;

CREATE VIEW v_room_status_now AS
SELECT ro.id AS room_id,ro.code,room_label(ro.name,ro.floor) AS room_name,
 ro.name AS short_name,ro.floor,ro.capacity,
 CASE WHEN cur.id IS NULL THEN
 CASE WHEN nxt.start_at <= shift_time(app_now(),600) THEN '곧 시작' ELSE '비어있음' END
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

CREATE TRIGGER reservations_no_overlap_insert BEFORE INSERT ON reservations
WHEN EXISTS(SELECT 1 FROM reservations r WHERE r.room_id=NEW.room_id
 AND r.start_at<NEW.end_at AND r.end_at>NEW.start_at
 AND NOT EXISTS(SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id=r.id)
 AND NOT EXISTS(SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id=r.id))
BEGIN SELECT RAISE(ABORT,'room reservation overlap'); END;
CREATE TRIGGER reservations_no_overlap_update BEFORE UPDATE OF room_id,start_at,end_at ON reservations
WHEN EXISTS(SELECT 1 FROM reservations r WHERE r.id<>NEW.id AND r.room_id=NEW.room_id
 AND r.start_at<NEW.end_at AND r.end_at>NEW.start_at
 AND NOT EXISTS(SELECT 1 FROM noshow_cancellations n WHERE n.reservation_id=r.id)
 AND NOT EXISTS(SELECT 1 FROM reservation_cancellations n WHERE n.reservation_id=r.id))
BEGIN SELECT RAISE(ABORT,'room reservation overlap'); END;
PRAGMA user_version=1;
COMMIT;
