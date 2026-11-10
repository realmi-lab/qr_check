"""슬랙 메시지에 넣을 그림을 슬랙에 직접 올림.

그림 주소가 임시 터널(trycloudflare)이면 터널 주소가 바뀌는 순간 예전 메시지의 그림이 모두 깨지고,
휴대폰 슬랙은 '메시지를 표시할 수 없음 … 이미지가 삭제되었거나 액세스 권한이 없음'으로 보여 줌.
슬랙에 올린 그림(slack_file)은 터널과 상관없이 계속 보임.

봇 토큰에 files:write 권한이 있을 때만 씀. 없거나 실패하면 None → 예전처럼 주소(image_url)를 씀.
같은 그림(종류·조건·5분 단위)은 한 번만 올림. 올린 직후 1~2초는 슬랙이 처리 중이라 블록에 넣으면
'invalid slack file'이 나므로 잠깐 기다렸다가 돌려줌. 슬랙 저장 공간을 아끼려고 30일 지난 그림은 지움.
"""

import logging
import threading
import time

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from backend.config import settings
from backend.db import tx

log = logging.getLogger(__name__)
SCOPE_CHECK_EVERY = 600   # 권한 확인 주기(초): 앱 권한을 바꾸면 10분 안에 반영
CACHE_MAX = 500
READY_WAIT = 2.0          # 올린 뒤 쓸 수 있을 때까지 (재 보니 약 1.5초)
KEEP_DAYS = 30

enabled = True            # 테스트에서는 끔
_lock = threading.Lock()
_cache: dict[tuple, str] = {}
_scope: tuple[float, bool] = (0.0, False)
_client: WebClient | None = None


def _get_client() -> WebClient | None:
    global _client
    if not settings.slack_bot_token:
        return None
    if _client is None or _client.token != settings.slack_bot_token:
        _client = WebClient(settings.slack_bot_token, timeout=10)
    return _client


def can_upload() -> bool:
    global _scope
    if not enabled:
        return False
    client = _get_client()
    if client is None:
        return False
    checked, ok = _scope
    if time.time() - checked < SCOPE_CHECK_EVERY:
        return ok
    try:
        scopes = (client.auth_test().headers.get("x-oauth-scopes") or "").split(",")
        ok = "files:write" in scopes
    except Exception:
        ok = False
    _scope = (time.time(), ok)
    return ok


_local = threading.local()


class no_wait:
    """창(modal)을 열 때처럼 3초 안에 답해야 할 때: 그림을 새로 올리느라 기다리지 않음.
    이미 올린 그림이 있으면 쓰고, 없으면 주소 그림을 쓰고 슬랙에는 뒤에서 올려 둠(다음번용)."""
    def __enter__(self):
        _local.quick = True

    def __exit__(self, *exc):
        _local.quick = False


def quick() -> bool:
    return getattr(_local, "quick", False)


def cached(key: tuple) -> str | None:
    with _lock:
        return _cache.get(key)


def upload(key: tuple, png: bytes, title: str) -> str | None:
    """그림을 슬랙에 올리고 파일 ID를 돌려줌 (채널에는 올리지 않음)."""
    with _lock:
        if key in _cache:
            return _cache[key]
    if not can_upload():
        return None
    try:
        r = _get_client().files_upload_v2(content=png, filename=f"{key[0]}.png", title=title)
        fid = (r.get("file") or r["files"][0])["id"]
    except SlackApiError as e:
        log.warning("그림 올리기 실패: %s", e.response.get("error"))
        return None
    except Exception:
        log.exception("그림 올리기 실패")
        return None
    try:
        with tx(write=True) as conn:
            conn.execute("INSERT INTO slack_images (file_id, kind) VALUES (?, ?) ON CONFLICT DO NOTHING",
                         (fid, key[0]))
    except Exception:
        log.exception("올린 그림 기록 실패")
    time.sleep(READY_WAIT)
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = fid
    log.info("그림을 슬랙에 올림: %s %s → %s", key[0], dict(key[1]), fid)
    return fid


def cleanup(limit: int = 50) -> int:
    """30일 지난 그림을 슬랙에서 지움 (한 번에 조금씩)."""
    if not can_upload():
        return 0
    with tx() as conn:
        old = [r["file_id"] for r in conn.execute(
            "SELECT file_id FROM slack_images WHERE created_at < shift_time(app_now(), -86400 * ?) "
            "ORDER BY created_at LIMIT ?", (KEEP_DAYS, limit)).fetchall()]
    done = []
    for fid in old:
        try:
            _get_client().files_delete(file=fid)
            done.append(fid)
        except SlackApiError as e:
            if e.response.get("error") in ("file_not_found", "file_deleted"):
                done.append(fid)
            else:
                log.warning("예전 그림 지우기 실패 (%s): %s", fid, e.response.get("error"))
                break
    if done:
        with tx(write=True) as conn:
            conn.execute("DELETE FROM slack_images WHERE file_id IN (SELECT value FROM json_each(?))", (done,))
        log.info("30일 지난 그림 %d개를 슬랙에서 지움", len(done))
    return len(done)


def _without_slack_files(obj):
    """slack_file 그림 블록만 뺀 사본."""
    if isinstance(obj, list):
        return [_without_slack_files(x) for x in obj if not (isinstance(x, dict) and "slack_file" in x)]
    if isinstance(obj, dict):
        return {k: _without_slack_files(v) for k, v in obj.items()}
    return obj


def install_guard() -> None:
    """혹시 그림이 아직 준비되지 않아 'invalid slack file'이 나면, 그림만 빼고 한 번 더 보냄 (메시지·홈이 통째로 안 뜨는 일 방지)."""
    if getattr(WebClient.api_call, "_image_guard", False):
        return
    orig = WebClient.api_call

    def api_call(self, api_method, *args, json=None, **kw):
        try:
            return orig(self, api_method, *args, json=json, **kw)
        except SlackApiError as e:
            if json is None or "invalid slack file" not in str(e.response.get("response_metadata") or ""):
                raise
            log.warning("그림이 아직 준비 안 됨 → 그림 없이 다시 보냄 (%s)", api_method)
            return orig(self, api_method, *args, json=_without_slack_files(json), **kw)

    api_call._image_guard = True
    WebClient.api_call = api_call
