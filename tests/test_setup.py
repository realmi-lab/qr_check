from dataclasses import replace
from backend.config import settings
from backend.doctor import check


def test_missing_credentials_and_unsafe_secrets_are_rejected():
    errors, _ = check(replace(settings, slack_bot_token=None, secret_key='dev-secret-change-me'))
    assert any('SLACK_BOT_TOKEN' in e for e in errors)
    assert any('SECRET_KEY' in e for e in errors)


def test_web_mode_requires_https_and_oidc_credentials():
    errors, _ = check(replace(settings, room_channels=False, public_base_url='http://localhost:8000',
                              slack_client_id=None, slack_client_secret=None))
    assert any('HTTPS' in e for e in errors)
    assert any('SLACK_CLIENT_ID' in e for e in errors)
