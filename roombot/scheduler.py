"""노쇼 스케줄러: 30초마다 체크인 리마인더 → 경고 → 자동취소 알림, 아침 브리핑."""

import logging
from datetime import datetime, time

from apscheduler.schedulers.background import BackgroundScheduler

from roombot import checkin, clock, messages, rooms
from roombot.config import settings
from roombot.db import tx

log = logging.getLogger(__name__)


def run_once(notifier) -> dict:
    """한 번 실행. 처리 건수를 돌려줌."""
    dms: list[tuple[dict, object]] = []

    cancelled = checkin.process_noshows()
    for r in cancelled:
        msg = messages.dm_auto_cancelled(r)
        dms += [(a, msg) for a in r["attendees"]]
        notifier.channel(messages.channel_room_freed(r))

    reminders = checkin.due_reminders()
    for kind, r in reminders:
        msg = messages.dm_checkin_open(r) if kind == "checkin_open" else messages.dm_noshow_warning(r)
        dms += [(a, msg) for a in r["attendees"]]

    briefs = morning_briefs()
    dms += briefs

    notifier.send_many(dms)

    # 회의실 채널의 체크인 패널: 지금/다음 회의가 바뀐 것만 고침
    if settings.room_channels and getattr(notifier, "client", None) is not None:
        from roombot import room_channels
        room_channels.refresh_all(notifier.client)
    # 홈은 표만 사용하므로 그림은 상세 화면을 요청할 때 만들고, 정리만 시간마다 실행.
    from roombot import slackfiles
    now = clock.now()
    if now.minute == 0 and now.second < 30:
        slackfiles.cleanup()
    if cancelled or reminders or briefs:
        log.info("노쇼 취소 %d건, 리마인더 %d건, 아침 브리핑 %d명 (DM %d개)",
                 len(cancelled), len(reminders), len(briefs), len(dms))
    return {"cancelled": len(cancelled), "reminders": len(reminders), "briefs": len(briefs)}


def morning_briefs() -> list[tuple[dict, object]]:
    """MORNING_BRIEF_AT 시각이 지나면 하루 한 번, 슬랙이 연결된 직원에게 오늘 회의 목록."""
    if not settings.morning_brief_at:
        return []
    now = clock.now()
    at = time.fromisoformat(settings.morning_brief_at)
    if now.time() < at or now.time() >= time(18, 0):
        return []
    key, today = "morning_brief_last", now.date().isoformat()
    with tx(write=True) as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        if row and row["value"] == today:
            return []
        conn.execute("""INSERT INTO system_settings (key, value) VALUES (?, ?)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = app_now()""", (key, today))
    out = []
    for emp in rooms.linked_users():
        meetings = [m for m in rooms.my_reservations(emp["id"], days=0)
                    if not m["is_simulated"] and m["start_at"].date() == now.date() and m["end_at"] > now and m["state"] != "노쇼 취소"]
        if meetings:
            out.append((emp, messages.dm_morning_brief(emp, meetings)))
    return out


def start(notifier, seconds: int = 30) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Seoul")

    def job():
        try:
            run_once(notifier)
        except Exception:
            log.exception("노쇼 처리 중 오류")

    # 첫 실행도 백그라운드에서 바로 (웹·슬랙봇 시작을 막지 않게)
    sched.add_job(job, "interval", seconds=seconds, max_instances=1, coalesce=True, id="noshow",
                  next_run_time=datetime.now())
    sched.start()
    return sched
