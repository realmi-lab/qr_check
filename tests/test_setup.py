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


def test_slack_tokens_reject_whitespace_and_accept_env_values():
    import pytest
    from backend.slack import read_tokens
    good = replace(settings, slack_bot_token='xoxb-synthetic', slack_app_token='xapp-synthetic')
    assert read_tokens(good) == ('xoxb-synthetic', 'xapp-synthetic')
    with pytest.raises(ValueError, match='SLACK_APP_TOKEN'):
        read_tokens(replace(good, slack_app_token='xapp-bad token'))


def test_root_env_loading_and_common_names(tmp_path):
    import os, shutil, subprocess, sys
    source = __import__('pathlib').Path(__file__).resolve().parents[1]
    package = tmp_path / 'backend'
    package.mkdir()
    (package / '__init__.py').touch()
    shutil.copy(source / 'backend/config.py', package / 'config.py')
    (tmp_path / '.env').write_text('SLACK_BOT_TOKEN=xoxb-synthetic\nSLACK_APP_TOKEN=xapp-synthetic\nSLACK_NOTIFY_CHANNEL=C123\nAPP_TIMEZONE=Asia/Seoul\nSQLITE_PATH=data/custom.sqlite\n')
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHON_DOTENV_DISABLED', 'SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'SLACK_NOTIFY_CHANNEL', 'APP_TIMEZONE', 'SQLITE_PATH')}
    env.update(PYTHONPATH=str(tmp_path), SLACK_NOTIFY_CHANNEL='C456')
    script = "from backend.config import settings as s; assert s.slack_bot_token=='xoxb-synthetic'; assert s.slack_notify_channel=='C456'; assert s.timezone=='Asia/Seoul'; assert s.sqlite_path.endswith('data/custom.sqlite')"
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path.parent, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
