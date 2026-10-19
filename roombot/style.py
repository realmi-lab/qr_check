"""슬랙 화면 표기 규칙 — 아이콘과 상태 이름은 여기서만 정함.

상태 (화면에는 4가지만)
  🔴 사용중   🟡 체크인 대기   ⚪ 종료   🟢 예약없음
  (곧 시작·노쇼 처리 중 → 체크인 대기, 노쇼 취소 → 종료(회의명 취소선), 예정 → 표시 없음)
물건·기능
  🏢 현황/층   🚪 회의실   👥 인원   🖥️ 장비   📅 날짜   👤 예약자·참석자
  📱 QR 체크인  ⏰ 마감   🔍 찾기   📋 내 예약   📊 통계   🔄 새로고침
  ✅ 완료   ⛔ 불가   🔔 알림   ☀️ 브리핑   💡 도움말   ⚠️ 주의
시간은 굵게(*09:00–10:00*) 씀. `코드` 모양은 슬랙에서 빨간 글자로 보여서 명령어에만 씀.
"""

from roombot.config import settings

# 상태 → (기본 아이콘, 커스텀 이모지, 보이는 이름)
# 화면에 보이는 상태는 4가지뿐: 사용중 · 체크인 대기 · 종료 · 예약없음 (예정 회의는 상태 표시 없음)
STATES = {
    "사용중":       ("🔴", ":rb-busy:",  "사용중"),
    "체크인 대기":   ("🟡", ":rb-wait:",  "체크인 대기"),
    "노쇼 처리 대기": ("🟡", ":rb-wait:",  "체크인 대기"),
    "곧 시작":      ("🟡", ":rb-wait:",  "체크인 대기"),
    "비어있음":     ("🟢", ":rb-free:",  "예약없음"),
    "예정":         ("",   "",           ""),
    "종료":         ("⚪", ":rb-done:",  "종료"),
    "노쇼 취소":    ("⚪", ":rb-done:",  "종료"),
}
SHOWN = ("사용중", "체크인 대기", "종료", "비어있음")   # 범례에 나오는 4가지

ROOM, PEOPLE, DATE, PERSON = "🚪", "👥", "📅", "👤"
QR, DEADLINE, FIND, MY, STATS, REFRESH, FLOOR = "📱", "⏰", "🔍", "📋", "📊", "🔄", "🏢"
OK, NO, BELL, BRIEF, HELP, WARN = "✅", "⛔", "🔔", "☀️", "💡", "⚠️"


def icon(state: str) -> str:
    base, custom, _ = STATES.get(state, ("•", "•", state))
    return custom if settings.icon_set == "custom" else base


def label(state: str) -> str:
    return STATES.get(state, ("", "", state))[2]


def badge(state: str) -> str:
    """아이콘 + 이름 (예: 🟡 체크인 대기). 예정은 빈 글자."""
    return f"{icon(state)} {label(state)}".strip()


def legend(*_ignored: str) -> str:
    return "   ".join(badge(s) for s in SHOWN)
