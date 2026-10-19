"""회의실별 QR 이미지와 인쇄용 페이지 만들기.

QR 안에는 체크인 주소가 들어감:  {PUBLIC_BASE_URL}/c/{회의실 코드}?t={토큰}
토큰은 QR_SECRET으로 계산하므로 DB를 다시 만들어도 같음. 주소(PUBLIC_BASE_URL)가 바뀌면 QR도 바뀌므로
인쇄는 고정 주소를 정한 뒤에 한 번만 하면 됨.
"""

import html
from pathlib import Path
from urllib.parse import quote

import qrcode
from PIL import Image
from qrcode.constants import ERROR_CORRECT_H

from roombot import icons
from roombot.checkin import qr_tokens
from roombot.config import settings

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT, FONT_BOLD = "/System/Library/Fonts/AppleSDGothicNeo.ttc", 6


def checkin_url(code: str, token: str) -> str:
    return f"{settings.public_base_url}/c/{quote(code)}?t={quote(token)}"


def room_icon(code: str) -> Path | None:
    """회의실별로 따로 정한 가운데 그림 (assets/qr-icons/custom/{코드}.png). 없으면 None → 방 코드 글자."""
    p = ASSETS / "qr-icons" / "custom" / f"{code}.png"
    return p if p.exists() else None


def text_logo(code: str) -> Image.Image:
    """가운데에 넣을 방 코드 배지: 둥근 테두리 안에 굵은 글자."""
    from PIL import ImageDraw, ImageFont
    W, line = 1024, 44
    img = Image.new("RGBA", (W, W), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((line // 2, line // 2, W - line // 2, W - line // 2), radius=180, outline="#1b0b22", width=line)
    size = 520
    while size > 80:
        font = ImageFont.truetype(FONT, size, index=FONT_BOLD)
        l, t, r, b = d.textbbox((0, 0), code, font=font)
        if r - l <= W * 0.72:
            break
        size -= 10
    d.text(((W - (r - l)) / 2 - l, (W - (b - t)) / 2 - t), code, font=font, fill="#1b0b22")
    return img


def _fit_square(img: Image.Image) -> Image.Image:
    """그림 둘레의 흰 여백을 잘라 정사각형으로 (가운데 칸을 그림이 꽉 채우게)."""
    from PIL import ImageChops, ImageOps
    rgb = Image.new("RGB", img.size, "white")
    rgb.paste(img, mask=img.getchannel("A"))
    box = ImageChops.difference(rgb, Image.new("RGB", img.size, "white")).convert("L").point(lambda v: 255 if v > 24 else 0).getbbox()
    if not box:
        return img
    crop = rgb.crop(box)
    side = int(max(crop.size) * 1.06)
    return ImageOps.pad(crop, (side, side), color="white").convert("RGBA")


def qr_image(url: str, logo_path: Path | Image.Image | None = None) -> Image.Image:
    """가운데 그림이 들어간 QR (오류 정정 H라 가운데를 가려도 읽힘)."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=16, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1b0b22", back_color="white").convert("RGBA")
    if isinstance(logo_path, Image.Image):
        logo_src = logo_path
    else:
        logo_path = logo_path or ASSETS / "qr-logo.png"
        logo_src = Image.open(logo_path) if logo_path.exists() else None
    if logo_src is not None:
        side = int(img.size[0] * 0.27)
        logo = _fit_square(logo_src.convert("RGBA")).resize((side, side), Image.LANCZOS)
        pad = side // 14
        plate = Image.new("RGBA", (side + pad * 2, side + pad * 2), "white")
        plate.alpha_composite(logo, (pad, pad))
        pos = ((img.size[0] - plate.size[0]) // 2, (img.size[1] - plate.size[1]) // 2)
        img.alpha_composite(plate, pos)
    return img.convert("RGB")


def generate(out_dir: str = "qr") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cards = []
    from roombot import room_channels
    channels = room_channels.mapping() if settings.room_channels else {}
    for r in qr_tokens():
        ch = channels.get(r["id"])
        # 회의실 채널이 있으면 슬랙 채널 링크 (영구, 안 바뀜), 없으면 웹 체크인 주소
        url = room_channels.qr_link(ch["channel_id"]) if ch else checkin_url(r["code"], r["token"])
        qr_image(url, room_icon(r["code"]) or text_logo(r["code"])).save(out / f"{r['code']}.png")
        cards.append(f"""<section class="card">
  <div class="brand">{icons.LOGO}<span>회의실봇 QR 체크인</span></div>
  <h2>{html.escape(r['name'])}</h2>
  <div class="meta">{r['floor']}층 · {r['capacity']}인</div>
  <img src="{html.escape(r['code'])}.png" alt="{html.escape(r['label'])} 체크인 QR">
  <p class="how">{'휴대폰 카메라로 찍으면 <b>슬랙</b>이 열려요 → <b>[채널 참여]</b> 누르면 체크인' if ch else '휴대폰 카메라로 찍고 <b>슬랙으로 로그인</b>하면 체크인돼요'}</p>
  <ul>
    <li>회의 시작 <b>10분 전</b>부터 · 참석자 중 <b>누구나</b></li>
    <li>시작 후 <b>10분</b> 안에 체크인이 없으면 <b>예약 자동 취소</b></li>
  </ul>
  <div class="code">{html.escape(r['code'])}</div>
</section>""")
    warn = "" if channels or (settings.public_base_url.startswith("https://") and "trycloudflare.com" not in settings.public_base_url) else (
        f'<p class="warn no-print">⚠️ 지금 주소({html.escape(settings.public_base_url)})는 바뀔 수 있는 임시 주소예요. '
        '고정 주소를 정한 뒤 다시 만들어서 인쇄하세요.</p>')
    (out / "index.html").write_text(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>회의실 QR 인쇄</title>
<style>
  @page {{ size: A4; margin: 10mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Apple SD Gothic Neo", sans-serif; margin: 16px; color: #1b0b22; background: #fff; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
  .card {{ border: 2px solid #3B0D4A; border-radius: 18px; padding: 18px 18px 14px; text-align: center; break-inside: avoid;
           page-break-inside: avoid; position: relative; }}
  .brand {{ display: flex; align-items: center; justify-content: center; gap: 8px; font-size: 13px; font-weight: 700; color: #5b2a86; }}
  .brand svg {{ width: 22px; height: 22px; }}
  h2 {{ font-size: 40px; letter-spacing: -.5px; margin: 10px 0 2px; }}
  .meta {{ color: #5f5868; font-size: 14px; }}
  img {{ width: 72%; max-width: 230px; margin: 12px auto 6px; display: block; }}
  .how {{ font-size: 15px; margin: 6px 0; }}
  ul {{ list-style: none; padding: 0; margin: 6px 0 0; font-size: 12.5px; color: #5f5868; line-height: 1.6; }}
  .code {{ position: absolute; right: 14px; top: 12px; font-size: 11px; color: #9a93a3; }}
  .warn {{ background: #fff1dc; color: #7a4500; padding: 10px 12px; border-radius: 10px; }}
  .top {{ margin-bottom: 12px; color: #5f5868; font-size: 14px; }}
  @media print {{ body {{ margin: 0; }} .no-print {{ display: none; }} .grid {{ grid-template-columns: 1fr 1fr; gap: 8mm; }} }}
</style></head><body>
<p class="top no-print">인쇄해서 각 회의실 입구에 붙이세요. 이 페이지에는 체크인 토큰이 들어 있으니 외부에 공유하지 마세요.</p>
{warn}
<div class="grid">
{chr(10).join(cards)}
</div></body></html>""",
        encoding="utf-8",
    )
    return out / "index.html"
