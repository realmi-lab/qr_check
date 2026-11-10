"""슬랙 Block Kit 조각과, 같은 내용을 터미널 글자로 바꾸는 함수."""

import json
import re
from html import escape
from dataclasses import dataclass, field


from frontend.style import icon  # noqa: E402,F401  (예전 코드 호환: blocks.icon)


@dataclass
class Reply:
    blocks: list[dict] = field(default_factory=list)
    fallback: str = ""   # 알림·접근성용 한 줄 요약
    open_finder: bool = False   # 슬랙에서 '빈 회의실 찾기' 창을 열라는 뜻
    color: str | None = None    # 알림 왼쪽 색 띠 (메시지에서만)

    def add(self, *bs: dict) -> "Reply":
        self.blocks.extend(b for b in bs if b)
        return self

    @property
    def text(self) -> str:
        """전체 내용 (터미널·테스트용 글자)."""
        return plain(self.blocks) or self.fallback

    @property
    def summary(self) -> str:
        """알림 미리보기용 한 줄."""
        return self.fallback or plain(self.blocks)[:300]

    def slack(self, limit: int = 50) -> dict:
        """메시지로 보낼 때. 색이 있으면 왼쪽 색 띠가 있는 첨부로 감쌈."""
        if self.color:
            return {"text": self.summary,
                    "attachments": [{"color": self.color, "fallback": self.summary, "blocks": self.blocks[:limit]}]}
        return {"text": self.summary, "blocks": self.blocks[:limit]}


# ---------------------------------------------------------------
# 블록
# ---------------------------------------------------------------

def header(text: str) -> dict:
    """작은 제목. Slack의 큰 header 대신 context 크기로 표시."""
    return context(f"*{escape(text[:150], quote=False)}*")


def section(md: str, accessory: dict | None = None, fields: list[str] | None = None) -> dict:
    b: dict = {"type": "section"}
    if md:
        b["text"] = {"type": "mrkdwn", "text": md[:3000]}
    if fields:
        b["fields"] = [{"type": "mrkdwn", "text": f[:2000]} for f in fields[:10]]
    if accessory:
        b["accessory"] = accessory
    return b


def context(*mds: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": m[:3000]} for m in mds if m][:10]}


def divider() -> dict:
    return {"type": "divider"}


def actions(*elements: dict, block_id: str | None = None) -> dict | None:
    els = [e for e in elements if e][:25]
    if not els:
        return None
    b = {"type": "actions", "elements": els}
    if block_id:
        b["block_id"] = block_id
    return b


def button(text: str, action_id: str, value: str = "", style: str | None = None, url: str | None = None) -> dict:
    b = {"type": "button", "text": {"type": "plain_text", "text": text[:75], "emoji": True},
         "action_id": action_id, "value": value[:2000] or action_id}
    if style:
        b["style"] = style
    if url:
        b["url"] = url
    return b


def image(source: str | dict | None, alt: str, title: str | None = None) -> dict | None:
    """source: 주소(str) 또는 {"image_url": …} / {"slack_file": {"id": …}}"""
    if not source:
        return None
    b = {"type": "image", **({"image_url": source} if isinstance(source, str) else source), "alt_text": alt[:2000]}
    if title:
        b["title"] = {"type": "plain_text", "text": title[:2000], "emoji": True}
    return b


# ----- 표 (Slack table block: 메시지·홈 탭에서만, 창(modal)에서는 안 됨) -----

def rt(*parts) -> dict:
    """서식 있는 칸. parts: "글자" 또는 ("글자", {"bold": True, "italic": True, "strike": True})"""
    els = []
    for p in parts:
        text, st = (p, None) if isinstance(p, str) else p
        if not text:
            continue
        el = {"type": "text", "text": text}
        if st:
            el["style"] = st
        els.append(el)
    if not els:
        els = [{"type": "text", "text": " "}]
    return {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": els}]}


B, I, S = {"bold": True}, {"italic": True}, {"strike": True}


def table(header_cells: list[str], rows: list[list[dict]], wrap: list[bool] | None = None,
          align: list[str] | None = None) -> dict:
    settings_ = []
    for i in range(len(header_cells)):
        # 문서는 null을 허용한다고 하지만 실제 API는 객체만 받음 → 항상 객체로
        settings_.append({"align": (align[i] if align else "left"), "is_wrapped": bool(wrap and wrap[i])})
    head = [rt((h, B)) for h in header_cells]
    return {"type": "table", "column_settings": settings_, "rows": [head] + rows[:99]}


TABLE_MAX_ROWS = 8   # 슬랙은 9줄부터 표를 접고 '전체 표 열기' 버튼을 띄움 → 항상 8줄 이하로 나눔


def tables(header_cells: list[str], groups: list[tuple[str, list[list[dict]]]], wrap=None, align=None) -> list[dict]:
    """모바일에서 잘리지 않는 작은 글씨의 한 열 목록."""
    out = []
    shown = 0
    total = sum(len(rows) for _, rows in groups)
    for title, rows in groups:
        if not rows:
            continue
        if title:
            out.append(context(f"*{title}*"))
        for row in rows:
            if shown >= 32:
                out.append(context(f"외 {total-shown}건 · 날짜나 회의실을 선택해 범위를 좁혀 주세요."))
                return out
            cells = [_cell_text(cell) for cell in row]
            lines = [escape(value, quote=False) for value in cells if value.strip()]
            out.append(context("\n".join(lines)))
            shown += 1
    return out


def _cell_text(c: dict) -> str:
    if c.get("type") == "raw_text":
        return c.get("text", "")
    return "".join(e.get("text", "") for sec in c.get("elements", []) for e in sec.get("elements", []))


def option(text: str, value: str) -> dict:
    return {"text": {"type": "plain_text", "text": text[:75], "emoji": True}, "value": value[:150]}


def grouped_select(placeholder: str, action_id: str, groups: list[tuple[str, list[tuple[str, str]]]]) -> dict:
    """그룹(예: 층별)으로 묶인 메뉴."""
    return {"type": "static_select", "action_id": action_id,
            "placeholder": {"type": "plain_text", "text": placeholder[:150], "emoji": True},
            "option_groups": [{"label": {"type": "plain_text", "text": g[:75], "emoji": True},
                               "options": [option(t, v) for t, v in opts][:100]} for g, opts in groups][:100]}


def select(placeholder: str, action_id: str, options: list[tuple[str, str]], initial: str | None = None) -> dict:
    opts = [option(t, v) for t, v in options][:100]
    b = {"type": "static_select", "action_id": action_id,
         "placeholder": {"type": "plain_text", "text": placeholder[:150], "emoji": True}, "options": opts}
    if initial is not None:
        found = [o for o in opts if o["value"] == initial]
        if found:
            b["initial_option"] = found[0]
    return b


# ---------------------------------------------------------------
# 터미널용 글자
# ---------------------------------------------------------------

def plain(blocks: list[dict]) -> str:
    out: list[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "header":
            out.append(f"\n■ {b['text']['text']}")
        elif t == "section":
            if "text" in b:
                out.append(b["text"]["text"])
            for f in b.get("fields", []):
                out.append("  " + f["text"].replace("\n", " "))
        elif t == "context":
            out.append("  " + "  ".join(e.get("text", "") for e in b["elements"]))
        elif t == "divider":
            out.append("─" * 30)
        elif t == "table":
            for row in b["rows"]:
                out.append("  " + " | ".join(_cell_text(c) for c in row))
        elif t == "image":
            out.append(f"  [그림: {b.get('alt_text', '')}]")
        elif t == "actions":
            out.append("  [" + "] [".join(e["text"]["text"] if "text" in e else e.get("placeholder", {}).get("text", "")
                                       for e in b["elements"]) + "]")
    text = "\n".join(out).strip()
    return re.sub(r"[*`_]", "", text)


def dumps(reply: Reply) -> str:
    return json.dumps(reply.blocks, ensure_ascii=False, indent=1)
