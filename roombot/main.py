"""전체 실행: QR 체크인 웹 + 노쇼 스케줄러 + 슬랙봇(토큰이 있을 때).

  uv run python -m roombot.main
"""

import logging
import os
import threading
import time

import uvicorn

from roombot import clock, scheduler, web
from roombot.config import settings
from roombot.notifier import default_notifier

log = logging.getLogger("roombot")

SOCKET_CHECK_EVERY = 60   # 슬랙 연결 확인 주기(초)
SOCKET_FAIL_LIMIT = 3     # 연속으로 끊겨 있으면 서버를 다시 시작 (launchd가 다시 켬)


def watch_slack(handler) -> None:
    """슬랙 연결이 3분 넘게 끊겨 있으면 프로세스를 끝냄 → launchd(KeepAlive)가 새로 켜서 깨끗하게 다시 연결."""
    bad = 0
    while True:
        time.sleep(SOCKET_CHECK_EVERY)
        try:
            connected = handler.client.is_connected()
        except Exception:
            connected = False
        bad = 0 if connected else bad + 1
        if bad:
            log.warning("슬랙 연결 끊김 %d/%d", bad, SOCKET_FAIL_LIMIT)
        if bad >= SOCKET_FAIL_LIMIT:
            log.error("슬랙 연결이 %d분째 끊겨 있어 서버를 다시 시작함", bad)
            from roombot import image_host
            image_host.stop()
            os._exit(1)


def notify_url_change(client) -> None:
    """휴대폰 체크인 주소가 바뀌었으면 관리자에게 슬랙 DM (Redirect URL·QR을 바꿔야 하므로)."""
    from roombot.db import tx
    if not settings.admin_slack_user or not settings.public_base_url.startswith("https://"):
        return
    with tx(write=True) as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key = 'public_url'").fetchone()
        conn.execute("""INSERT INTO system_settings (key, value) VALUES ('public_url', ?)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = app_now()""",
                     (settings.public_base_url,))
    old = row["value"] if row else None
    if old and old != settings.public_base_url:
        new = settings.public_base_url
        client.chat_postMessage(
            channel=settings.admin_slack_user, unfurl_links=False,
            text=f"⚠️ 회의실봇 휴대폰 체크인 주소가 바뀌었어요: {new}",
            attachments=[{"color": "#f5a524", "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "휴대폰 체크인 주소가 바뀌었어요"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*예전 주소*\n{old}"},
                    {"type": "mrkdwn", "text": f"*새 주소*\n{new}"}]},
                {"type": "section", "text": {"type": "mrkdwn", "text":
                    "*해야 할 일*\n"
                    f"1. 슬랙 앱 설정 → OAuth & Permissions → Redirect URLs 에 `{new}/auth/slack/callback` 추가\n"
                    "2. QR을 붙여 뒀다면 `.venv/bin/python -m roombot.cli qr` 로 다시 만들어 인쇄"}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text":
                    "임시 주소(trycloudflare)는 Cloudflare가 언제든 바꿀 수 있어요. 고정 주소를 만들면 이 일이 없어져요."}]},
            ]}])
        log.info("관리자에게 주소 변경 알림을 보냄: %s → %s", old, new)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)   # 30초마다 남는 실행 기록은 숨김
    log.info("현재 시각 %s%s", clock.now().isoformat(timespec="minutes"), " (가상 시계)" if settings.app_now else "")

    from roombot.db import initialize
    initialize()
    from roombot import image_host
    image_host.start()
    notifier = default_notifier()
    web.app.state.notifier = notifier
    scheduler.start(notifier)
    log.info("노쇼 스케줄러 시작 (알림: %s)", type(notifier).__name__)

    if settings.slack_bot_token and settings.slack_app_token:
        # 기본 연결 방식은 오래 켜 두면 'Broken pipe'로 다시 붙지 못하는 경우가 있어 websocket-client 방식을 씀
        from slack_bolt.adapter.socket_mode.websocket_client import SocketModeHandler
        from roombot.slack_app import build_app
        app = build_app()
        handler = SocketModeHandler(app, settings.slack_app_token)
        handler.connect()
        threading.Thread(target=watch_slack, args=(handler,), daemon=True, name="slack-watchdog").start()
        log.info("슬랙봇 연결됨 (Socket Mode)")
        # 주소 변경 알림은 끔 (슬랙 채널 QR 방식이라 터널 주소를 쓰지 않음)
        if settings.room_channels:
            def _setup():
                from roombot import room_channels
                try:
                    log.info("회의실 채널 준비: %d개", room_channels.setup_all(app.client))
                except Exception:
                    log.exception("회의실 채널 준비 실패")
            threading.Thread(target=_setup, daemon=True, name="room-channels").start()
    else:
        log.info("SLACK_BOT_TOKEN / SLACK_APP_TOKEN 이 없어 슬랙봇은 켜지 않음 (CLI로 확인 가능)")

    log.info("QR 체크인 웹: %s", settings.public_base_url)
    try:
        uvicorn.run(web.app, host="0.0.0.0", port=settings.web_port, log_level="warning")
    finally:
        image_host.stop()


if __name__ == "__main__":
    main()
