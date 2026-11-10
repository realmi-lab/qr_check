"""Slack 공통 연결: .env 토큰 확인과 Socket Mode 접속만 담당한다."""
from backend.config import settings


def read_tokens(config=settings):
    tokens = (('SLACK_BOT_TOKEN', config.slack_bot_token, 'xoxb-'),
              ('SLACK_APP_TOKEN', config.slack_app_token, 'xapp-'))
    for name, value, prefix in tokens:
        if not value or not value.startswith(prefix) or any(c.isspace() for c in value):
            raise ValueError(f'{name}: .env에 올바른 토큰을 입력하세요.')
    return config.slack_bot_token, config.slack_app_token


def connect(app, app_token):
    from slack_bolt.adapter.socket_mode.websocket_client import SocketModeHandler
    handler = SocketModeHandler(app, app_token)
    handler.connect()
    return handler
