"""이미지 만들기: 슬랙 앱 아이콘, 상태 커스텀 이모지, 파비콘, QR 가운데 로고.

  .venv/bin/python -m roombot.assets      →  assets/ 폴더에 PNG 생성
크게(4배) 그린 뒤 줄여서 가장자리를 부드럽게 함.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets"
S = 4  # 크게 그리는 배율

PURPLE_TOP, PURPLE_BOTTOM = (138, 63, 160), (59, 13, 74)
GREEN = (46, 182, 125)
WHITE = (255, 255, 255)


def _gradient(size: int, top, bottom) -> Image.Image:
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / (size - 1)
        # 대각선 느낌: 위→아래 + 약간 왼쪽→오른쪽
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=c)
    return img


def _rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def _logo(px: int, rounded: bool = True) -> Image.Image:
    """문 + 초록 체크 배지 로고. 64 단위 좌표를 px 크기로."""
    n = px * S
    u = n / 64
    bg = _gradient(n, PURPLE_TOP, PURPLE_BOTTOM).convert("RGBA")
    if rounded:
        bg.putalpha(_rounded_mask(n, int(15 * u)))
    d = ImageDraw.Draw(bg)
    w = int(4.2 * u)
    # 문틀
    d.rounded_rectangle([22 * u, 14 * u, 42 * u, 60 * u], radius=int(3.5 * u), outline=WHITE, width=w)
    bg_cut = _gradient(n, PURPLE_TOP, PURPLE_BOTTOM).crop((0, int(49 * u + w / 2), n, n))
    bg.paste(bg_cut, (0, int(49 * u + w / 2)), _rounded_mask(n, int(15 * u)).crop((0, int(49 * u + w / 2), n, n)))
    d.line([15 * u, 49 * u, 35 * u, 49 * u], fill=WHITE, width=w)
    d.ellipse([15 * u - w / 2, 49 * u - w / 2, 15 * u + w / 2, 49 * u + w / 2], fill=WHITE)
    d.ellipse([35 * u - w / 2, 49 * u - w / 2, 35 * u + w / 2, 49 * u + w / 2], fill=WHITE)
    # 손잡이
    d.ellipse([30.6 * u, 28.6 * u, 35.4 * u, 33.4 * u], fill=WHITE)
    # 체크 배지
    d.ellipse([31.5 * u, 31.5 * u, 58.5 * u, 58.5 * u], fill=PURPLE_BOTTOM)
    d.ellipse([34 * u, 34 * u, 56 * u, 56 * u], fill=GREEN)
    cw = int(3.4 * u)
    pts = [(40 * u, 45.2 * u), (43.4 * u, 48.6 * u), (49.8 * u, 41.6 * u)]
    d.line(pts, fill=WHITE, width=cw, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - cw / 2, y - cw / 2, x + cw / 2, y + cw / 2], fill=WHITE)
    return bg.resize((px, px), Image.LANCZOS)


# ---------------------------------------------------------------
# 상태 이모지 (슬랙 커스텀 이모지용, 128px)
# ---------------------------------------------------------------

EMOJI = {
    "rb-busy":      ((229, 72, 77), "door"),
    "rb-wait":      ((245, 165, 36), "clock"),
    "rb-late":      ((247, 107, 21), "bang"),
    "rb-soon":      ((62, 99, 221), "arrow"),
    "rb-free":      ((48, 164, 108), "check"),
    "rb-scheduled": ((139, 141, 152), "cal"),
    "rb-done":      ((176, 178, 186), "check"),
    "rb-noshow":    ((205, 43, 49), "x"),
}


def _emoji(color, glyph: str, px: int = 128) -> Image.Image:
    n = px * S
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([n * .04, n * .04, n * .96, n * .96], fill=color)
    w = int(n * .085)
    c = n / 2

    def dot(x, y, r=None):
        r = r or w / 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)

    def stroke(*pts):
        d.line(pts, fill=WHITE, width=w, joint="curve")
        dot(*pts[0])
        dot(*pts[-1])

    if glyph == "check":
        stroke((n * .3, n * .52), (n * .44, n * .66), (n * .71, n * .37))
    elif glyph == "x":
        stroke((n * .34, n * .34), (n * .66, n * .66))
        stroke((n * .66, n * .34), (n * .34, n * .66))
    elif glyph == "clock":
        d.ellipse([n * .23, n * .23, n * .77, n * .77], outline=WHITE, width=int(w * .75))
        stroke((c, n * .35), (c, c), (n * .62, n * .6))
    elif glyph == "bang":
        stroke((c, n * .28), (c, n * .56))
        dot(c, n * .71, w * .62)
    elif glyph == "arrow":
        stroke((n * .3, c), (n * .68, c))
        stroke((n * .53, n * .35), (n * .68, c), (n * .53, n * .65))
    elif glyph == "door":
        d.rounded_rectangle([n * .34, n * .24, n * .66, n * .78], radius=int(n * .05), outline=WHITE, width=w)
        dot(n * .57, n * .52, w * .55)
    elif glyph == "cal":
        d.rounded_rectangle([n * .27, n * .3, n * .73, n * .73], radius=int(n * .06), outline=WHITE, width=w)
        d.line([n * .27, n * .44, n * .73, n * .44], fill=WHITE, width=w)
        stroke((n * .39, n * .24), (n * .39, n * .32))
        stroke((n * .61, n * .24), (n * .61, n * .32))
    return img.resize((px, px), Image.LANCZOS)


def generate() -> list[Path]:
    OUT.mkdir(exist_ok=True)
    (OUT / "emoji").mkdir(exist_ok=True)
    made = []
    for name, px, rounded in (("app-icon.png", 1024, False), ("favicon.png", 64, True), ("qr-logo.png", 256, True)):
        _logo(px, rounded).save(OUT / name)
        made.append(OUT / name)
    for name, (color, glyph) in EMOJI.items():
        p = OUT / "emoji" / f"{name}.png"
        _emoji(color, glyph).save(p)
        made.append(p)
    # 한눈에 보는 미리보기
    sheet = Image.new("RGBA", (128 * len(EMOJI) + 16 * (len(EMOJI) + 1), 160), (255, 255, 255, 255))
    for i, name in enumerate(EMOJI):
        sheet.alpha_composite(Image.open(OUT / "emoji" / f"{name}.png"), (16 + i * 144, 16))
    sheet.save(OUT / "emoji-preview.png")
    made.append(OUT / "emoji-preview.png")
    return made


if __name__ == "__main__":
    for p in generate():
        print(p.relative_to(OUT.parent))
