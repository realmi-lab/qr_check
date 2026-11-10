"""Local setup validation only; never sends Slack messages or changes the DB."""
from pathlib import Path
import shutil
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.config import settings


def check(config=settings):
    errors, notes = [], []
    for name, value, prefix in (
        ('SLACK_BOT_TOKEN', config.slack_bot_token, 'xoxb-'),
        ('SLACK_APP_TOKEN', config.slack_app_token, 'xapp-'),
        ('SLACK_TEAM_ID', config.slack_team_id, 'T'),
        ('SLACK_APP_ID', config.slack_app_id, 'A'),
    ):
        if not value or not value.startswith(prefix):
            errors.append(f'{name}: 설정이 없거나 형식이 잘못되었습니다.')
    for name, value in [('SECRET_KEY', config.secret_key), ('QR_SECRET', config.qr_secret)]:
        if len(value) < 32 or value.startswith(('dev-', 'replace-')):
            errors.append(f'{name}: 32자 이상의 새로운 임의 값을 설정하세요.')
    try:
        ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append('APP_TIMEZONE: 유효한 IANA 시간대가 필요합니다.')
    if not config.sqlite_path.strip():
        errors.append('SQLITE_PATH: 저장 경로가 필요합니다.')
    for name, value in [('WEB_PORT', config.web_port), ('IMAGE_PORT', config.image_port)]:
        if not 1 <= value <= 65535:
            errors.append(f'{name}: 1~65535 범위가 필요합니다.')
    if config.web_port == config.image_port:
        errors.append('WEB_PORT와 IMAGE_PORT는 달라야 합니다.')
    if not config.room_channels:
        if urlparse(config.public_base_url).scheme != 'https':
            errors.append('웹 QR 모드에는 휴대폰에서 접속 가능한 HTTPS PUBLIC_BASE_URL이 필요합니다.')
        if not config.slack_client_id or not config.slack_client_secret:
            errors.append('웹 QR 모드에는 SLACK_CLIENT_ID와 SLACK_CLIENT_SECRET이 필요합니다.')
    if config.image_tunnel and not shutil.which(config.cloudflared_bin):
        errors.append('IMAGE_TUNNEL=true: cloudflared를 설치하거나 CLOUDFLARED_BIN을 설정하세요.')
    if config.demo_mode or config.app_now or config.notify_override_slack_user:
        errors.append('실사용 전 DEMO_MODE=false, APP_NOW와 NOTIFY_OVERRIDE_SLACK_USER를 비우세요.')
    from frontend.charts import FONT_PATH
    if not FONT_PATH or not Path(FONT_PATH).is_file():
        errors.append('한글 폰트를 찾지 못했습니다. CHART_FONT_PATH를 설정하세요.')
    if not config.image_base_url.startswith('https://') and not config.public_base_url.startswith('https://') and not config.image_tunnel:
        notes.append('이미지 HTTPS 주소 없음: 일정은 글자 목록으로 표시됩니다.')
    return errors, notes


def main():
    errors, notes = check()
    for message in errors:
        print('ERROR:', message)
    for message in notes:
        print('INFO:', message)
    if errors:
        return 1
    print('설정 형식 정상. Slack 토큰 유효성·권한·이벤트·DB 쓰기 권한은 실제 실행 후 확인하세요.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
