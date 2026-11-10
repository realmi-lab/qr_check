"""Mobile check-in presentation; no database writes or Slack API calls."""
import html
from urllib.parse import urlencode
from fastapi.responses import HTMLResponse
from frontend import icons
from backend.config import settings

def slack_bot_url() -> str | None:
    """휴대폰에서 누르면 슬랙 앱의 회의실봇 DM이 열리는 주소."""
    if not (settings.slack_app_id and settings.slack_team_id):
        return None
    return "https://slack.com/app_redirect?" + urlencode({"app": settings.slack_app_id, "team": settings.slack_team_id})


def _page(kind: str, title: str, rows: list[tuple[str, str]] = (), note: str = "",
          ok: bool = True, extra: str = "", open_slack: bool = False, lead: str = "") -> HTMLResponse:
    """모바일 화면 한 장. kind: ok / info / wait / denied / late / free / error / network / login / logout / building"""
    table = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)
    slack_btn, auto_open = "", ""
    if (url := slack_bot_url()):
        slack_btn = f'<a class="btn" href="{html.escape(url)}">{_SLACK_MARK} 슬랙 회의실봇 열기</a>'
        if open_slack:
            # 체크인 확인 화면을 잠깐 보여준 뒤 슬랙 봇 DM으로 이동 (확인 메시지가 거기 있음)
            auto_open = ('<p class="note center"><span class="dot"></span> 잠시 후 슬랙 회의실봇으로 이동해요</p>'
                         f'<script>setTimeout(function(){{location.href={url!r};}}, 2500);</script>')
    title_color = icons.COLORS.get(kind, icons.COLORS["info"])[0]
    body = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#3B0D4A">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<title>{html.escape(title)} · 회의실봇</title>
<style>
  :root {{ color-scheme: light dark; --fg:#16171a; --bg:#f4f2f7; --card:#fff; --muted:#62636c; --line:#ebe8f0; --title:{title_color}; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ededf0; --bg:#141217; --card:#1f1c24; --muted:#a5a3ad; --line:#2e2a35; --title:{icons.COLORS.get(kind, icons.COLORS['info'])[1]}; }}
    .badge {{ filter:brightness(.92) saturate(1.1); }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:14px/1.5 -apple-system,"Apple SD Gothic Neo","Pretendard",sans-serif; background:var(--bg); color:var(--fg);
          -webkit-font-smoothing:antialiased; }}
  .top {{ display:flex; align-items:center; gap:8px; max-width:440px; margin:0 auto; padding:12px 16px 0; font-weight:600; }}
  .top svg {{ width:24px; height:24px; }}
  main {{ max-width:440px; margin:0 auto; padding:10px 16px 24px; }}
  .card {{ background:var(--card); border-radius:14px; padding:20px 16px 16px; box-shadow:0 1px 2px rgba(20,10,30,.06), 0 8px 24px rgba(20,10,30,.06); }}
  .badge {{ width:52px; height:52px; border-radius:50%; display:flex; align-items:center; justify-content:center; margin:0 auto; }}
  .badge svg {{ width:30px; height:30px; }}
  h1 {{ text-align:center; font-size:19px; letter-spacing:-.3px; margin:10px 0 14px; color:var(--title); }}
  .lead {{ margin:-4px 0 12px; padding:10px 12px; border-radius:10px; background:{icons.COLORS.get(kind, icons.COLORS["info"])[1]};
           color:{title_color}; font-weight:600; font-size:14px; line-height:1.5; text-align:center; word-break:keep-all; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ text-align:left; color:var(--muted); font-weight:500; padding:8px 8px 8px 0; width:34%; vertical-align:top; }}
  td {{ padding:8px 0; font-weight:600; word-break:keep-all; overflow-wrap:anywhere; }}
  tr + tr th, tr + tr td {{ border-top:1px solid var(--line); }}
  .note {{ margin:12px 0 0; color:var(--muted); font-size:13px; line-height:1.55; }}
  .center {{ text-align:center; }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:#2EB67D; margin-right:4px;
          animation:p 1s ease-in-out infinite alternate; }}
  @keyframes p {{ from {{ opacity:.3; }} to {{ opacity:1; }} }}
  .btn {{ display:flex; align-items:center; justify-content:center; gap:6px; margin-top:14px; padding:0 12px; min-height:44px; box-sizing:border-box; border-radius:10px;
          background:#4a154b; color:#fff; text-decoration:none; font-weight:600; font-size:14px; }}
  .btn svg {{ width:18px; height:18px; }}
  form input {{ width:100%; padding:10px; min-height:44px; font-size:16px; border-radius:10px; border:1px solid var(--line);
               background:var(--card); color:var(--fg); }}
  form button {{ width:100%; margin-top:8px; min-height:44px; padding:10px; font-size:14px; font-weight:600; border-radius:10px; border:0;
                background:#4a154b; color:#fff; }}
  footer {{ text-align:center; color:var(--muted); font-size:12px; margin-top:16px; }}
</style></head><body>
<div class="top">{icons.LOGO}<span>회의실봇</span></div>
<main><div class="card">
{icons.badge(kind)}<h1>{html.escape(title)}</h1>
{f'<p class="lead">{html.escape(lead)}</p>' if lead else ''}
<table>{table}</table>
{f'<p class="note">{html.escape(note)}</p>' if note else ''}
{extra}{slack_btn if ok and rows else ''}{auto_open}
</div><footer>회의실 QR 체크인 · 시작 10분 전부터 · 시작 10분 후까지 미체크인 시 자동 취소</footer></main></body></html>"""
    return HTMLResponse(body)


# 슬랙 로고 대신 쓰는 단순한 # 모양 (상표 이미지는 쓰지 않음)
_SLACK_MARK = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">'
               '<path d="M9 3 7 21M17 3l-2 18M3.5 8.5h18M2.5 15.5h18"/></svg>')
