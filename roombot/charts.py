"""슬랙에 넣을 그림: 회의실 하루 타임라인, 전체 회의실 현황판.

2배 크기로 그려서 슬랙에서 또렷하게 보이게 함. 색은 로고 보라색을 기준으로 상태색을 통일.
"""

import io
from datetime import date, datetime, timedelta
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from roombot import clock, rooms

FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
_WEIGHT = {"regular": 0, "medium": 2, "semibold": 4, "bold": 6}

# 팔레트
INK, MUTED, FAINT, GRID, BG, CARD = "#1b0b22", "#6b6475", "#a39dab", "#ece8f1", "#ffffff", "#f7f5fa"
BRAND = "#5b2a86"
STATE_FILL = {
    "사용중": "#e5484d", "체크인 대기": "#f5a524", "노쇼 처리 대기": "#f5a524", "예정": "#8e6bc9",
    "종료": "#c9c6cf", "노쇼 취소": "#c9c6cf", "곧 시작": "#f5a524",
}
STATE_TEXT = {"종료": INK, "노쇼 취소": INK, "체크인 대기": INK, "노쇼 처리 대기": "#ffffff"}
FREE_FILL, FREE_LINE, FREE_TEXT = "#e7f6ee", "#30a46c", "#1a7f4b"
NOW = "#e5484d"
LEGEND = [("사용중", "사용중"), ("체크인 대기", "체크인 대기"), ("종료", "종료")]   # + 예약없음 (보라색 막대는 예정 회의)

OPEN_H, CLOSE_H = 9, 18


@lru_cache(maxsize=64)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size, index=_WEIGHT[weight])
    except OSError:
        return ImageFont.load_default(size)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _fit(d: ImageDraw.ImageDraw, text: str, f, max_w: float) -> str:
    """폭에 맞게 자르고 … 붙이기."""
    if d.textlength(text, font=f) <= max_w:
        return text
    while text and d.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text + "…" if text else ""


class _Axis:
    """09:00~18:00 를 x좌표로."""

    def __init__(self, day: date, x0: float, x1: float):
        self.start = datetime.combine(day, datetime.min.time(), clock.TZ) + timedelta(hours=OPEN_H)
        self.end = self.start + timedelta(hours=CLOSE_H - OPEN_H)
        self.x0, self.x1 = x0, x1

    def x(self, t: datetime) -> float:
        t = min(max(t, self.start), self.end)
        return self.x0 + (self.x1 - self.x0) * (t - self.start) / (self.end - self.start)

    def visible(self, a: datetime, b: datetime) -> bool:
        return b > self.start and a < self.end


def _grid(d, ax: _Axis, y0: float, y1: float, labels_y: float | None):
    for h in range(OPEN_H, CLOSE_H + 1):
        x = ax.x(ax.start + timedelta(hours=h - OPEN_H))
        d.line([(x, y0), (x, y1)], fill=GRID, width=2)
        if h < CLOSE_H:
            xm = ax.x(ax.start + timedelta(hours=h - OPEN_H, minutes=30))
            for yy in range(int(y0), int(y1), 12):   # 30분 점선
                d.line([(xm, yy), (xm, min(yy + 5, y1))], fill=GRID, width=1)
        if labels_y is not None:
            t = f"{h:02d}:00"
            w = d.textlength(t, font=font(22, "medium"))
            d.text((x - w / 2, labels_y), t, fill=FAINT, font=font(22, "medium"))


def _now_line(img, d, ax: _Axis, day: date, y0: float, y1: float, label: bool = True, label_y: float | None = None):
    now = clock.now()
    if now.date() != day or not (ax.start <= now <= ax.end):
        return
    x = ax.x(now)
    # 지난 시간은 살짝 흐리게
    veil = Image.new("RGBA", (int(x - ax.x0), int(y1 - y0)), (255, 255, 255, 110))
    img.alpha_composite(veil, (int(ax.x0), int(y0)))
    d.line([(x, y0 - 6), (x, y1)], fill=NOW, width=4)
    d.ellipse([x - 9, y0 - 15, x + 9, y0 + 3], fill=NOW)
    if label:
        t = f"지금 {now:%H:%M}"
        f = font(22, "bold")
        w = d.textlength(t, font=f)
        bx = min(max(x - w / 2 - 12, ax.x0), ax.x1 - w - 24)
        ly = label_y if label_y is not None else y0 - 52
        d.rounded_rectangle([bx, ly, bx + w + 24, ly + 32], radius=16, fill=NOW)
        d.text((bx + 12, ly + 2), t, fill="#fff", font=f)


def _bar(d, x0, y0, x1, y1, state: str, title: str, sub: str = ""):
    fill = STATE_FILL.get(state, STATE_FILL["예정"])
    r = min(14, (y1 - y0) / 2)
    d.rounded_rectangle([x0 + 2, y0, x1 - 2, y1], radius=r, fill=fill)
    if False:   # (노쇼 취소도 '종료'와 같은 회색으로 표시)
        for k in range(int(x0) - int(y1 - y0), int(x1), 14):
            d.line([(max(k, x0 + 4), y1 - max(0, x0 + 4 - k)), (min(k + (y1 - y0), x1 - 4), y0 + max(0, k + (y1 - y0) - (x1 - 4)))],
                   fill="#efd3d1", width=2)
        d.rounded_rectangle([x0 + 2, y0, x1 - 2, y1], radius=r, outline="#e5484d", width=2)
    color = STATE_TEXT.get(state, "#ffffff")
    w = x1 - x0 - 24
    if w < 64:      # 너무 짧은 막대는 글자 없이 (… 만 남는 것보다 깔끔)
        return
    h = y1 - y0
    tf = font(24 if h >= 70 else 21, "bold")
    t = _fit(d, title, tf, w)
    if sub and h >= 70:
        d.text((x0 + 14, y0 + h / 2 - 28), t, fill=color, font=tf)
        d.text((x0 + 14, y0 + h / 2 + 2), _fit(d, sub, font(20, "medium"), w), fill=color, font=font(20, "medium"))
    else:
        d.text((x0 + 14, y0 + h / 2 - 13), t, fill=color, font=tf)


def _free(d, x0, y0, x1, y1, text: str = "예약없음"):
    d.rounded_rectangle([x0 + 2, y0, x1 - 2, y1], radius=min(14, (y1 - y0) / 2), fill=FREE_FILL, outline=FREE_LINE, width=2)
    f = font(20, "semibold")
    if x1 - x0 - 24 > d.textlength(text, font=f):
        d.text((x0 + 14, y0 + (y1 - y0) / 2 - 12), text, fill=FREE_TEXT, font=f)


def _legend(d, x, y, items=LEGEND, with_free=True):
    f = font(21, "medium")
    for st, name in items:
        d.rounded_rectangle([x, y + 4, x + 26, y + 26], radius=7, fill=STATE_FILL[st],
                            outline=None, width=2)
        d.text((x + 34, y), name, fill=MUTED, font=f)
        x += 34 + d.textlength(name, font=f) + 30
    if with_free:
        d.rounded_rectangle([x, y + 4, x + 26, y + 26], radius=7, fill=FREE_FILL, outline=FREE_LINE, width=2)
        d.text((x + 34, y), "예약없음", fill=MUTED, font=f)
    return x


# ---------------------------------------------------------------
# 회의실 하루 타임라인
# ---------------------------------------------------------------

def room_day_png(room: dict, day: date) -> bytes:
    sched = rooms.room_schedule(room["id"], day)
    free = rooms.free_windows(sched, day) if sched else []
    W, H = 1600, 410
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 제목
    d.text((48, 30), room["label"], fill=INK, font=font(36, "bold"))
    sub = f"{room['floor']}층 · {room['capacity']}인"
    d.text((48, 80), sub, fill=MUTED, font=font(22, "medium"))
    wk = "월화수목금토일"[day.weekday()]
    dt = f"{day.month}월 {day.day}일 ({wk})"
    f = font(26, "bold")
    d.text((W - 48 - d.textlength(dt, font=f), 38), dt, fill=BRAND, font=f)
    live = [r for r in sched if r["state"] != "노쇼 취소"]
    fm = sum(int((b - a).total_seconds() // 60) for a, b in free)
    summ = f"예약 {len(live)}건 · 빈 시간 {fm // 60}시간 {fm % 60}분".replace(" 0분", "") if free else f"예약 {len(live)}건"
    f2 = font(24, "medium")
    d.text((W - 48 - d.textlength(summ, font=f2), 80), summ, fill=MUTED, font=f2)

    # 타임라인
    ax = _Axis(day, 70, W - 70)
    y0, y1 = 160, 280
    d.rounded_rectangle([ax.x0 - 14, y0 - 14, ax.x1 + 14, y1 + 14], radius=18, fill=CARD)
    _grid(d, ax, y0 - 6, y1 + 6, y1 + 26)
    for a, b in free:
        if ax.visible(a, b):
            _free(d, ax.x(a), y0 + 16, ax.x(b), y1 - 16)
    for r in sched:
        if ax.visible(r["start_at"], r["end_at"]):
            n = len(r["attendees"])
            _bar(d, ax.x(r["start_at"]), y0 + 8, ax.x(r["end_at"]), y1 - 8, r["state"], r["title"],
                 f"{r['organizer_name']} · {n}명")
    if not sched:
        _free(d, ax.x0, y0 + 16, ax.x1, y1 - 16, "하루 종일 비어 있어요")
    _now_line(img, d, ax, day, y0 - 6, y1 + 6)
    _legend(d, 70, H - 52)
    return _png(img.convert("RGB"))


# ---------------------------------------------------------------
# 전체 회의실 현황판
# ---------------------------------------------------------------

def board_png(day: date, only_floor: int | None = None) -> bytes:
    all_rooms = [r for r in rooms.all_rooms() if only_floor is None or r["floor"] == only_floor]
    schedules = rooms.room_schedules([r["id"] for r in all_rooms], day, include_attendees=False)
    if len(all_rooms) > 6:
        return _board_wide(day, all_rooms, schedules)
    floors = sorted({r["floor"] for r in all_rooms})
    ROW, FLOOR_ROW, TOP, BOTTOM, LEFT = 52, 40, 170, 80, 300
    W = 1600
    H = TOP + len(floors) * FLOOR_ROW + len(all_rooms) * ROW + BOTTOM
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    now = clock.now()
    title = "회의실 현황판" if only_floor is None else f"{only_floor}층 회의실 현황판"
    d.text((48, 30), title, fill=INK, font=font(36, "bold"))
    wk = "월화수목금토일"[day.weekday()]
    when = f"{day.month}월 {day.day}일 ({wk})" + ("  ·  빨간 선이 지금" if now.date() == day else "")
    d.text((48, 80), when, fill=MUTED, font=font(22, "medium"))

    ax = _Axis(day, LEFT, W - 60)
    y = TOP
    _grid(d, ax, TOP - 6, H - BOTTOM + 6, TOP - 40)
    for fl in floors:
        d.text((48, y + 8), f"{fl}층", fill=BRAND, font=font(26, "bold"))
        d.line([(48 + 70, y + 24), (LEFT - 24, y + 24)], fill=GRID, width=2)
        y += FLOOR_ROW
        for room in [r for r in all_rooms if r["floor"] == fl]:
            sched = schedules[room["id"]]
            d.rounded_rectangle([40, y + 4, W - 40, y + ROW - 4], radius=14, fill=CARD)
            busy_now = any(r["state"] in ("사용중", "체크인 대기", "노쇼 처리 대기") for r in sched)
            dot = STATE_FILL["사용중"] if busy_now else FREE_LINE
            d.ellipse([60, y + ROW / 2 - 9, 78, y + ROW / 2 + 9], fill=dot)
            d.text((92, y + ROW / 2 - 14), room["name"], fill=INK, font=font(24, "bold"))
            cap = f"{room['capacity']}인"
            fc = font(21, "medium")
            d.text((LEFT - 30 - d.textlength(cap, font=fc), y + ROW / 2 - 12), cap, fill=FAINT, font=fc)
            for r in sched:
                if ax.visible(r["start_at"], r["end_at"]):
                    _bar(d, ax.x(r["start_at"]), y + 12, ax.x(r["end_at"]), y + ROW - 12, r["state"], r["title"])
            y += ROW
    _now_line(img, d, ax, day, TOP - 6, H - BOTTOM + 6, label=False)
    if now.date() == day:      # '지금' 배지는 오른쪽 위에 고정
        t = f"지금 {now:%H:%M}"
        f = font(26, "bold")
        w = d.textlength(t, font=f)
        d.rounded_rectangle([W - 48 - w - 32, 40, W - 48, 80], radius=20, fill=NOW)
        d.text((W - 48 - w - 16, 44), t, fill="#fff", font=f)
    lx = _legend(d, 48, H - 56)
    f = font(21, "medium")
    d.ellipse([lx + 150, H - 49, lx + 168, H - 31], fill=STATE_FILL["사용중"])
    d.text((lx + 176, H - 56), "지금 사용중", fill=MUTED, font=f)
    return _png(img.convert("RGB"))


def _board_wide(day: date, all_rooms: list[dict], schedules: dict[int, list[dict]]) -> bytes:
    """회의실이 많을 때: 층을 두 줄(왼쪽·오른쪽)로 나눠 가로로 넓게 → 슬랙에서 작아지지 않게."""
    floors = sorted({r["floor"] for r in all_rooms})
    # 회의실 수가 비슷하게 두 덩어리로 나눔
    best, total, acc = 1, len(all_rooms), 0
    for i, fl in enumerate(floors[:-1]):
        acc += sum(1 for r in all_rooms if r["floor"] == fl)
        if abs(total / 2 - acc) < abs(total / 2 - sum(1 for r in all_rooms if r["floor"] in floors[:best])):
            best = i + 1
    cols = [floors[:best], floors[best:]]

    W, PAD, GAP = 2400, 56, 64
    ROW, FLOOR_ROW, TOP, BOTTOM, NAME = 54, 42, 190, 40, 330
    col_w = (W - PAD * 2 - GAP) / 2
    heights = [len(c) * FLOOR_ROW + sum(1 for r in all_rooms if r["floor"] in c) * ROW for c in cols]
    H = int(TOP + max(heights) + BOTTOM)
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    now = clock.now()
    wk = "월화수목금토일"[day.weekday()]
    d.text((PAD, 34), "회의실 현황판", fill=INK, font=font(44, "bold"))
    d.text((PAD + d.textlength("회의실 현황판", font=font(44, "bold")) + 24, 48), f"{day.month}월 {day.day}일 ({wk})",
           fill=MUTED, font=font(28, "medium"))
    if now.date() == day:
        t = f"지금 {now:%H:%M}"
        f = font(34, "bold")
        w = d.textlength(t, font=f)
        d.rounded_rectangle([W - PAD - w - 40, 44, W - PAD, 100], radius=28, fill=NOW)
        d.text((W - PAD - w - 20, 50), t, fill="#fff", font=f)
    # 범례 (제목 아래)
    x = PAD
    fl_ = font(24, "medium")
    for st, name in LEGEND + [("빈", "예약없음")]:
        fill = FREE_FILL if st == "빈" else STATE_FILL[st]
        d.rounded_rectangle([x, 112, x + 28, 136], radius=7, fill=fill,
                            outline=FREE_LINE if st == "빈" else None, width=2)
        d.text((x + 38, 106), name, fill=MUTED, font=fl_)
        x += 42 + d.textlength(name, font=fl_) + 40

    for ci, col in enumerate(cols):
        cx = PAD + ci * (col_w + GAP)
        ax = _Axis(day, cx + NAME, cx + col_w - 10)
        y = TOP
        # 시간 눈금: 3시간마다 글자, 1시간마다 선
        y_end = TOP + heights[ci]
        for h in range(OPEN_H, CLOSE_H + 1):
            xx = ax.x(ax.start + timedelta(hours=h - OPEN_H))
            d.line([(xx, TOP - 8), (xx, y_end)], fill=GRID, width=2)
            if (h - OPEN_H) % 3 == 0:
                t = f"{h:02d}:00"
                w = d.textlength(t, font=font(26, "medium"))
                d.text((xx - w / 2, TOP - 46), t, fill=FAINT, font=font(26, "medium"))
        for fl in col:
            d.text((cx, y + 8), f"{fl}층", fill=BRAND, font=font(32, "bold"))
            y += FLOOR_ROW
            for room in [r for r in all_rooms if r["floor"] == fl]:
                sched = schedules[room["id"]]
                d.rounded_rectangle([cx - 12, y + 5, cx + col_w + 4, y + ROW - 5], radius=16, fill=CARD)
                busy_now = any(r["state"] in ("사용중", "체크인 대기", "노쇼 처리 대기") for r in sched)
                d.ellipse([cx + 4, y + ROW / 2 - 11, cx + 26, y + ROW / 2 + 11],
                          fill=STATE_FILL["사용중"] if busy_now else FREE_LINE)
                d.text((cx + 40, y + ROW / 2 - 17), _fit(d, room["name"], font(28, "bold"), NAME - 130),
                       fill=INK, font=font(28, "bold"))
                cap = f"{room['capacity']}인"
                d.text((cx + NAME - 24 - d.textlength(cap, font=font(26, "medium")), y + ROW / 2 - 15), cap,
                       fill=FAINT, font=font(26, "medium"))
                for r in sched:
                    if ax.visible(r["start_at"], r["end_at"]):
                        _bar(d, ax.x(r["start_at"]), y + 14, ax.x(r["end_at"]), y + ROW - 14, r["state"], "")
                y += ROW
        _now_line(img, d, ax, day, TOP - 8, y_end, label=False)
    return _png(img.convert("RGB"))
