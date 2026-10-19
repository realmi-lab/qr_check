"""체크인 웹 화면용 SVG 아이콘과 로고 (선 굵기·모서리를 맞춘 한 세트)."""

LOGO = """<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
<defs><linearGradient id="rbg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#8A3FA0"/><stop offset="1" stop-color="#3B0D4A"/></linearGradient></defs>
<rect width="64" height="64" rx="15" fill="url(#rbg)"/>
<path d="M22 49V17.5a3.5 3.5 0 0 1 3.5-3.5h13a3.5 3.5 0 0 1 3.5 3.5V36" fill="none" stroke="#fff" stroke-width="4.2" stroke-linecap="round"/>
<path d="M15 49h20" stroke="#fff" stroke-width="4.2" stroke-linecap="round"/>
<circle cx="33" cy="31" r="2.4" fill="#fff"/>
<circle cx="45" cy="45" r="11" fill="#2EB67D" stroke="#3B0D4A" stroke-width="3"/>
<path d="m40 45.2 3.4 3.4 6.4-7" fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

_PATHS = {
    "ok":       '<circle cx="12" cy="12" r="9.5"/><path d="m7.8 12.4 2.9 2.9 5.5-6.1"/>',
    "info":     '<circle cx="12" cy="12" r="9.5"/><path d="M12 11v5.5"/><path d="M12 7.6h.01"/>',
    "wait":     '<circle cx="12" cy="12" r="9.5"/><path d="M12 6.8V12l3.4 2.2"/>',
    "denied":   '<circle cx="9.5" cy="7.5" r="3.8"/><path d="M2.8 20.2v-1a5.2 5.2 0 0 1 5.2-5.2h3"/>'
                '<path d="m15.6 14.6 5.4 5.4"/><path d="m21 14.6-5.4 5.4"/>',
    "late":     '<circle cx="12" cy="12" r="9.5"/><path d="m15.2 8.8-6.4 6.4"/><path d="m8.8 8.8 6.4 6.4"/>',
    "free":     '<path d="M6 20.5V5a1.5 1.5 0 0 1 1.5-1.5h9A1.5 1.5 0 0 1 18 5v15.5"/><path d="M3.5 20.5h17"/>'
                '<path d="M14.6 12.2h.01"/>',
    "error":    '<path d="M10.3 4.2 2.6 17.6a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z"/>'
                '<path d="M12 9.5v4"/><path d="M12 17h.01"/>',
    "network":  '<path d="M2.5 8.8a14 14 0 0 1 19 0"/><path d="M5.6 12.3a9.5 9.5 0 0 1 12.8 0"/>'
                '<path d="M8.8 15.8a5 5 0 0 1 6.4 0"/><path d="M12 19.6h.01"/>',
    "login":    '<circle cx="7.6" cy="15.4" r="4.6"/><path d="m11 12 9.5-9.5"/><path d="m16.5 6.5 2.8 2.8"/>'
                '<path d="m14 9 2 2"/>',
    "logout":   '<path d="M9.5 20.5H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2h3.5"/><path d="m15.5 16.5 4.5-4.5-4.5-4.5"/>'
                '<path d="M20 12H9.5"/>',
    "building": '<rect x="4.5" y="2.5" width="15" height="19" rx="2"/><path d="M9.5 21.5v-3.5h5v3.5"/>'
                '<path d="M8.5 6.5h.01M12 6.5h.01M15.5 6.5h.01M8.5 10h.01M12 10h.01M15.5 10h.01M8.5 13.5h.01M12 13.5h.01M15.5 13.5h.01"/>',
}

# (아이콘 색, 배경 색)  — 밝은/어두운 화면 모두 대비가 충분한 색
COLORS = {
    "ok": ("#1a7f4b", "#e3f5ec"), "info": ("#2f5bd3", "#e6edfc"), "wait": ("#a35c00", "#fff1dc"),
    "denied": ("#b42318", "#fde8e6"), "late": ("#b42318", "#fde8e6"), "free": ("#1a7f4b", "#e3f5ec"),
    "error": ("#a35c00", "#fff1dc"), "network": ("#2f5bd3", "#e6edfc"), "login": ("#5b2a86", "#f1e8f8"),
    "logout": ("#5b2a86", "#f1e8f8"), "building": ("#5b2a86", "#f1e8f8"),
}


def icon_svg(kind: str, size: int = 34) -> str:
    color = COLORS.get(kind, COLORS["info"])[0]
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{_PATHS.get(kind, _PATHS["info"])}</svg>')


def badge(kind: str) -> str:
    bg = COLORS.get(kind, COLORS["info"])[1]
    return f'<div class="badge" style="background:{bg}">{icon_svg(kind)}</div>'
