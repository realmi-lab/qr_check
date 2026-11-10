"""QR 체크인 웹.

  휴대폰으로 회의실 QR 스캔
   → GET /c/{회의실 코드}?t={토큰}
   → 로그인 안 돼 있으면 Sign in with Slack (처음 한 번, 이후 30일 쿠키)
   → 체크인 처리 → 결과 화면 (체크인한 사람 / 회의실 / 체크인 시간)
   → 슬랙 DM으로 확인 메시지, 예약자에게도 알림
"""

import html
import ipaddress
from datetime import date
import logging
import secrets
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from backend import checkin
from frontend import icons
from backend import imgurl, messages, rooms
from backend.config import settings
from backend.notifier import default_notifier

log = logging.getLogger(__name__)
ASSETS = Path(__file__).resolve().parents[1] / "frontend" / "assets"

app = FastAPI(title="회의실 QR 체크인", docs_url=None, redoc_url=None)
app.state.notifier = None

SESSION_COOKIE = "roombot_session"
NONCE_COOKIE = "roombot_oidc_nonce"
_signer = URLSafeTimedSerializer(settings.secret_key)
_secure = settings.public_base_url.startswith("https://")
REDIRECT_PATH = "/auth/slack/callback"


def notifier():
    if app.state.notifier is None:
        app.state.notifier = default_notifier()
    return app.state.notifier


# ---------------------------------------------------------------
# 세션
# ---------------------------------------------------------------

def _session(request: Request) -> dict | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _signer.loads(raw, salt="session", max_age=settings.session_days * 86400)
    except BadSignature:
        return None


def _login_response(next_path: str, employee: dict, slack_user_id: str | None) -> RedirectResponse:
    resp = RedirectResponse(_safe_next(next_path), status_code=303)
    token = _signer.dumps({"emp": employee["id"], "slack": slack_user_id, "name": employee["name"]}, salt="session")
    resp.set_cookie(SESSION_COOKIE, token, max_age=settings.session_days * 86400,
                    httponly=True, samesite="lax", secure=_secure)
    resp.delete_cookie(NONCE_COOKIE)
    return resp


def _safe_next(path: str | None) -> str:
    """다른 사이트로 넘어가지 않게 이 서버 안의 경로만 허용."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def _client_ip(request: Request) -> str | None:
    # X-Forwarded-For는 직접 바꿔 보낼 수 있으므로, 앞단 프록시를 둔 경우에만 믿음
    fwd = request.headers.get("x-forwarded-for") if settings.trust_proxy_headers else None
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)
    try:
        return str(ipaddress.ip_address(ip or ""))
    except ValueError:
        return None


_PROXY_HEADERS = ("x-forwarded-for", "cf-connecting-ip", "cf-ray", "forwarded", "x-real-ip")


def _is_local(request: Request) -> bool:
    """이 컴퓨터에서 직접 연 요청인지 (터널·프록시를 거친 요청은 아님)."""
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _network_allowed(ip: str | None) -> bool:
    if not settings.checkin_allowed_cidrs:
        return True
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(c, strict=False) for c in settings.checkin_allowed_cidrs)


# ---------------------------------------------------------------
# 화면
# ---------------------------------------------------------------

from frontend.pages import _page


@app.get("/favicon.svg")
def favicon_svg():
    return Response(icons.LOGO, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/favicon.ico")
def favicon_ico():
    png = ASSETS / "favicon.png"
    if png.exists():
        return Response(png.read_bytes(), media_type="image/png", headers={"Cache-Control": "max-age=86400"})
    return Response(icons.LOGO, media_type="image/svg+xml")


# ---------------------------------------------------------------
# 체크인
# ---------------------------------------------------------------

@app.api_route("/img/{kind}.png", methods=["GET", "HEAD"])
def chart_image(kind: str, request: Request):
    """슬랙 메시지에 들어가는 그림 (서명된 주소만). 휴대폰 슬랙은 HEAD로 먼저 확인하기도 함."""
    params = dict(request.query_params)
    if kind not in ("room", "board") or not imgurl.verify(kind, params):
        return Response(status_code=404)
    png = imgurl.render(kind, params)
    if png is None:
        return Response(status_code=404)
    headers = {"Cache-Control": "public, max-age=300", "Content-Length": str(len(png))}
    if request.method == "HEAD":
        return Response(status_code=200, media_type="image/png", headers=headers)
    return Response(png, media_type="image/png", headers=headers)


@app.get("/healthz")
def healthz():
    from backend.db import tx
    with tx() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"ok": True}


@app.get("/c/{room_code}")
def qr_checkin(room_code: str, request: Request, background: BackgroundTasks, t: str = ""):
    room = checkin.room_for_qr(room_code, t)
    if not room:
        return _page("error", "올바르지 않은 QR이에요", note="회의실에 붙어 있는 QR을 다시 찍어 주세요.", ok=False)

    ip = _client_ip(request)
    if not _network_allowed(ip):
        return _page("network", "회사 네트워크에서만 체크인할 수 있어요", [("회의실", room["label"])],
                     "회사 와이파이에 연결한 뒤 다시 찍어 주세요.", ok=False)

    sess = _session(request)
    if not sess or not rooms.employee_by_id(sess["emp"]):
        resp = RedirectResponse("/login?" + urlencode({"next": str(request.url.path) + "?" + str(request.url.query)}),
                                status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    res = checkin.check_in(room, sess["emp"], source_ip=ip, user_agent=request.headers.get("user-agent"))
    view = messages.checkin_view(res)
    done = res.kind in ("ok", "joined") and res.receipt
    if done:
        background.add_task(_send_checkin_messages, res, sess["emp"])
    if res.kind == "wrong_room":
        background.add_task(_send_checkin_messages, res, sess["emp"])
    return _page(view.kind, view.title, view.rows, view.note, ok=view.ok, open_slack=bool(done), lead=view.lead)


def _send_checkin_messages(res: checkin.CheckinResult, employee_id: int) -> None:
    messages.send_checkin_messages(notifier(), res, employee_id)


# ---------------------------------------------------------------
# 로그인 (Sign in with Slack / 시연용 사번 로그인)
# ---------------------------------------------------------------

def _oidc_ready() -> bool:
    return bool(settings.slack_client_id and settings.slack_client_secret)


@app.get("/login")
def login(request: Request, next: str = "/"):
    if _oidc_ready():
        nonce = secrets.token_urlsafe(16)
        state = _signer.dumps({"next": _safe_next(next), "nonce": nonce}, salt="oidc-state")
        params = {
            "response_type": "code",
            "scope": "openid email profile",
            "client_id": settings.slack_client_id,
            "redirect_uri": settings.public_base_url + REDIRECT_PATH,
            "state": state,
            "nonce": nonce,
        }
        if settings.slack_team_id:
            params["team"] = settings.slack_team_id
        resp = RedirectResponse("https://slack.com/openid/connect/authorize?" + urlencode(params), status_code=303)
        resp.set_cookie(NONCE_COOKIE, nonce, max_age=600, httponly=True, samesite="lax", secure=_secure)
        return resp
    if settings.demo_mode and _is_local(request):
        form = f"""<form method="get" action="/dev-login">
<input type="hidden" name="next" value="{html.escape(_safe_next(next))}">
<input name="emp" placeholder="사번 (예: D0030)" autocomplete="off" required>
<button type="submit">로그인</button></form>"""
        return _page("login", "시연용 로그인", note="Slack 로그인이 아직 설정되지 않아 사번으로 로그인해요. (DEMO_MODE)",
                     ok=True, extra=form)
    return _page("error", "로그인이 설정되지 않았어요",
                 note="관리자: SLACK_CLIENT_ID / SLACK_CLIENT_SECRET 을 설정해 주세요.", ok=False)


@app.get(REDIRECT_PATH)
def slack_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return _page("error", "슬랙 로그인을 취소했어요", note="QR을 다시 찍어 주세요.", ok=False)
    try:
        st = _signer.loads(state, salt="oidc-state", max_age=600)
    except BadSignature:
        return _page("error", "로그인 시간이 지났어요", note="QR을 다시 찍어 주세요.", ok=False)
    if not secrets.compare_digest(request.cookies.get(NONCE_COOKIE, ""), st["nonce"]):
        return _page("error", "로그인 확인에 실패했어요", note="같은 브라우저에서 QR을 다시 찍어 주세요.", ok=False)

    client = WebClient()
    try:
        tok = client.openid_connect_token(client_id=settings.slack_client_id,
                                          client_secret=settings.slack_client_secret,
                                          code=code, redirect_uri=settings.public_base_url + REDIRECT_PATH)
        info = client.openid_connect_userInfo(token=tok["access_token"]).data
    except SlackApiError as e:
        log.warning("Slack 로그인 실패: %s", e.response.get("error"))
        return _page("error", "슬랙 로그인에 실패했어요", note="잠시 후 다시 시도해 주세요.", ok=False)

    if settings.slack_team_id and info.get("https://slack.com/team_id") != settings.slack_team_id:
        return _page("denied", "회사 슬랙 계정으로 로그인해 주세요", ok=False)

    slack_user_id = info.get("https://slack.com/user_id") or info.get("sub")
    emp = rooms.employee_by_slack_user(slack_user_id) or (
        rooms.employee_by_email(info["email"]) if info.get("email") else None)
    if not emp:
        return _page("denied", "등록된 직원 정보를 찾지 못했어요", [("슬랙 이메일", info.get("email", "-"))],
                     "슬랙 프로필 이메일이 등록된 이메일과 같은지 확인해 주세요.", ok=False)
    rooms.link_slack_user(emp["id"], slack_user_id)
    return _login_response(st["next"], emp, slack_user_id)


@app.get("/dev-login")
def dev_login(request: Request, emp: str = "", next: str = "/"):
    # 사번만으로 로그인되므로 시연 모드 + 이 컴퓨터에서 직접 연 경우에만 허용 (터널로 들어온 요청은 거부)
    if not settings.demo_mode or not _is_local(request):
        return _page("denied", "사용할 수 없어요", note="사번 로그인은 서버 컴퓨터에서만 쓸 수 있어요.", ok=False)
    e = rooms.employee_by_emp_no(emp)
    if not e:
        return _page("error", "사번을 찾지 못했어요", note=emp, ok=False)
    return _login_response(next, e, rooms.slack_user_of(e["id"]))


@app.get("/logout")
def logout():
    resp = _page("logout", "로그아웃했어요", ok=True)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/")
def index(request: Request):
    sess = _session(request)
    who = [("로그인", sess["name"])] if sess else []
    return _page("building", "회의실 QR 체크인", who, "회의실 입구의 QR을 휴대폰 카메라로 찍으면 체크인돼요.")
