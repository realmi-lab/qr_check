-- 회의실 예약 슬랙봇 - 샘플 DB 스키마
--   daou : 다우오피스가 가진 데이터. 회의실·직원·예약(참석자 포함)만 있음.
--          예약 상태(노쇼, 취소 등)는 없고, 직접 취소한 예약은 DB에 남지 않음.
--          나중에 실제 다우오피스 API/DB로 교체되는 부분.
--   bot  : 우리 슬랙봇이 직접 관리하는 데이터 (QR 체크인, 노쇼 자동취소, 알림 기록 등)
--          회의실 상태(사용중/체크인 대기 등)는 저장하지 않고 예약 + 체크인 기록으로 계산.

SET TIME ZONE 'Asia/Seoul';

CREATE EXTENSION IF NOT EXISTS btree_gist;

DROP SCHEMA IF EXISTS bot CASCADE;
DROP SCHEMA IF EXISTS daou CASCADE;
CREATE SCHEMA daou;
CREATE SCHEMA bot;

-- ---------------------------------------------------------------
-- 현재 시각 (가상 시계)
--   SET app.now = '2026-10-08 14:05+09'; 처럼 지정하면 그 시각을 "지금"으로 사용.
--   지정하지 않으면 실제 now().
-- ---------------------------------------------------------------
CREATE FUNCTION bot.app_now() RETURNS timestamptz
LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('app.now', true), '')::timestamptz, now())
$$;

-- 노쇼 규칙: 체크인은 시작 10분 전부터 가능, 시작 후 10분 안에 참석자 누구도 체크인하지 않으면 자동 취소
CREATE FUNCTION bot.checkin_opens_before() RETURNS interval LANGUAGE sql IMMUTABLE AS $$ SELECT interval '10 minutes' $$;
CREATE FUNCTION bot.noshow_grace()         RETURNS interval LANGUAGE sql IMMUTABLE AS $$ SELECT interval '10 minutes' $$;

-- ===============================================================
-- daou : 다우오피스 영역
-- ===============================================================

CREATE TABLE daou.rooms (
  id          serial PRIMARY KEY,
  code        text NOT NULL UNIQUE,          -- R01 ~ R20
  name        text NOT NULL UNIQUE,          -- 예: 3층 한라
  floor       int  NOT NULL,
  capacity    int  NOT NULL CHECK (capacity > 0),
  equipment   text[] NOT NULL DEFAULT '{}',
  is_active   boolean NOT NULL DEFAULT true
);

CREATE TABLE daou.employees (
  id          serial PRIMARY KEY,
  emp_no      text NOT NULL UNIQUE,
  name        text NOT NULL,
  email       text NOT NULL UNIQUE,          -- 슬랙 사용자와 매칭하는 키
  department  text NOT NULL,
  position    text NOT NULL
);

CREATE TABLE daou.holidays (
  holiday_date date PRIMARY KEY,
  name         text NOT NULL
);

CREATE TABLE daou.reservations (
  id            bigserial PRIMARY KEY,
  room_id       int  NOT NULL REFERENCES daou.rooms(id),
  title         text NOT NULL,
  organizer_id  int  NOT NULL REFERENCES daou.employees(id),
  start_at      timestamptz NOT NULL,
  end_at        timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (end_at > start_at),
  -- 같은 회의실 같은 시간대 중복 예약 금지
  CONSTRAINT reservations_no_overlap EXCLUDE USING gist (
    room_id WITH =,
    tstzrange(start_at, end_at) WITH &&
  )
);
CREATE INDEX reservations_start_idx ON daou.reservations (start_at);
CREATE INDEX reservations_organizer_idx ON daou.reservations (organizer_id);

CREATE TABLE daou.reservation_attendees (
  reservation_id bigint NOT NULL REFERENCES daou.reservations(id) ON DELETE CASCADE,
  employee_id    int    NOT NULL REFERENCES daou.employees(id),
  is_organizer   boolean NOT NULL DEFAULT false,
  PRIMARY KEY (reservation_id, employee_id)
);
CREATE INDEX reservation_attendees_employee_idx ON daou.reservation_attendees (employee_id);

-- ===============================================================
-- bot : 슬랙봇 영역
-- ===============================================================

-- 슬랙 사용자 <-> 직원 연결 (Slack 로그인 시 이메일로 매칭해 저장)
CREATE TABLE bot.slack_users (
  employee_id   int  PRIMARY KEY REFERENCES daou.employees(id),
  slack_user_id text NOT NULL UNIQUE,
  linked_at     timestamptz NOT NULL DEFAULT now()
);

-- 회의실에 붙이는 QR 안의 서명 토큰
CREATE TABLE bot.room_qr_tokens (
  room_id    int  PRIMARY KEY REFERENCES daou.rooms(id),
  token      text NOT NULL UNIQUE,
  rotated_at timestamptz NOT NULL DEFAULT now()
);

-- QR 체크인 기록: 누가 / 어느 회의실에 / 언제 체크인했는지
CREATE TABLE bot.checkins (
  id             bigserial PRIMARY KEY,
  reservation_id bigint NOT NULL REFERENCES daou.reservations(id) ON DELETE CASCADE,
  employee_id    int    NOT NULL REFERENCES daou.employees(id),
  room_id        int    NOT NULL REFERENCES daou.rooms(id),
  checked_in_at  timestamptz NOT NULL DEFAULT now(),
  method         text NOT NULL DEFAULT 'qr',
  source_ip      inet,
  user_agent     text,
  UNIQUE (reservation_id, employee_id)
);
CREATE INDEX checkins_room_time_idx ON bot.checkins (room_id, checked_in_at);

-- 노쇼 자동취소 기록 (우리 시스템이 취소한 예약)
--   synced_to_daou_at : 실제 다우오피스에 취소를 반영한 시각 (연동 전까지는 NULL)
CREATE TABLE bot.noshow_cancellations (
  reservation_id    bigint PRIMARY KEY REFERENCES daou.reservations(id) ON DELETE CASCADE,
  cancelled_at      timestamptz NOT NULL DEFAULT now(),
  synced_to_daou_at timestamptz
);

-- 회의실마다 슬랙 채널 (QR을 찍으면 이 채널이 열리고, 고정된 체크인 패널의 버튼으로 체크인)
CREATE TABLE bot.room_channels (
  room_id         int  PRIMARY KEY REFERENCES daou.rooms(id),
  channel_id      text NOT NULL UNIQUE,
  panel_ts        text,                 -- 체크인 패널 메시지
  panel_posted_at timestamptz,
  panel_hash      text                  -- 내용이 바뀔 때만 고침
);

-- 슬랙에 올린 그림 (30일 지나면 슬랙에서 지움)
CREATE TABLE bot.slack_images (
  file_id    text PRIMARY KEY,
  kind       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON bot.slack_images (created_at);

-- 시스템 설정
--   noshow_enforced_from : 이 시각 이후 시작하는 예약부터 노쇼 규칙 적용 (스케줄러 첫 실행 시 자동 기록)
--                          → 시스템을 처음 켰을 때 이미 진행 중인 회의가 한꺼번에 취소되지 않게
CREATE TABLE bot.system_settings (
  key        text PRIMARY KEY,
  value      text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- 리마인더/경고/자동취소 알림 중복 발송 방지
CREATE TABLE bot.notification_log (
  id             bigserial PRIMARY KEY,
  reservation_id bigint NOT NULL REFERENCES daou.reservations(id) ON DELETE CASCADE,
  kind           text NOT NULL,   -- checkin_open, noshow_warning, auto_cancelled, checkin_done
  sent_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (reservation_id, kind)
);

-- ===============================================================
-- 인덱스 (실제 다우오피스처럼 예약이 몇 년치 쌓여도 회의실·시간으로 바로 찾게)
-- ===============================================================
CREATE INDEX reservations_room_time_idx ON daou.reservations (room_id, start_at);
CREATE INDEX reservations_room_end_idx  ON daou.reservations (room_id, end_at);

-- ===============================================================
-- 표시용 함수 / 뷰
-- ===============================================================

-- 회의실 표시 이름: 9A, 10C 처럼 층이 이름에 있으면 그대로, 코인 이름 회의실은 층을 붙임 → "Bitcoin (22층)"
CREATE FUNCTION bot.room_label(name text, floor int) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN name ~ '^[0-9]' THEN name ELSE name || ' (' || floor || '층)' END
$$;

-- 예약 + 지금 시각 기준 상태 (상태는 저장하지 않고 계산)
--   예정 / 체크인 대기 / 사용중 / 노쇼 처리 대기 / 노쇼 취소 / 종료
CREATE VIEW bot.v_reservations AS
SELECT r.*,
       bot.room_label(ro.name, ro.floor) AS room_name,
       ro.floor                   AS room_floor,
       ro.code                    AS room_code,
       org.name                   AS organizer_name,
       ck.first_checkin_at,
       nc.cancelled_at            AS noshow_cancelled_at,
       CASE
         WHEN nc.reservation_id IS NOT NULL          THEN '노쇼 취소'
         WHEN r.end_at   <= bot.app_now()            THEN '종료'
         WHEN ck.first_checkin_at IS NOT NULL        THEN '사용중'   -- 시작 전에 미리 체크인해도 사용중
         WHEN r.start_at >  bot.app_now()            THEN '예정'
         WHEN bot.app_now() < r.start_at + bot.noshow_grace() THEN '체크인 대기'
         ELSE '노쇼 처리 대기'
       END                        AS state
FROM daou.reservations r
JOIN daou.rooms     ro  ON ro.id  = r.room_id
JOIN daou.employees org ON org.id = r.organizer_id
LEFT JOIN LATERAL (
  SELECT min(c.checked_in_at) AS first_checkin_at
  FROM bot.checkins c WHERE c.reservation_id = r.id
) ck ON true
LEFT JOIN bot.noshow_cancellations nc ON nc.reservation_id = r.id;

-- 체크인 확인 정보: QR 체크인 후 화면과 슬랙 메시지에 보여줄 내용
-- (체크인한 사람, 회의실명, 체크인 시간 + 회의 정보)
CREATE VIEW bot.v_checkin_receipts AS
SELECT c.id              AS checkin_id,
       c.reservation_id,
       e.name            AS checked_in_by,
       e.department      AS checked_in_by_department,
       bot.room_label(ro.name, ro.floor) AS room_name,
       c.checked_in_at,
       r.title,
       r.start_at,
       r.end_at,
       org.name          AS organizer_name,
       c.method
FROM bot.checkins c
JOIN daou.employees    e   ON e.id   = c.employee_id
JOIN daou.rooms        ro  ON ro.id  = c.room_id
JOIN daou.reservations r   ON r.id   = c.reservation_id
JOIN daou.employees    org ON org.id = r.organizer_id;

-- 지금 회의실 현황 (/room now)
--   노쇼 취소된 예약은 회의실을 차지하지 않음
--   지금 시각에 걸친 예약만 먼저 골라낸 뒤 상태를 계산 (예약이 많이 쌓여도 회의실마다 한두 건만 봄)
CREATE VIEW bot.v_room_status_now AS
SELECT ro.id   AS room_id,
       ro.code,
       bot.room_label(ro.name, ro.floor) AS room_name,
       ro.name AS short_name,
       ro.floor,
       ro.capacity,
       CASE
         WHEN cur.id IS NULL THEN
           CASE WHEN nxt.start_at <= bot.app_now() + bot.checkin_opens_before() THEN '곧 시작' ELSE '비어있음' END
         WHEN cur.first_checkin_at IS NOT NULL THEN '사용중'
         WHEN bot.app_now() < cur.start_at + bot.noshow_grace() THEN '체크인 대기'
         ELSE '노쇼 처리 대기'
       END            AS state,
       cur.id         AS current_reservation_id,
       cur.title      AS current_title,
       cur.start_at   AS current_start_at,
       cur.end_at     AS current_end_at,
       org.name       AS current_organizer,
       nxt.id         AS next_reservation_id,
       nxt.title      AS next_title,
       nxt.start_at   AS next_start_at
FROM daou.rooms ro
LEFT JOIN LATERAL (
  SELECT r.id, r.title, r.start_at, r.end_at, r.organizer_id,
         (SELECT min(c.checked_in_at) FROM bot.checkins c WHERE c.reservation_id = r.id) AS first_checkin_at
  FROM daou.reservations r
  WHERE r.room_id = ro.id
    AND r.end_at > bot.app_now()
    AND (r.start_at <= bot.app_now()
         -- 시작 10분 전부터 미리 체크인했으면 그 회의가 '지금' 회의 (사용중)
         OR (r.start_at - bot.checkin_opens_before() <= bot.app_now()
             AND EXISTS (SELECT 1 FROM bot.checkins c WHERE c.reservation_id = r.id)))
    AND NOT EXISTS (SELECT 1 FROM bot.noshow_cancellations n WHERE n.reservation_id = r.id)
  -- 앞 회의가 끝나기 전에 다음 회의가 미리 체크인했으면(앞 회의가 일찍 끝남) 다음 회의를 '지금'으로
  ORDER BY r.start_at DESC
  LIMIT 1
) cur ON true
LEFT JOIN daou.employees org ON org.id = cur.organizer_id
LEFT JOIN LATERAL (
  SELECT r.id, r.title, r.start_at
  FROM daou.reservations r
  WHERE r.room_id = ro.id
    AND r.start_at > bot.app_now()
    AND NOT EXISTS (SELECT 1 FROM bot.noshow_cancellations n WHERE n.reservation_id = r.id)
  ORDER BY r.start_at
  LIMIT 1
) nxt ON true
WHERE ro.is_active;

-- 관리자 강제 배정 (/룸 qwerasdf): 공사 등으로 회의실을 막고, 겹치는 예약은 취소 후 통보
--   관리자가 넣은 예약은 노쇼·리마인더 대상이 아님 (시작하면 자동으로 사용중)
CREATE TABLE IF NOT EXISTS bot.admin_blocks (
  reservation_id   bigint PRIMARY KEY REFERENCES daou.reservations(id) ON DELETE CASCADE,
  reason           text NOT NULL,
  admin_slack_user text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- 강제 배정으로 취소된 예약 (다우오피스에서 취소하면 예약이 사라지므로 내용을 남겨 둠)
CREATE TABLE IF NOT EXISTS bot.admin_cancellations (
  reservation_id   bigint PRIMARY KEY,           -- 원래 다우오피스 예약 번호
  block_id         bigint REFERENCES bot.admin_blocks(reservation_id) ON DELETE SET NULL,
  room_id          int  NOT NULL REFERENCES daou.rooms(id),
  title            text NOT NULL,
  organizer_id     int  NOT NULL REFERENCES daou.employees(id),
  start_at         timestamptz NOT NULL,
  end_at           timestamptz NOT NULL,
  attendee_ids     int[] NOT NULL,
  reason           text NOT NULL,
  cancelled_at     timestamptz NOT NULL DEFAULT now(),
  synced_to_daou_at timestamptz                  -- 실제 다우오피스에 취소를 반영한 시각 (연동 전까지는 NULL)
);
