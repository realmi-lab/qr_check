"""/룸 명령어(/room도 같음) 해석과 답변 만들기 (슬랙 Block Kit). 슬랙봇·홈 탭·터미널 CLI가 같이 씀.
표기(아이콘·상태 이름)는 roombot/style.py 규칙을 따름.

  /룸                            도움말
  /룸 now | 현황                  지금 전체 회의실 현황 (층별 표)
  /룸 22층 [날짜]                 한 층의 현황 / 그 날 회의실별 빈 시간
  /룸 <회의실> [날짜]              회의실 하루 일정표 (예: /룸 BTC 내일, /룸 9A 10/20)
  /룸 free [날짜] [시각] [길이] [인원]   빈 회의실 찾기 (예: /룸 free 10/20 14:00 1h 6명)
  /룸 찾기                        빈 회의실 찾기 창 열기
  /룸 my | 내예약                 내 예약과 체크인 상태
  /룸 통계                        최근 7일 체크인·노쇼 통계
"""

import re
from datetime import date, datetime, time, timedelta

from roombot import clock, imgurl, rooms, style
from roombot.blocks import (B, I, S, Reply, actions, button, context, divider, grouped_select, header, image, rt,
                            section, select, table, tables)
from roombot.checkin import CHECKIN_OPENS_BEFORE, NOSHOW_GRACE
from roombot.config import settings
from roombot.style import icon

WEEKDAYS = "월화수목금토일"
ACTIVE = ("사용중", "체크인 대기", "노쇼 처리 대기")


# ---------------------------------------------------------------
# 표시 형식
# ---------------------------------------------------------------

def fmt_day(d: date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def hm(dt: datetime) -> str:
    return dt.astimezone(clock.TZ).strftime("%H:%M")


def mins(td: timedelta) -> str:
    """남은 시간을 사람 말로: 3분 / 1시간 / 1시간 20분 (분은 올림, 최소 1분)."""
    m = max(1, -(-int(td.total_seconds()) // 60))
    h, m = divmod(m, 60)
    return f"{h}시간 {m}분" if h and m else f"{h}시간" if h else f"{m}분"


def span(a: datetime, b: datetime) -> str:
    return f"{hm(a)}–{hm(b)}"


def bspan(a: datetime, b: datetime) -> str:
    """굵은 시간 (*09:00–10:00*)."""
    return f"*{span(a, b)}*"


TITLE_SHORT = 14    # 좁은 칸(현황 '지금·다음', 홈 다음 회의)
TITLE_MID = 20      # 내 예약 표, 창 안 목록


def clip(text: str, n: int) -> str:
    """회의명이 길면 n글자에서 자르고 … (일정표 표·알림에서는 자르지 않고 줄바꿈)."""
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def names(attendees: list[dict], limit: int = 6) -> str:
    ns = [a["name"] for a in attendees]
    more = f" 외 {len(ns) - limit}명" if len(ns) > limit else ""
    return ", ".join(ns[:limit]) + more


def people_line(attendees: list[dict], organizer: str, limit: int = 5) -> str:
    """👤 예약자(굵게) · 참석자들 — 예약자를 맨 앞에."""
    others = [a["name"] for a in attendees if a["name"] != organizer]
    more = f" 외 {len(others) - limit}명" if len(others) > limit else ""
    rest = (" · " + ", ".join(others[:limit]) + more) if others else ""
    return f"{style.PERSON} *{organizer}*{rest}"


def room_value(code: str, day: date) -> str:
    return f"{code}|{day.isoformat()}"


def free_value(start: datetime, dur: timedelta, cap: int) -> str:
    return f"{start.isoformat()}|{int(dur.total_seconds() // 60)}|{cap}"


def room_menu(day: date, action_id: str = "room_pick") -> dict:
    """회의실 일정 보기 메뉴 하나 (층별로 묶음)."""
    groups = []
    for fl in rooms.floors():
        groups.append((f"{fl}층", [(f"{r['name']} · {r['capacity']}인", room_value(r["code"], day))
                                   for r in rooms.all_rooms() if r["floor"] == fl]))
    return grouped_select("회의실 선택", action_id, groups)


def quick_actions(*, finder: bool = True, my: bool = True, now: bool = True, stats: bool = False,
                  menu_day: date | None = None) -> dict | None:
    return actions(
        room_menu(menu_day) if menu_day else None,
        button("빈방 찾기", "open_finder", style="primary") if finder else None,
        button("전체 현황", "show_now") if now else None,
        button("내 예약", "show_my") if my else None,
        button("통계", "show_stats") if stats else None,
    )


# ---------------------------------------------------------------
# 입력 해석
# ---------------------------------------------------------------

def parse_date(tok: str, today: date | None = None) -> date | None:
    today = today or clock.now().date()
    t = tok.strip()
    rel = {"오늘": 0, "today": 0, "내일": 1, "tomorrow": 1, "모레": 2}
    if t.lower() in rel:
        return today + timedelta(days=rel[t.lower()])
    for pat in (r"(\d{4})-(\d{1,2})-(\d{1,2})$", r"(\d{4})/(\d{1,2})/(\d{1,2})$"):
        m = re.match(pat, t)
        if m:
            return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2})[/.-](\d{1,2})$", t) or re.match(r"(\d{1,2})월\s*(\d{1,2})일$", t)
    if m:
        return _safe_date(today.year, int(m[1]), int(m[2]))
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_time(tok: str) -> time | None:
    t = tok.strip()
    m = re.match(r"(\d{1,2}):(\d{2})$", t) or re.match(r"(\d{1,2})시(?:\s*(\d{1,2})분)?$", t)
    if not m:
        m = re.match(r"(\d{2})(\d{2})$", t)
    if not m:
        return None
    h, mi = int(m[1]), int(m[2] or 0)
    return time(h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None


def parse_duration(tok: str) -> timedelta | None:
    t = tok.strip().lower()
    m = re.match(r"^(?:(\d+)\s*(?:h|시간))?\s*(?:(\d+)\s*(?:m|min|분))?$", t)
    if not m or not (m[1] or m[2]):
        return None
    d = timedelta(hours=int(m[1] or 0), minutes=int(m[2] or 0))
    return d if d > timedelta(0) else None


def parse_capacity(tok: str) -> int | None:
    m = re.match(r"^(\d+)\s*(?:명|인|people|p)$", tok.strip().lower())
    return int(m[1]) if m else None


# ---------------------------------------------------------------
# 명령 처리
# ---------------------------------------------------------------

def handle(text: str, employee: dict | None, slack_user_id: str | None = None) -> Reply:
    words = (text or "").split()
    if not words or words[0].lower() in ("help", "도움말", "?"):
        return help_reply()
    cmd, args = words[0].lower(), words[1:]
    if cmd in ("now", "현황", "지금"):
        return status_reply()
    if cmd in ("free", "빈방", "빈회의실", "빈"):
        return free_reply(args)
    if cmd in ("찾기", "find", "search"):
        return free_reply(args) if args else Reply(open_finder=True, fallback="빈 회의실 찾기")
    if cmd in ("my", "내예약", "내", "me"):
        return my_reply(employee)
    if cmd in ("통계", "stats", "노쇼"):
        return stats_reply()
    if cmd in ("link", "연결"):
        return link_reply(args, slack_user_id)
    from roombot import admin
    if cmd == admin.KEYWORD and admin.is_admin(slack_user_id):
        return admin.preview(args)
    return room_reply(words)


def help_reply() -> Reply:
    r = Reply(fallback="회의실봇 사용법")
    r.add(
        header("회의실봇 사용법"),
        section("", fields=[
            "*지금 현황*\n`/룸 지금`\n`/룸 22층`",
            "*회의실 일정표*\n`/룸 9A`\n`/룸 BTC 내일`",
            "*빈 회의실 찾기*\n`/룸 빈방 오후 2시 1시간 6명`\n`/룸 찾기` (창에서 고르기)",
            "*내 예약 · 통계*\n`/룸 내예약`\n`/룸 통계`",
        ]),
        divider(),
        section("*QR 체크인 규칙*\n"
                "• 회의실 입구 QR을 휴대폰 카메라로 찍으면 회의실 채널이 열려요 → *[✅ 체크인]* (처음이면 *[채널 참여]* 먼저)\n"
                "• 시작 *10분 전*부터, 참석자 중 *누구나* 찍으면 돼요\n"
                "• 시작 후 *10분* 안에 아무도 체크인하지 않으면 예약이 *자동 취소*돼요\n"
                "• 다른 회의실 QR을 찍으면 체크인되지 않고, 내 예약 회의실을 알려줘요\n"
                "• 예약자 대신 왔다면(휴가 등) 다우오피스에서 그 예약의 *참석자로 추가*되면 체크인할 수 있어요"),
        context("상태 표시   " + style.legend()),
        context("회의실   " + "   ".join(
            f"*{fl}층* " + " · ".join(x["name"] for x in rooms.all_rooms() if x["floor"] == fl) for fl in rooms.floors())),
        quick_actions(),
    )
    return r


# ---------------------------------------------------------------
# 현황 (층별 표)
# ---------------------------------------------------------------

def status_counts(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        key = style.label(r["state"])
        counts[key] = counts.get(key, 0) + 1
    out = []
    for s in ("사용중", "체크인 대기", "비어있음"):
        if counts.get(style.label(s)):
            out.append(f"{style.badge(s)} *{counts[style.label(s)]}*")
    return "   ".join(out)


def _room_cells(r: dict, today: date) -> list[str]:
    """[회의실 칸, 지금·다음 칸]"""
    left = f"{icon(r['state'])} *{r['short_name']}*  ·  {r['capacity']}인"
    if r["current_reservation_id"]:
        right = f"~{hm(r['current_end_at'])}  {clip(r['current_title'], TITLE_SHORT)}\n{style.PERSON} {r['current_organizer']}"
        if r["state"] != "사용중":
            right += f"  ·  {style.label(r['state'])}"
    elif r["next_start_at"] and r["next_start_at"].date() == today:
        right = f"다음 *{hm(r['next_start_at'])}*  {clip(r['next_title'], TITLE_SHORT)}"
        if r["state"] == "곧 시작":
            right += f"\n{style.label('곧 시작')}"
    else:
        right = "_오늘 남은 예약 없음_"
    return [left, right]


def floor_sections(rows: list[dict], today: date) -> list[dict]:
    """층마다 한 덩어리: 굵은 층 이름 + [회의실 | 지금·다음] 2칸 표."""
    out = []
    for fl in sorted({r["floor"] for r in rows}):
        cells = [c for r in rows if r["floor"] == fl for c in _room_cells(r, today)]
        for i in range(0, len(cells), 10):      # 한 덩어리에 칸은 10개까지
            out.append(section(f"*{fl}층*" if i == 0 else "", fields=cells[i:i + 10]))
        out.append(divider())
    return out


def _status_now_cell(r: dict, today: date) -> dict:
    if r["current_reservation_id"]:
        extra = f" · {style.label(r['state'])}" if r["state"] != "사용중" else ""
        return rt((f"~{hm(r['current_end_at'])} ", B), clip(r["current_title"], TITLE_SHORT), (f"  {r['current_organizer']}{extra}", I))
    if r["next_start_at"] and r["next_start_at"].date() == today:
        return rt("다음 ", (hm(r["next_start_at"]), B), f"  {clip(r['next_title'], TITLE_SHORT)}")
    return rt(("오늘 남은 예약 없음", I))


def status_tables(rows: list[dict], today: date) -> list[dict]:
    """인원을 회의실 칸에 합친 3열 표."""
    groups = []
    for fl in sorted({r["floor"] for r in rows}):
        fr = sorted([r for r in rows if r["floor"] == fl], key=lambda x: x["room_id"])
        groups.append((f"{fl}층", [[rt((r["short_name"], B), (f" · {r['capacity']}인", I)),
                                                 rt(style.badge(r["state"])), _status_now_cell(r, today)] for r in fr]))
    return tables(["회의실", "상태", "지금 · 다음"], groups,
                  wrap=[False, False, True])


def floor_summary(rows: list[dict]) -> dict:
    """홈에서는 층별 수량만 보여 주고 상세 조회로 연결."""
    summary = []
    for floor in sorted({r["floor"] for r in rows}):
        floor_rows = [r for r in rows if r["floor"] == floor]
        using = sum(r["state"] == "사용중" for r in floor_rows)
        waiting = sum(r["state"] in ("체크인 대기", "노쇼 처리 대기", "곧 시작") for r in floor_rows)
        free = sum(r["state"] == "비어있음" for r in floor_rows)
        summary.append([rt((f"{floor}층", B)), rt(str(using)), rt(str(waiting)), rt(str(free))])
    return table(["층", "사용", "대기", "빈방"], summary, align=["left", "right", "right", "right"])


def status_reply(only_floor: int | None = None, surface: str = "message") -> Reply:
    now = clock.now()
    rows = rooms.status_now()
    if only_floor is not None:
        rows = [r for r in rows if r["floor"] == only_floor]
    title = f"{only_floor}층 회의실 현황" if only_floor is not None else "회의실 현황"
    chart_title = f"{only_floor}층 회의실 일정" if only_floor is not None else "전체 회의실 일정"
    free_now = sum(1 for r in rows if r["state"] == "비어있음")
    reply = Reply(fallback=f"회의실 현황 {fmt_day(now.date())} {hm(now)} · 비어있음 {free_now}곳")
    reply.add(header(title),
              context(f"{style.DATE} *{fmt_day(now.date())} {hm(now)}* 기준", status_counts(rows)),
              image(imgurl.src("board", chart_title, d=now.date().isoformat(), f=only_floor),
                    f"{chart_title} — 09:00–18:00, 회의명·예약 시간·상태"),
              actions(room_menu(now.date()), button("빈방 찾기", "open_finder", style="primary")))
    if surface == "modal":
        reply.add(divider(), *floor_sections(rows, now.date()))
    else:
        reply.add(*status_tables(rows, now.date()))
    reply.add(context("상태 표시   " + style.legend("사용중", "체크인 대기", "노쇼 처리 대기", "곧 시작", "비어있음")),
              quick_actions(finder=False, now=False))
    return reply


# ---------------------------------------------------------------
# 회의실 일정
# ---------------------------------------------------------------

def _pick_room(query: str) -> tuple[dict | None, Reply | None]:
    found = rooms.find_rooms(query)
    if len(found) == 1:
        return found[0], None
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:층|f)\s*", query.lower())
    if found and m and all(r["floor"] == int(m[1]) for r in found):
        return None, status_reply(only_floor=int(m[1]))
    today = clock.now().date()
    if not found:
        r = Reply(fallback=f"'{query}' 회의실을 찾지 못했어요")
        r.add(section(f"{style.WARN} *'{query}'* 회의실을 찾지 못했어요. 메뉴에서 골라주세요."),
              actions(room_menu(today)),
              context("예: `/룸 9A` · `/룸 BTC` · `/룸 bitcoin` · `/룸 22층`"))
        return None, r
    r = Reply(fallback=f"'{query}' 회의실이 {len(found)}개예요")
    r.add(section(f"*'{query}'*에 해당하는 회의실이 {len(found)}개예요.",
                  accessory=select("회의실 고르기", "room_pick",
                                   [(f"{x['label']} · {x['capacity']}인", room_value(x["code"], today)) for x in found])))
    return None, r


def room_reply(words: list[str]) -> Reply:
    day = parse_date(words[-1]) if len(words) > 1 else None
    query = " ".join(words[:-1] if day else words)
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:층|f)\s*", query.lower())
    if day and m and rooms.find_rooms(query):
        return floor_day_reply(int(m[1]), day)
    room, err = _pick_room(query)
    if err:
        return err
    return schedule_reply(room, day or clock.now().date())


def floor_day_reply(floor: int, day: date) -> Reply:
    """한 층의 그 날 회의실별 예약 수와 빈 시간 (/룸 22층 내일)."""
    off = rooms.is_holiday(day)
    reply = Reply(fallback=f"{floor}층 {fmt_day(day)} 회의실 일정")
    reply.add(header(f"{floor}층 · {fmt_day(day)}"),
              context("회의실별 예약 수와 빈 시간" + (f" · {off}" if off else "")), divider())
    for room in [r for r in rooms.all_rooms() if r["floor"] == floor]:
        sched = rooms.room_schedule(room["id"], day)
        live = [r for r in sched if r["state"] != "노쇼 취소"]
        free = rooms.free_windows(sched, day)
        free_txt = "  ·  ".join(bspan(a, b) for a, b in free) if free else "_남은 빈 시간 없음_"
        reply.add(section(f"{style.ROOM} *{room['label']}*  ·  {room['capacity']}인  ·  예약 {len(live)}건\n"
                          f"{icon('비어있음')} {free_txt}",
                          accessory=button("일정표", f"room_schedule:{room['code']}", room_value(room["code"], day))))
    reply.add(actions(button(f"{style.FIND} 빈 회의실 찾기", "open_finder", style="primary")))
    return reply


def _status_note(r: dict) -> str:
    """일정표 왼쪽 칸 아래 줄: 상태가 의미 있을 때만 (예정은 비움)."""
    state = r["state"]
    if r["first_checkin_at"] and state in ("사용중", "종료"):
        who = r.get("checked_in_by")
        return f"{style.OK} {who + ' ' if who else ''}{hm(r['first_checkin_at'])} 체크인"
    if state == "체크인 대기":
        return f"{style.label(state)} · {hm(r['start_at'] + NOSHOW_GRACE)}까지"
    if state == "노쇼 처리 대기":
        return f"{style.label(state)} · {hm(r['start_at'] + NOSHOW_GRACE)} 지남"
    if state in ("노쇼 취소", "사용중", "종료"):
        return style.label(state)
    return ""


def _state_cell(r: dict, note: str) -> str:
    """표의 상태 칸: 첫 줄 배지, 둘째 줄 체크인 정보 (배지와 같은 말은 반복하지 않음)."""
    label = style.label(r["state"])
    extra = note.removeprefix(f"{label} · ") if note and note != label else ""
    return style.badge(r["state"]) + (f"\n{style.DEADLINE} {extra}" if extra and extra != note else f"\n{extra}" if extra else "")


def _schedule_rows(sched: list[dict], free: list[tuple[datetime, datetime]]) -> list[tuple[datetime, str, dict]]:
    rows = [(r["start_at"], "meeting", r) for r in sched] + [(a, "free", {"start_at": a, "end_at": b}) for a, b in free]
    return sorted(rows, key=lambda x: x[0])


def schedule_tables(sched: list[dict], free: list[tuple[datetime, datetime]]) -> list[dict]:
    """[시간 | 상태 | 회의 | 예약자 · 참석자] 표를 13시 이전·이후로 나눔. 빈 시간도 시간 순서대로."""
    parts = [("13시 이전", 13), ("13시 이후", 24)]
    groups = [(title, []) for title, _ in parts]
    for _, kind, r in _schedule_rows(sched, free):
        idx = next(i for i, (_, end_h) in enumerate(parts) if r["start_at"].hour < end_h)
        when = rt((span(r["start_at"], r["end_at"]), B))
        if kind == "free":
            groups[idx][1].append([when, rt(style.badge("비어있음")), rt(""), rt("")])
            continue
        note = _status_note(r)
        state = _state_cell(r, note)
        others = [a["name"] for a in r["attendees"] if a["name"] != r["organizer_name"]]
        guests = ", ".join(others[:6]) + (f" 외 {len(others) - 6}명" if len(others) > 6 else "")
        title = (r["title"], S) if r["state"] == "노쇼 취소" else (r["title"], B)
        groups[idx][1].append([when, rt(state), rt(title),
                               rt((r["organizer_name"], B), (f", {guests}" if guests else ""),
                                  (f"  ({len(r['attendees'])}명)", I))])
    return tables(["시간", "상태", "회의", "예약자 · 참석자"], groups, wrap=[False, True, True, True])


def schedule_reply(room: dict, day: date, surface: str = "message") -> Reply:
    """회의실 하루 일정: 위에 타임라인 그림, 아래에 표 (창에서는 2칸 목록)."""
    sched = rooms.room_schedule(room["id"], day)
    today = clock.now().date()
    off = rooms.is_holiday(day)
    free = rooms.free_windows(sched, day) if sched else []
    live = [r for r in sched if r["state"] != "노쇼 취소"]
    free_min = sum(int((b - a).total_seconds() // 60) for a, b in free)
    free_total = f"{free_min // 60}시간" + (f" {free_min % 60}분" if free_min % 60 else "") if free_min >= 60 else f"{free_min}분"

    meta = f"{style.PEOPLE} {room['capacity']}인   {style.DATE} *{fmt_day(day)}*" + (f" {off}" if off else "")
    summary = f"예약 *{len(live)}건*" + (f"   빈 시간 *{len(free)}곳* (총 {free_total})" if free else "")
    reply = Reply(fallback=f"{room['label']} {fmt_day(day)} 일정 {len(live)}건")
    reply.add(header(f"{room['label']}"), context(meta, summary),
              image(imgurl.src("room", f"{room['label']} 타임라인", r=room["code"], d=day.isoformat()), f"{room['label']} {fmt_day(day)} 타임라인"))

    if not sched:
        reply.add(section(f"{icon('비어있음')} 이 날은 예약이 없어요. *09:00–18:00* 모두 비어 있어요."))
    elif surface != "modal":
        reply.add(*schedule_tables(sched, free))
    else:
        # 창(modal)에서는 표를 못 써서 2칸 목록 — 줄마다 가로선을 넣어 구분
        reply.add(divider(), section("", fields=["*시간*", "*회의 · 참석자*"]), divider())
        for _, kind, r in _schedule_rows(sched, free)[:40]:
            if kind == "free":
                reply.add(section("", fields=[f"{icon('비어있음')} {bspan(r['start_at'], r['end_at'])}", "_예약없음_"]),
                          divider())
                continue
            note = _status_note(r)
            t = clip(r["title"], TITLE_MID)
            title = f"~{t}~" if r["state"] == "노쇼 취소" else f"*{t}*"
            reply.add(section("", fields=[
                f"{icon(r['state'])} {bspan(r['start_at'], r['end_at'])}" + (f"\n{note}" if note else ""),
                f"{title}\n{people_line(r['attendees'], r['organizer_name'])}"]), divider())
    if sched and day >= today and not free:
        reply.add(context("남은 빈 시간이 없어요."))

    prev_d, next_d = day - timedelta(days=1), day + timedelta(days=1)
    reply.add(actions(
        button(f"◀ {fmt_day(prev_d)}", "day_nav:prev", room_value(room["code"], prev_d)),
        button("오늘", "day_nav:today", room_value(room["code"], today)) if day != today else None,
        button(f"{fmt_day(next_d)} ▶", "day_nav:next", room_value(room["code"], next_d)),
        button("빈방 찾기", "open_finder"),
        room_menu(day, action_id="room_switch"),   # 같은 날짜의 다른 회의실 일정으로 바꿔 보기
    ))
    return reply


# ---------------------------------------------------------------
# 빈 회의실
# ---------------------------------------------------------------

def free_reply(args: list[str]) -> Reply:
    day, start_t, dur, cap = None, None, timedelta(hours=1), 1
    pm, prev_hour_only = None, False
    for a in args:
        if a in ("오전", "오후"):                      # 오후 3시 → 15:00
            pm = a == "오후"
            continue
        if prev_hour_only and (m := re.match(r"^(\d{1,2})분$", a)) and int(m[1]) < 60:
            start_t = start_t.replace(minute=int(m[1]))  # "3시 30분" → 3:30 (30분 동안이 아니라)
            prev_hour_only = False
            continue
        prev_hour_only = False
        if (d := parse_date(a)) is not None:
            day = d
        elif (t := parse_time(a)) is not None:
            start_t = t
            prev_hour_only = a.endswith("시")
        elif (c := parse_capacity(a)) is not None:
            cap = c
        elif (du := parse_duration(a)) is not None:
            dur = du
        else:
            return Reply(fallback="입력을 이해하지 못했어요").add(
                section(f"🤔 *'{a}'*을(를) 이해하지 못했어요."),
                context("예: `/룸 빈방 10/20 오후 2시 1시간 6명` · `/룸 빈방 15시 30분`"),
                actions(button("🔍 창에서 골라 찾기", "open_finder", style="primary")))

    if start_t is not None and pm is not None:
        if pm and start_t.hour < 12:
            start_t = start_t.replace(hour=start_t.hour + 12)
        elif not pm and start_t.hour == 12:
            start_t = start_t.replace(hour=0)
    if day is None and start_t is None:
        start = rooms.default_start()
    else:
        start = datetime.combine(day or clock.now().date(), start_t or time(9, 0), clock.TZ)
    return free_search(start, dur, cap)


def free_search(start: datetime, dur: timedelta, cap: int = 1, surface: str = "message") -> Reply:
    end = start + dur
    found = rooms.free_rooms(start, end, cap)
    cond = f"{style.DATE} *{fmt_day(start.date())} {span(start, end)}*" + (f"   {style.PEOPLE} {cap}인 이상" if cap > 1 else "")
    reply = Reply(fallback=f"빈 회의실 {len(found)}곳 — {fmt_day(start.date())} {span(start, end)}")
    reply.add(header("빈 회의실"),
              context(cond, f"*{len(found)}곳* 비어 있어요" if found else "비어 있는 곳이 없어요"))
    if start < clock.now() - timedelta(minutes=1):
        reply.add(context(f"{style.WARN} 이미 지난 시간이에요."))
    reply.add(divider())

    if found and surface != "modal":
        groups = []
        for fl in sorted({r["floor"] for r in found}):
            groups.append((f"{style.FLOOR} {fl}층", [
                [rt((r["name"], B)), rt(f"{r['capacity']}인"),
                 rt(("다음 ", None), (hm(r["next_start_at"]), B))
                 if r["next_start_at"] and r["next_start_at"].date() == start.date() else rt(("이후 예약 없음", I))]
                for r in found if r["floor"] == fl]))
        reply.add(*tables(["회의실", "인원", "다음 예약"], groups,
                          wrap=[False, False, False], align=["left", "right", "left"]),
                  actions(select(f"{style.ROOM} 일정표 보기", "room_pick",
                                 [(f"{r['label']} · {r['capacity']}인", room_value(r["code"], start.date())) for r in found[:100]])))
    else:
        for r in found[:20]:
            nxt = r["next_start_at"]
            tail = f"다음 예약 *{hm(nxt)}*" if nxt and nxt.date() == start.date() else "_이후 예약 없음_"
            reply.add(section(f"{icon('비어있음')} *{r['label']}*  ·  {r['capacity']}인\n{tail}",
                              accessory=button("일정표", f"room_schedule:{r['code']}", room_value(r["code"], start.date()))))
        if len(found) > 20:
            reply.add(context(f"외 {len(found) - 20}곳"))

    if not found:
        alts = []
        for minutes in (30, -30, 60, -60, 90, 120, 150, 180):
            s = start + timedelta(minutes=minutes)
            close = datetime.combine(s.date(), rooms.CLOSE_TIME, clock.TZ)
            if s < rooms.default_start() or s.date() != start.date():
                continue
            if s.time() < rooms.OPEN_TIME or s + dur > close:
                continue
            n = len(rooms.free_rooms(s, s + dur, cap))
            if n:
                alts.append(button(f"{span(s, s + dur)} · {n}곳", f"free_at:{len(alts)}", free_value(s, dur, cap)))
            if len(alts) >= 4:
                break
        reply.add(section("조건에 맞는 빈 회의실이 없어요." + (" 이 시간대는 어때요?" if alts else "")))
        reply.add(actions(*alts))
    reply.add(actions(button("조건 바꾸기", "open_finder")))
    return reply


# ---------------------------------------------------------------
# 내 예약 / 통계 / 연결
# ---------------------------------------------------------------

def _not_linked() -> Reply:
    msg = "슬랙 계정이 다우오피스 직원 정보와 연결되지 않았어요.\n슬랙 프로필 이메일이 다우오피스 이메일과 같은지 확인해 주세요."
    r = Reply(fallback="계정 연결 필요").add(section(f"{style.WARN} {msg}"))
    if settings.demo_mode:
        r.add(context("(시연 모드) `/룸 link D0001` 처럼 사번으로 연결할 수 있어요."))
    return r


def _checkin_hint(r: dict, now: datetime) -> str:
    if r["state"] == "사용중":
        return f"{style.OK} 체크인 완료 · 사용중"
    if r["state"] == "체크인 대기":
        return f"{style.DEADLINE} *{hm(r['start_at'] + NOSHOW_GRACE)}까지 QR 체크인하세요*"
    if r["start_at"] - now <= CHECKIN_OPENS_BEFORE:
        return f"{style.QR} 지금 QR로 체크인할 수 있어요"
    return f"{style.QR} {hm(r['start_at'] - CHECKIN_OPENS_BEFORE)}부터 QR 체크인"


def next_meeting_block(employee: dict) -> list[dict]:
    """홈 탭 '내 다음 회의' 카드."""
    now = clock.now()
    upcoming = [r for r in rooms.my_reservations(employee["id"], days=0)
                if r["start_at"].date() == now.date() and r["end_at"] > now and r["state"] != "노쇼 취소"]
    if not upcoming:
        return [section("오늘 남은 회의가 없어요.")]
    r = upcoming[0]
    more = f"\n_오늘 {len(upcoming) - 1}건 더_" if len(upcoming) > 1 else ""
    return [section(f"*다음 회의*   {bspan(r['start_at'], r['end_at'])}   *{clip(r['title'], TITLE_MID)}*\n"
                    f"{style.ROOM} {r['room_name']}  ·  {'예약자' if r['i_am_organizer'] else '참석'}\n"
                    f"{_checkin_hint(r, now)}{more}",
                    accessory=button("일정표", "room_schedule", room_value(r["room_code"], r["start_at"].date())))]


def my_reply(employee: dict | None, surface: str = "message") -> Reply:
    if not employee:
        return _not_linked()
    rows = rooms.my_reservations(employee["id"])
    reply = Reply(fallback=f"{employee['name']}님의 예약 {len(rows)}건")
    reply.add(header(f"{employee['name']}님의 예약"),
              context(f"{employee['department']}   오늘부터 7일   *{len(rows)}건*"))
    if not rows:
        return reply.add(section("예약이 없어요."), quick_actions(my=False))
    if surface != "modal":
        by_day: dict[date, list] = {}
        for r in rows:
            note = _status_note(r) if r["state"] != "종료" else ""
            st = _state_cell(r, note)
            if r["i_checked_in"]:
                st += f"\n{style.OK} 내가 체크인"
            t = clip(r["title"], TITLE_MID)
            title = (t, S) if r["state"] == "노쇼 취소" else (t, B)
            by_day.setdefault(r["start_at"].date(), []).append(
                [rt((span(r["start_at"], r["end_at"]), B)), rt(title), rt(r["room_name"]),
                 rt("예약자" if r["i_am_organizer"] else "참석"), rt(st)])
        reply.add(*tables(["시간", "회의", "회의실", "역할", "상태"],
                          [(f"{style.DATE} {fmt_day(d)}", rs) for d, rs in by_day.items()],
                          wrap=[False, True, False, False, True])[:44],
                  quick_actions(my=False))
        return reply
    reply.add(divider(), section("", fields=["*시간*", "*회의 · 회의실*"]), divider())
    last_day, count = None, 0
    for r in rows:
        if count >= 30:      # 창은 덩어리 100개까지 (줄 + 가로선)
            reply.add(context(f"외 {len(rows) - count}건"))
            break
        d = r["start_at"].date()
        if d != last_day:
            reply.add(context(f"{style.DATE} *{fmt_day(d)}*"))
            last_day = d
        note = _status_note(r) if r["state"] != "종료" else ""
        mine = f"\n{style.OK} 내가 체크인" if r["i_checked_in"] else ""
        t = clip(r["title"], TITLE_MID)
        title = f"~{t}~" if r["state"] == "노쇼 취소" else f"*{t}*"
        reply.add(section("", fields=[
            f"{icon(r['state'])} {bspan(r['start_at'], r['end_at'])}" + (f"\n{note}" if note else "") + mine,
            f"{title}\n{style.ROOM} {r['room_name']}  ·  {'예약자' if r['i_am_organizer'] else '참석'}"]), divider())
        count += 1
    reply.add(divider(), quick_actions(my=False))
    return reply


def stats_reply(days: int = 7) -> Reply:
    s = rooms.stats(days)
    total, used, noshow = s["total"], s["used"], s["noshow"]
    rate = f"{noshow / total * 100:.0f}%" if total else "-"
    reply = Reply(fallback=f"최근 {days}일 노쇼 {noshow}건 / 예약 {total}건")
    reply.add(header(f"체크인 · 노쇼 통계 (최근 {days}일)"),
              context(f"{fmt_day(s['since'].date())} {hm(s['since'])} 이후, 노쇼 규칙이 적용된 예약 기준"),
              section("", fields=[f"*예약*\n{total}건", f"*{icon('사용중')} 체크인*\n{used}건",
                                  f"*{icon('노쇼 취소')} 노쇼 자동취소*\n{noshow}건", f"*노쇼율*\n{rate}"]))
    if s["by_room"]:
        reply.add(section("*노쇼가 많은 회의실*\n" + "\n".join(
            f"{i + 1}. {r['room_name']} — {r['noshow']}건 / {r['total']}건" for i, r in enumerate(s["by_room"]))))
    if s["by_organizer"]:
        reply.add(section("*노쇼가 많은 예약자*\n" + "\n".join(
            f"{i + 1}. {r['organizer_name']} — {r['noshow']}건" for i, r in enumerate(s["by_organizer"]))))
    if not total:
        reply.add(section("아직 집계할 예약이 없어요."))
    return reply


def link_reply(args: list[str], slack_user_id: str | None) -> Reply:
    if not settings.demo_mode:
        return Reply().add(section("계정 연결은 슬랙 프로필 이메일로 자동으로 돼요."))
    if not args or not slack_user_id:
        return Reply().add(section("사용법: `/룸 link D0001` (사번)"))
    emp = rooms.employee_by_emp_no(args[0])
    if not emp:
        return Reply().add(section(f"사번 {args[0]} 직원을 찾지 못했어요."))
    rooms.link_slack_user(emp["id"], slack_user_id)
    return Reply(fallback="계정 연결됨").add(
        section(f"{style.OK} (시연 모드) 이 슬랙 계정을 *{emp['name']}* ({emp['department']}, {emp['emp_no']})으로 연결했어요."))
