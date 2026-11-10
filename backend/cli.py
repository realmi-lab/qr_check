"""터미널에서 슬랙 없이 확인하는 CLI.

  uv run python -m backend.cli room now
  uv run python -m backend.cli room 무등 내일
  uv run python -m backend.cli room free 10/20 14:00 1h 6명
  uv run python -m backend.cli --as D0001 room my
  uv run python -m backend.cli --as D0001 checkin R19      QR 체크인 흉내 (토큰 확인 생략)
  uv run python -m backend.cli tick                        노쇼 처리·알림 한 번 실행 (알림은 화면에 출력)
  uv run python -m backend.cli qr                          회의실 QR 이미지 만들기

  --now 2026-10-17T10:03   가상 시계로 실행
"""

import argparse
import json
import sqlite3
import re
import sys
from datetime import datetime

from backend import checkin, clock, commands, rooms


def plain(mrkdwn: str) -> str:
    """슬랙 mrkdwn을 터미널용으로 단순화."""
    return re.sub(r"[*`]", "", mrkdwn)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="roombot")
    p.add_argument("--now", help="가상 시각 (예: 2026-10-17T10:03)")
    p.add_argument("--as", dest="as_emp", help="이 사번의 직원으로 실행 (예: D0001)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("room", help="/room 명령과 같음")
    r.add_argument("text", nargs="*")
    c = sub.add_parser("checkin", help="QR 체크인 흉내")
    c.add_argument("room_code")
    sub.add_parser("tick", help="노쇼 처리와 알림 한 번 실행")
    q = sub.add_parser("qr", help="회의실 QR 이미지 만들기")
    q.add_argument("--out", default="qr")
    d = sub.add_parser("db", help="SQLite 초기화·상태·백업")
    d.add_argument("operation", choices=["init", "status", "backup"])
    d.add_argument("destination", nargs="?")
    e = sub.add_parser("employee-add", help="로컬 직원 등록")
    for key in ("emp-no", "name", "email", "department", "position"):
        e.add_argument("--" + key, required=True)
    e.add_argument("--slack-user")
    room = sub.add_parser("room-add", help="로컬 회의실 등록")
    for key in ("code", "name"):
        room.add_argument("--" + key, required=True)
    room.add_argument("--floor", type=int, required=True)
    room.add_argument("--capacity", type=int, required=True)
    a = p.parse_args(argv)

    if a.now:
        at = datetime.fromisoformat(a.now)
        clock.freeze(at if at.tzinfo else at.replace(tzinfo=clock.TZ))

    from backend.db import backup, initialize, tx
    initialize()
    if a.cmd == "db":
        if a.operation == "backup":
            if not a.destination:
                p.error("db backup은 저장할 파일 경로가 필요해요.")
            print(backup(a.destination))
        else:
            with tx() as conn:
                counts = {name: conn.execute(f"SELECT count(*) AS n FROM {name}").fetchone()["n"]
                          for name in ("rooms", "employees", "reservations", "checkins", "noshow_cancellations", "reservation_cancellations")}
                counts["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()["integrity_check"]
            print(json.dumps(counts, ensure_ascii=False))
        return 0
    if a.cmd in ("employee-add", "room-add"):
        try:
            with tx(write=True) as conn:
                if a.cmd == "employee-add":
                    eid = conn.execute("INSERT INTO employees(emp_no,name,email,department,position) VALUES(?,?,?,?,?) RETURNING id",
                                       (a.emp_no, a.name, a.email, a.department, a.position)).fetchone()["id"]
                    if a.slack_user:
                        conn.execute("INSERT INTO slack_users(employee_id,slack_user_id) VALUES(?,?)", (eid,a.slack_user))
                    print(f"직원 등록 완료 #{eid}")
                else:
                    rid = conn.execute("INSERT INTO rooms(code,name,floor,capacity) VALUES(?,?,?,?) RETURNING id",
                                       (a.code, a.name, a.floor, a.capacity)).fetchone()["id"]
                    rooms.clear_cache()
                    print(f"회의실 등록 완료 #{rid}")
        except sqlite3.IntegrityError:
            print("중복된 값이나 잘못된 인원이에요. 기존 정보를 확인해 주세요.", file=sys.stderr)
            return 1
        return 0

    emp = None
    if a.as_emp:
        emp = rooms.employee_by_emp_no(a.as_emp)
        if not emp:
            print(f"사번 {a.as_emp} 직원이 없어요.", file=sys.stderr)
            return 1

    if a.cmd == "room":
        print(plain(commands.handle(" ".join(a.text), emp).text))
    elif a.cmd == "checkin":
        if not emp:
            print("--as 사번 이 필요해요.", file=sys.stderr)
            return 1
        found = rooms.find_rooms(a.room_code)
        if len(found) != 1:
            print(f"회의실 '{a.room_code}'을(를) 하나로 특정하지 못했어요.", file=sys.stderr)
            return 1
        from backend import messages
        res = checkin.check_in(found[0], emp["id"], source_ip="127.0.0.1", user_agent="cli")
        title, body = messages.checkin_page_text(res)
        print(f"[{res.kind}] {title}\n{body}")
    elif a.cmd == "tick":
        from backend import notifier, scheduler
        scheduler.run_once(notifier.ConsoleNotifier())
    elif a.cmd == "qr":
        from frontend import qr
        out = qr.generate(a.out)
        print(f"QR 이미지와 인쇄용 페이지를 만들었어요: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
