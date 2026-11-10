"""환경 변수 설정 (.env 파일도 읽음)."""

from pathlib import Path
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


@dataclass
class Settings:
    sqlite_path: str = str(ROOT / Path(os.getenv("SQLITE_PATH") or "data/roombot.sqlite3").expanduser())
    timezone: str = (os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Seoul").strip()
    # 가상 시계: 이 시각부터 시간이 흐르는 것처럼 동작 (예: 2026-10-17T10:03+09:00)
    app_now: str | None = os.getenv("APP_NOW") or None

    # 슬랙 (봇 토큰 / Socket Mode 앱 토큰)
    slack_bot_token: str | None = (os.getenv("SLACK_BOT_TOKEN") or "").strip() or None
    slack_app_token: str | None = (os.getenv("SLACK_APP_TOKEN") or "").strip() or None
    # Sign in with Slack (QR 체크인 웹에서 사용자 확인)
    slack_client_id: str | None = os.getenv("SLACK_CLIENT_ID") or None
    slack_client_secret: str | None = os.getenv("SLACK_CLIENT_SECRET") or None
    slack_team_id: str | None = os.getenv("SLACK_TEAM_ID") or None
    slack_app_id: str | None = os.getenv("SLACK_APP_ID") or None
    # 노쇼로 회의실이 비었을 때 공지할 채널 (예: #회의실)
    slack_notify_channel: str | None = (os.getenv("SLACK_NOTIFY_CHANNEL") or os.getenv("SLACK_NOTIFICATION_CHANNEL") or "").strip() or None

    # QR 체크인 웹
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    web_port: int = int(os.getenv("WEB_PORT", "8000"))
    # 일정 이미지만 제공하는 HTTPS 주소. QR 웹의 공개 주소와 분리한다.
    image_base_url: str = os.getenv("IMAGE_BASE_URL", "").rstrip("/")
    image_port: int = int(os.getenv("IMAGE_PORT", "8001"))
    image_tunnel: bool = _bool("IMAGE_TUNNEL")
    cloudflared_bin: str = os.getenv("CLOUDFLARED_BIN", "cloudflared")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-change-me")
    # QR 토큰 계산용 비밀값. 바꾸면 인쇄한 QR이 모두 무효가 되므로 한 번 정하면 그대로 둠
    qr_secret: str = os.getenv("QR_SECRET", "dev-qr-secret-change-me")
    session_days: int = int(os.getenv("SESSION_DAYS", "30"))
    # 체크인을 허용할 네트워크 (비어 있으면 제한 없음). 예: 10.0.0.0/8,192.168.0.0/16
    checkin_allowed_cidrs: list[str] = field(default_factory=lambda: _list("CHECKIN_ALLOWED_CIDRS"))
    # 앞단에 리버스 프록시/터널이 있을 때만 X-Forwarded-For 헤더의 IP를 믿음
    trust_proxy_headers: bool = _bool("TRUST_PROXY_HEADERS")

    # 회의실마다 슬랙 채널 (QR이 슬랙 채널을 열고 [채널 참여] = 체크인). false면 예전 웹 체크인 방식
    room_channels: bool = _bool("ROOM_CHANNELS", True)
    # 체크인 후 회의실 채널에서 내보냄 → 다음에도 QR → [채널 참여] 한 번으로 체크인 (채널에 '나감' 표시가 남음)
    leave_after_checkin: bool = _bool("LEAVE_AFTER_CHECKIN", True)
    # 상태 아이콘: unicode(기본) / custom (assets/emoji 의 커스텀 이모지를 워크스페이스에 올렸을 때)
    icon_set: str = os.getenv("ICON_SET", "unicode")
    # 아침 브리핑 DM 시각 (HH:MM, 비우면 끔)
    morning_brief_at: str = os.getenv("MORNING_BRIEF_AT", "08:30")

    # 시연 모드: 개발용 로그인, 슬랙 계정-직원 수동 연결 허용
    demo_mode: bool = _bool("DEMO_MODE")
    # 시연 시 모든 DM을 이 슬랙 사용자에게 보냄 (샘플 직원 이메일은 실제 슬랙에 없으므로)
    notify_override_slack_user: str | None = os.getenv("NOTIFY_OVERRIDE_SLACK_USER") or None
    # 운영 알림(주소 변경 등)을 받을 관리자 슬랙 사용자 ID (U로 시작)
    admin_slack_user: str | None = os.getenv("ADMIN_SLACK_USER") or None


settings = Settings()
