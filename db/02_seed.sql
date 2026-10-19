-- 회의실 예약 슬랙봇 - 샘플 데이터 (다우오피스 영역만)
--   기간 : 2026-10-01 ~ 2026-10-31
--   길이 : 30분~2시간이 대부분, 회의실·날짜의 약 12%에는 4~8시간짜리 긴 회의 (워크숍·교육 등)
--   대상 : 평일 09:00~18:00, 공휴일 제외. 단 "오늘"은 요일과 상관없이 시연용으로 포함
--   점유율: 회의실별 하루 30분 슬롯 18개 중 4~6개만 비움 → 약 70%
--   다우오피스에는 예약 건만 있음 (노쇼·취소 상태 없음, 직접 취소한 예약은 DB에 남지 않음)
--   bot 영역(체크인, 노쇼 취소)은 비어 있음. 시연용 데이터는 03_demo_activity.sql
--
-- "오늘"을 바꾸려면 실행 전에:  SET app.now = '2026-10-08 14:05+09';

SET TIME ZONE 'Asia/Seoul';

TRUNCATE bot.system_settings, bot.notification_log, bot.noshow_cancellations, bot.checkins,
         bot.room_qr_tokens, bot.slack_users,
         daou.reservation_attendees, daou.reservations, daou.holidays,
         daou.employees, daou.rooms
RESTART IDENTITY CASCADE;

SELECT setseed(0.2026);

-- ---------------------------------------------------------------
-- 공휴일
-- ---------------------------------------------------------------
INSERT INTO daou.holidays (holiday_date, name) VALUES
  ('2026-10-03', '개천절'),
  ('2026-10-05', '개천절 대체공휴일'),
  ('2026-10-09', '한글날');

-- ---------------------------------------------------------------
-- 회의실 20개
--   건물은 6~10층, 22~25층. 회의실은 9층(9A~9D), 10층(10A~10D),
--   22~25층은 층마다 3개씩 코인 이름 (6~8층에는 회의실 없음)
--   코드는 QR 주소와 /room 명령에 쓰임 (예: /room 9A, /room BTC)
-- ---------------------------------------------------------------
INSERT INTO daou.rooms (code, name, floor, capacity, equipment)
SELECT code, name, floor, cap,
       CASE WHEN cap >= 20 THEN ARRAY['빔프로젝터','화상회의','마이크']
            WHEN cap >= 8  THEN ARRAY['TV','화상회의']
            ELSE ARRAY['TV'] END
FROM (VALUES
  ('9A',   '9A',        9,  4), ('9B',   '9B',        9,  6), ('9C',   '9C',         9,  8), ('9D',  '9D',      9, 12),
  ('10A',  '10A',      10,  4), ('10B',  '10B',      10,  6), ('10C',  '10C',       10,  8), ('10D', '10D',    10, 12),
  ('BTC',  'Bitcoin',  22,  6), ('ETH',  'Ethereum', 22,  8), ('SOL',  'Solana',    22, 12),
  ('XRP',  'Ripple',   23,  6), ('ADA',  'Cardano',  23,  8), ('DOT',  'Polkadot',  23, 12),
  ('AVAX', 'Avalanche',24,  8), ('LINK', 'Chainlink',24, 12), ('POL',  'Polygon',   24, 16),
  ('ATOM', 'Cosmos',   25, 10), ('LTC',  'Litecoin', 25, 20), ('XLM',  'Stellar',   25, 20)
) AS t(code, name, floor, cap);

-- ---------------------------------------------------------------
-- 직원 200명 (이름 중복 없음)
--   동시에 열리는 회의 참석자가 많을 때도 한 사람이 같은 시간에 두 회의에 들어가지 않을 만큼
-- ---------------------------------------------------------------
INSERT INTO daou.employees (emp_no, name, email, department, position)
SELECT format('D%s', lpad(n::text, 4, '0')),
       (ARRAY['김','이','박','최','정','강','조','윤','장','임','한','오','서','신','권','황','안','송','류','홍'])[n % 20 + 1]
       || (ARRAY['민준','서연','지호','하은','도윤','수아','예준','지유','시우','서윤',
                 '주원','하린','지훈','채원','건우','지민','우진','윤서','현우','다은'])[(n / 20 + n * 3) % 20 + 1],
       format('user%s@example.com', lpad(n::text, 3, '0')),
       (ARRAY['경영지원팀','인사팀','재무팀','영업1팀','영업2팀','마케팅팀','개발1팀','개발2팀','디자인팀','고객지원팀'])[(n - 1) % 10 + 1],
       (ARRAY['사원','사원','대리','대리','과장','차장','부장'])[(n * 3) % 7 + 1]
FROM generate_series(1, 200) n;

-- QR 토큰은 DB에 넣지 않음: 앱이 QR_SECRET으로 회의실 코드마다 계산 (DB를 다시 만들어도 인쇄한 QR이 그대로 동작)
--   특정 회의실 QR만 바꾸고 싶으면 bot.room_qr_tokens 에 새 토큰을 넣으면 그 값이 우선함

-- ---------------------------------------------------------------
-- 예약 / 참석자
-- ---------------------------------------------------------------
DO $$
DECLARE
  v_now       timestamptz := bot.app_now();
  v_today     date        := (bot.app_now() AT TIME ZONE 'Asia/Seoul')::date;
  v_titles    text[] := ARRAY['주간 회의','기획 리뷰','1:1 미팅','스프린트 플래닝','고객사 미팅',
                              '디자인 리뷰','채용 면접','예산 검토','장애 회고','신규 입사자 온보딩',
                              '분기 실적 점검','제품 데모','파트너사 협의','코드 리뷰','교육 세션'];
  d           date;
  r           record;
  free_slots  int[];
  long_start  int;
  long_len    int;
  s           int;
  run_end     int;
  len         int;
  v_start     timestamptz;
  v_end       timestamptz;
  v_org       int;
  v_res       bigint;
BEGIN
  FOR d IN SELECT g::date FROM generate_series(date '2026-10-01', date '2026-10-31', interval '1 day') g
  LOOP
    -- 주말·공휴일은 건너뜀. 단 오늘은 시연용으로 항상 생성
    CONTINUE WHEN d <> v_today
              AND (extract(isodow FROM d) >= 6
                   OR EXISTS (SELECT 1 FROM daou.holidays h WHERE h.holiday_date = d));

    FOR r IN SELECT id, capacity FROM daou.rooms ORDER BY id
    LOOP
      -- 18개 슬롯(09:00~18:00, 30분 단위) 중 비워둘 슬롯 4~6개 (평균 5.25개 → 점유율 약 71%)
      -- 약 12%는 4~8시간(8~16슬롯)짜리 긴 회의 하나를 먼저 잡음
      long_start := NULL;
      long_len   := 0;
      IF random() < 0.12 THEN
        long_len   := 8 + floor(random() * 9)::int;
        long_start := floor(random() * (18 - long_len + 1))::int;
      END IF;
      SELECT coalesce(array_agg(x), '{}') INTO free_slots
      FROM (SELECT x FROM generate_series(0, 17) x
            WHERE long_start IS NULL OR x < long_start OR x >= long_start + long_len
            ORDER BY random()
            LIMIT greatest(0, least((ARRAY[4, 5, 6, 6])[1 + floor(random() * 4)::int], 18 - long_len - 1))) t;

      s := 0;
      WHILE s < 18 LOOP
        IF s = ANY (free_slots) THEN
          s := s + 1;
          CONTINUE;
        END IF;

        -- [s, run_end) : 빈 슬롯 사이의 연속 구간
        run_end := s;
        WHILE run_end < 18 AND NOT (run_end = ANY (free_slots)) LOOP
          run_end := run_end + 1;
        END LOOP;

        -- 연속 구간을 30분~2시간짜리 회의들로 나눔
        WHILE s < run_end LOOP
          IF s = long_start THEN
            len := long_len;                     -- 긴 회의 (4~8시간)
          ELSE
            len := least(run_end - s, (ARRAY[1, 2, 2, 2, 3, 4])[1 + floor(random() * 6)::int]);
            IF long_start IS NOT NULL AND s < long_start AND s + len > long_start THEN
              len := long_start - s;             -- 긴 회의 자리를 침범하지 않게
            END IF;
          END IF;
          v_start := (d + time '09:00' + make_interval(mins => s * 30)) AT TIME ZONE 'Asia/Seoul';
          v_end   := v_start + make_interval(mins => len * 30);
          s       := s + len;

          -- 예약자와 참석자는 그 시간에 다른 회의가 없는 사람 중에서 고름
          SELECT e.id INTO v_org
          FROM daou.employees e
          WHERE NOT EXISTS (
            SELECT 1 FROM daou.reservation_attendees a
            JOIN daou.reservations x ON x.id = a.reservation_id
            WHERE a.employee_id = e.id
              AND tstzrange(x.start_at, x.end_at) && tstzrange(v_start, v_end))
          ORDER BY random()
          LIMIT 1;

          INSERT INTO daou.reservations (room_id, title, organizer_id, start_at, end_at, created_at)
          VALUES (r.id, v_titles[1 + floor(random() * array_length(v_titles, 1))::int], v_org,
                  v_start, v_end,
                  least(v_start - make_interval(days => 2 + floor(random() * 12)::int),
                        v_now - interval '1 hour'))
          RETURNING id INTO v_res;

          -- 참석자: 예약자 + 1~7명 (수용인원 이내)
          INSERT INTO daou.reservation_attendees (reservation_id, employee_id, is_organizer)
          VALUES (v_res, v_org, true);

          INSERT INTO daou.reservation_attendees (reservation_id, employee_id, is_organizer)
          SELECT v_res, e.id, false
          FROM daou.employees e
          WHERE e.id <> v_org
            AND NOT EXISTS (
              SELECT 1 FROM daou.reservation_attendees a
              JOIN daou.reservations x ON x.id = a.reservation_id
              WHERE a.employee_id = e.id
                AND tstzrange(x.start_at, x.end_at) && tstzrange(v_start, v_end))
          ORDER BY random()
          LIMIT 1 + floor(random() * least(r.capacity - 1, 7))::int;
        END LOOP;
      END LOOP;
    END LOOP;
  END LOOP;
END $$;
