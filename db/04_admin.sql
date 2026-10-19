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
