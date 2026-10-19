-- 시연용: 오늘 QR 체크인 시스템을 운영했다고 가정한 bot 데이터
--   본 DB(daouoffice)에는 넣지 않고, 시연용 DB에만 사용.
--   오늘 지금(bot.app_now()) 이전에 시작한 예약에 대해
--     - 유예 시간(10분)이 지났으면 85% 체크인, 15% 노쇼 자동취소
--     - 아직 유예 시간 안이면 50% 체크인, 나머지는 체크인 대기
--
-- 예:  PGOPTIONS="-c app.now=2026-10-17T10:03+09" psql -d daouoffice_demo -f db/03_demo_activity.sql

SET TIME ZONE 'Asia/Seoul';

TRUNCATE bot.notification_log, bot.noshow_cancellations, bot.checkins RESTART IDENTITY;

-- 오늘 0시부터 노쇼 규칙을 적용해 온 것으로 기록
INSERT INTO bot.system_settings (key, value)
VALUES ('noshow_enforced_from', date_trunc('day', bot.app_now())::text)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

SELECT setseed(0.1017);

DO $$
DECLARE
  v_now      timestamptz := bot.app_now();
  r          record;
  v_checker  int;
  v_check_at timestamptz;
BEGIN
  FOR r IN
    SELECT res.id, res.room_id, res.start_at
    FROM daou.reservations res
    WHERE (res.start_at AT TIME ZONE 'Asia/Seoul')::date = (v_now AT TIME ZONE 'Asia/Seoul')::date
      AND res.start_at <= v_now
    ORDER BY res.start_at, res.id
  LOOP
    IF (v_now >= r.start_at + bot.noshow_grace() AND random() < 0.85)
       OR (v_now < r.start_at + bot.noshow_grace() AND random() < 0.50) THEN
      -- 참석자 중 누구나 체크인 가능. 예약자가 찍는 경우가 60%
      SELECT a.employee_id INTO v_checker
      FROM daou.reservation_attendees a
      WHERE a.reservation_id = r.id
      ORDER BY (a.is_organizer AND random() < 0.6) DESC, random()
      LIMIT 1;

      -- 체크인 가능 시간(시작 10분 전 ~ 시작 10분 후) 안에서, 지금 시각을 넘지 않게
      v_check_at := least(r.start_at - bot.checkin_opens_before()
                            + (bot.checkin_opens_before() + bot.noshow_grace()) * random(),
                          v_now);

      INSERT INTO bot.checkins (reservation_id, employee_id, room_id, checked_in_at, method)
      VALUES (r.id, v_checker, r.room_id, v_check_at, 'qr');
      INSERT INTO bot.notification_log (reservation_id, kind, sent_at)
      VALUES (r.id, 'checkin_done', v_check_at);

    ELSIF v_now >= r.start_at + bot.noshow_grace() THEN
      INSERT INTO bot.noshow_cancellations (reservation_id, cancelled_at)
      VALUES (r.id, r.start_at + bot.noshow_grace());
      INSERT INTO bot.notification_log (reservation_id, kind, sent_at)
      VALUES (r.id, 'auto_cancelled', r.start_at + bot.noshow_grace());
    END IF;
  END LOOP;
END $$;
