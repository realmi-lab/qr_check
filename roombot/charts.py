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

class _ScheduleCanvas:
    """논리 좌표의 두 배 크기로 글자와 선을 직접 그리는 PNG 캔버스."""

    scale = 2

    def __init__(self, width: int, height: int):
        self.img = Image.new("RGB", (width * self.scale, height * self.scale), "#f6f7fb")
        self.draw = ImageDraw.Draw(self.img)

    def box(self, bounds, fill, radius=0, outline=None, width=1):
        self.draw.rounded_rectangle(tuple(round(v * self.scale) for v in bounds),
                                    radius=round(radius * self.scale), fill=fill,
                                    outline=outline, width=round(width * self.scale))

    def line(self, points, fill, width=1):
        self.draw.line([(round(x * self.scale), round(y * self.scale)) for x, y in points],
                       fill=fill, width=max(1, round(width * self.scale)))

    def text(self, x, y, text, size=13, fill=INK, weight="regular", max_width=None, anchor="lt"):
        face = font(round(size * self.scale), weight)
        if max_width is not None:
            text = _fit(self.draw, text, face, max_width * self.scale)
        self.draw.text((round(x * self.scale), round(y * self.scale)), text,
                       font=face, fill=fill, anchor=anchor)


# 배경 · 테두리 · 글자. 옅은 배경과 진한 글자로 작은 회의명도 구분.
_SCHEDULE_PALETTE = {
    "예정": ("#eee8fa", "#8c6bc4", "#4f327e"),
    "사용중": ("#ffe4e7", "#dc4c64", "#92273e"),
    "체크인 대기": ("#fff0d1", "#d99a27", "#805313"),
    "노쇼 처리 대기": ("#fff0d1", "#d99a27", "#805313"),
    "곧 시작": ("#fff0d1", "#d99a27", "#805313"),
    "종료": ("#e8ebf1", "#b3bac8", "#4c5668"),
    "노쇼 취소": ("#e8ebf1", "#b3bac8", "#4c5668"),
}


def _schedule_bar(canvas: _ScheduleCanvas, axis: _Axis, row_y: float, reservation: dict) -> None:
    start, end = reservation["start_at"], reservation["end_at"]
    if end <= start or not axis.visible(start, end):
        return
    x0, x1 = axis.x(start), axis.x(end)
    width = x1 - x0
    fill, border, ink = _SCHEDULE_PALETTE.get(reservation["state"], _SCHEDULE_PALETTE["예정"])
    if width < 4:
        canvas.line([(x0, row_y + 7), (x0, row_y + 35)], border, 1)
        return
    canvas.box((x0 + 1, row_y + 5, x1 - 1, row_y + 37), fill, radius=5, outline=border, width=.65)
    available = width - 12
    if available < 28:
        return
    # 짧은 예약도 회의명을 남기고, 넓은 막대에는 시작·종료 시각을 함께 표시.
    if available < 62:
        canvas.text(x0 + 6, row_y + 10, reservation["title"], size=11, fill=ink,
                    weight="medium", max_width=available)
        canvas.text(x0 + 6, row_y + 25, start.astimezone(clock.TZ).strftime("%H:%M"),
                    size=9, fill=ink, max_width=available)
        return
    canvas.text(x0 + 6, row_y + 10, reservation["title"], size=11.5, fill=ink,
                weight="semibold", max_width=available)
    when = f"{start.astimezone(clock.TZ):%H:%M}–{end.astimezone(clock.TZ):%H:%M}"
    canvas.text(x0 + 6, row_y + 25, when, size=9, fill=ink, max_width=available)


def board_png(day: date, only_floor: int | None = None) -> bytes:
    """전체·층별 일정을 같은 시간축과 선명한 2배 해상도로 표시."""
    all_rooms = sorted((r for r in rooms.all_rooms() if only_floor is None or r["floor"] == only_floor),
                       key=lambda r: (r["floor"], r["id"]))
    schedules = rooms.room_schedules([r["id"] for r in all_rooms], day, include_attendees=False)
    floors = sorted({r["floor"] for r in all_rooms})
    width, pad, label_width = 1200, 28, 190
    top, floor_header, row_height, floor_gap, bottom = 148, 30, 42, 14, 42
    height = top + len(floors) * (floor_header + floor_gap) + len(all_rooms) * row_height + bottom
    if not all_rooms:
        height += 64
    canvas = _ScheduleCanvas(width, height)
    now = clock.now()
    title = "전체 회의실 일정" if only_floor is None else f"{only_floor}층 회의실 일정"
    canvas.text(pad, 26, title, size=23, weight="bold")
    total = sum(len(rs) for rs in schedules.values())
    canvas.text(pad, 59, f"{len(all_rooms)}개 회의실  ·  예약 {total}건  ·  09:00–18:00", size=12, fill=MUTED)
    day_label = f"{day:%Y.%m.%d}  {'월화수목금토일'[day.weekday()]}요일"
    canvas.text(width - pad, 29, day_label, size=15, weight="semibold", anchor="rt")
    if day == now.date():
        canvas.text(width - pad, 60, f"{now:%H:%M} 기준", size=12, fill=BRAND, anchor="rt")

    # 상태 이름을 모두 적어 색의 의미를 설명. 빈 시간은 흰 바탕으로 남김.
    x = pad
    for state in ("예정", "체크인 대기", "사용중", "종료"):
        fill, border, ink = _SCHEDULE_PALETTE[state]
        canvas.box((x, 94, x + 12, 106), fill, radius=3, outline=border)
        canvas.text(x + 19, 94, state, size=11, fill=MUTED)
        x += 116 if state == "체크인 대기" else 88
    canvas.box((x, 94, x + 12, 106), "#ffffff", radius=3, outline="#c7cdd8")
    canvas.text(x + 19, 94, "예약 없음", size=11, fill=MUTED)
    canvas.line([(pad, 124), (width - pad, 124)], "#dde1eb")

    axis = _Axis(day, pad + label_width, width - pad - 12)
    show_now = day == now.date() and axis.start <= now <= axis.end
    y = top
    for floor in floors:
        floor_rooms = [r for r in all_rooms if r["floor"] == floor]
        end_y = y + floor_header + len(floor_rooms) * row_height
        canvas.box((pad - 8, y - 5, width - pad + 8, end_y + 2), "#ffffff", radius=10, outline="#e2e6ee")
        canvas.box((pad - 7, y - 4, width - pad + 7, y + floor_header), "#f0edf7", radius=8)
        canvas.text(pad + 8, y + 5, f"{floor}층", size=13, fill=BRAND, weight="bold")
        canvas.text(pad + 49, y + 7, f"{len(floor_rooms)}개 회의실", size=10, fill=MUTED)
        for hour in range(OPEN_H, CLOSE_H + 1):
            xx = axis.x(axis.start + timedelta(hours=hour - OPEN_H))
            canvas.text(xx, y + 7, f"{hour:02d}:00", size=10, fill="#626b7c", anchor="mt")
        row_y = y + floor_header
        for index, room in enumerate(floor_rooms):
            if index % 2:
                canvas.box((pad - 6, row_y, width - pad + 6, row_y + row_height), "#fafbfe")
            canvas.text(pad + 10, row_y + 14, room["name"], size=13, weight="semibold", max_width=126)
            canvas.text(axis.x0 - 15, row_y + 15, f"{room['capacity']}인", size=10, fill=MUTED, anchor="rt")
            # 시간선은 행 바탕 위, 예약 막대 아래에 그려 가려지지 않게 유지.
            for half_hour in range((CLOSE_H - OPEN_H) * 2 + 1):
                xx = axis.x(axis.start + timedelta(minutes=30 * half_hour))
                color = "#e2e6ee" if half_hour % 2 == 0 else "#f0f2f7"
                canvas.line([(xx, row_y), (xx, row_y + row_height)], color, .65)
            if show_now:
                xx = axis.x(now)
                canvas.line([(xx, row_y), (xx, row_y + row_height)], "#d94158", 1.4)
            for reservation in schedules[room["id"]]:
                _schedule_bar(canvas, axis, row_y, reservation)
            canvas.line([(pad + 4, row_y + row_height), (width - pad - 4, row_y + row_height)], "#eef0f5", .6)
            row_y += row_height
        if show_now:
            xx = axis.x(now)
            canvas.box((xx - 2.5, y + floor_header - 2, xx + 2.5, y + floor_header + 3), "#d94158", radius=2)
        y = end_y + floor_gap
    if not all_rooms:
        canvas.text(pad, y + 18, "표시할 회의실이 없습니다.", size=14, fill=MUTED)
    canvas.text(pad, height - 24, "빈 구간은 예약 없음 · 회의실 메뉴에서 상세 일정 확인", size=10, fill=MUTED)
    if show_now:
        canvas.text(width - pad, height - 24, f"빨간 선: 현재 {now:%H:%M}", size=10, fill="#b92f44", anchor="rt")
    return _png(canvas.img)
