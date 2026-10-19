"""슬랙 토큰을 .env에 넣는 로컬 전용 입력 페이지 (이 컴퓨터에서만 열림).

  .venv/bin/python -m roombot.setup_env   →  http://127.0.0.1:8765
입력한 값은 화면이나 로그에 다시 보여주지 않고 .env 파일에만 저장한다.
"""

import html
import re
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

ENV = Path(__file__).resolve().parent.parent / ".env"
FIELDS = [
    ("SLACK_BOT_TOKEN", "Bot User OAuth Token", "OAuth & Permissions 화면", "xoxb-"),
    ("SLACK_APP_TOKEN", "App-Level Token (connections:write)", "Basic Information → App-Level Tokens", "xapp-"),
    ("SLACK_CLIENT_SECRET", "Client Secret", "Basic Information → App Credentials → Show", ""),
]

app = FastAPI(docs_url=None, redoc_url=None)


def _current() -> dict[str, str]:
    vals = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line)
        if m:
            vals[m[1]] = m[2]
    return vals


def _save(updates: dict[str, str]) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z_]+)=", line)
        if m and m[1] in updates:
            lines[i] = f"{m[1]}={updates[m[1]]}"
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV.chmod(0o600)


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>회의실봇 토큰 입력</title>
<style>
 body {{ font-family:-apple-system,"Apple SD Gothic Neo",sans-serif; background:#f6f7f9; margin:0; }}
 main {{ max-width:560px; margin:32px auto; background:#fff; border-radius:14px; padding:24px; }}
 label {{ display:block; font-weight:600; margin-top:18px; }}
 small {{ color:#666; font-weight:400; }}
 input {{ width:100%; box-sizing:border-box; padding:11px; font-size:15px; margin-top:6px; border:1px solid #ccc; border-radius:8px; }}
 button {{ margin-top:22px; width:100%; padding:13px; font-size:16px; border:0; border-radius:10px; background:#4a154b; color:#fff; }}
 .ok {{ color:#1a7f37; }} .bad {{ color:#b42318; }} li {{ margin:6px 0; }}
</style></head><body><main>{body}</main></body></html>""")


@app.get("/")
def form():
    cur = _current()
    rows = ""
    for key, label, where, prefix in FIELDS:
        state = "✅ 입력됨 (비워두면 그대로 유지)" if cur.get(key) else "아직 비어 있음"
        rows += (f'<label>{html.escape(label)} <small>— {html.escape(where)} · {state}</small>'
                 f'<input type="password" name="{key}" autocomplete="off" placeholder="{prefix}..."></label>')
    return _page(f"""<h2>회의실봇 슬랙 토큰 입력</h2>
<p>api.slack.com/apps 의 회의실봇 앱에서 복사해 붙여넣으세요. 값은 이 컴퓨터의 .env 파일에만 저장돼요.</p>
<form method="post">{rows}<button type="submit">저장하고 확인</button></form>""")


@app.post("/")
def save(SLACK_BOT_TOKEN: str = Form(""), SLACK_APP_TOKEN: str = Form(""), SLACK_CLIENT_SECRET: str = Form("")):
    given = {"SLACK_BOT_TOKEN": SLACK_BOT_TOKEN.strip(), "SLACK_APP_TOKEN": SLACK_APP_TOKEN.strip(),
             "SLACK_CLIENT_SECRET": SLACK_CLIENT_SECRET.strip()}
    problems = []
    for key, label, _, prefix in FIELDS:
        v = given[key]
        if v and prefix and not v.startswith(prefix):
            problems.append(f"{label}: '{prefix}'로 시작해야 해요 (다른 토큰을 넣은 것 같아요)")
            given[key] = ""
    _save({k: v for k, v in given.items() if v})

    cur = _current()
    checks = []
    if cur.get("SLACK_BOT_TOKEN"):
        try:
            info = WebClient(token=cur["SLACK_BOT_TOKEN"]).auth_test()
            checks.append(f'<li class="ok">봇 토큰 확인됨 — 워크스페이스 {html.escape(info["team"])}, 봇 {html.escape(info["user"])}</li>')
        except SlackApiError as e:
            checks.append(f'<li class="bad">봇 토큰 확인 실패: {html.escape(str(e.response.get("error")))}</li>')
    for key, label, _, _ in FIELDS:
        if not cur.get(key):
            checks.append(f'<li class="bad">{html.escape(label)} 아직 비어 있음</li>')
        elif key != "SLACK_BOT_TOKEN":
            checks.append(f'<li class="ok">{html.escape(label)} 저장됨</li>')
    probs = "".join(f'<li class="bad">{html.escape(p)}</li>' for p in problems)
    done = all(cur.get(k) for k, *_ in FIELDS) and not problems
    tail = "<p><b>모두 들어갔어요. 이 창을 닫고 Claude에게 '다 넣었어'라고 알려주세요.</b></p>" if done \
        else '<p><a href="/">다시 입력하기</a></p>'
    return _page(f"<h2>저장 결과</h2><ul>{probs}{''.join(checks)}</ul>{tail}")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
