"""cloudflared 임시 터널 실행기 (자동 실행용).

cloudflared를 띄우고, 새로 받은 https://…trycloudflare.com 주소가 .env의 PUBLIC_BASE_URL과 다르면
  1) .env의 PUBLIC_BASE_URL을 새 주소로 바꾸고
  2) logs/public-url.txt 에 기록하고
  3) 회의실봇 서버(com.roombot.server)를 다시 시작해 새 주소를 쓰게 함
     (서버가 켜지면서 주소가 바뀐 걸 관리자 슬랙 DM으로 알림)

임시 터널은 Cloudflare가 언제든 지울 수 있음. 아래 경우엔 스스로 끝내서 launchd가 새 터널을 받게 함:
  - "Tunnel not found" 오류가 계속됨 (터널이 지워짐)
  - 서버는 살아 있는데 공개 주소로는 3분 넘게 응답이 없음
"""

import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
LOGS = ROOT / "logs"
CLOUDFLARED = os.path.expanduser("~/.local/bin/cloudflared")
SERVER_LABEL = "com.roombot.server"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
GONE_RE = re.compile(r"Tunnel not found|Unauthorized")
GONE_LIMIT = 3          # 지워졌다는 오류가 이만큼 나오면 새 터널로
CHECK_EVERY = 60        # 공개 주소 확인 주기(초)
FAIL_LIMIT = 3          # 연속 실패 횟수


def current_url() -> str | None:
    m = re.search(r"^PUBLIC_BASE_URL=(.*)$", ENV.read_text(encoding="utf-8"), re.M)
    return m[1].strip() if m else None


def set_url(url: str) -> None:
    text = ENV.read_text(encoding="utf-8")
    text = re.sub(r"^PUBLIC_BASE_URL=.*$", f"PUBLIC_BASE_URL={url}", text, flags=re.M)
    ENV.write_text(text, encoding="utf-8")
    ENV.chmod(0o600)


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} [tunnel] {msg}", flush=True)


def ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def stop(proc: subprocess.Popen, reason: str) -> None:
    log(f"{reason} → 터널을 끝내고 새로 받음")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    os._exit(1)   # launchd(KeepAlive)가 다시 실행


def watch_public(proc: subprocess.Popen, url: str) -> None:
    """서버(로컬)는 응답하는데 공개 주소만 안 되면 터널 문제로 보고 다시 받음."""
    fails = 0
    time.sleep(30)
    while proc.poll() is None:
        if ok("http://127.0.0.1:8000"):
            fails = 0 if ok(url) else fails + 1
            if fails >= FAIL_LIMIT:
                stop(proc, f"공개 주소가 {FAIL_LIMIT}분째 응답 없음")
        time.sleep(CHECK_EVERY)


def main() -> int:
    LOGS.mkdir(exist_ok=True)
    proc = subprocess.Popen([CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", "http://localhost:8000"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seen, gone = False, 0
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if GONE_RE.search(line):
            gone += 1
            if gone >= GONE_LIMIT:
                stop(proc, "Cloudflare가 임시 터널을 지움")
            continue
        m = URL_RE.search(line)
        if not m or seen:
            continue
        seen = True
        url = m[0]
        (LOGS / "public-url.txt").write_text(url + "\n", encoding="utf-8")
        threading.Thread(target=watch_public, args=(proc, url), daemon=True).start()
        if url != current_url():
            log(f"주소가 바뀜: {current_url()} → {url}")
            set_url(url)
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{SERVER_LABEL}"], check=False)
            log("회의실봇 서버를 새 주소로 다시 시작함 (관리자에게 슬랙 DM으로 알림)")
        else:
            log(f"주소 그대로: {url}")
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
