"""서명된 일정 PNG만 제공하는 전용 서버와 선택적 임시 HTTPS 터널."""

import atexit
import logging
from pathlib import Path
import re
import shutil
import subprocess
import threading

from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
import uvicorn

from roombot import imgurl
from roombot.config import settings
from roombot.web import chart_image

log = logging.getLogger(__name__)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_api_route('/img/{kind}.png', chart_image, methods=['GET', 'HEAD'])


@app.get('/healthz')
def healthz():
    return {'ok': True}


@app.get('/view/{kind}')
def view_image(kind: str, request: Request):
    params = dict(request.query_params)
    if kind not in ('room', 'board') or not imgurl.verify(kind, params):
        return Response(status_code=404)
    source = escape(f"/img/{kind}.png?{urlencode(params)}", quote=True)
    title = '전체 회의실 일정' if kind == 'board' else '회의실 일정'
    document = f"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{title}</title>
<style>*{{box-sizing:border-box}}body{{margin:0;font:12px/1.45 -apple-system,BlinkMacSystemFont,sans-serif;background:#f3f5f8;color:#253047}}
nav{{position:sticky;top:0;display:flex;align-items:center;gap:10px;justify-content:space-between;padding:10px 12px;background:#fff;border-bottom:1px solid #dfe5ec}}
h1{{font-size:13px;margin:0}}button{{font:inherit;border:1px solid #c8d2df;border-radius:7px;padding:6px 10px;background:white;color:#253047}}
main{{overflow:auto;padding:6px}}img{{display:block;width:100%;height:auto;background:white}}body.zoom img{{width:1200px;max-width:none}}
small{{display:block;padding:4px 12px;color:#697789}}</style></head><body>
<nav><h1>{title}</h1><button onclick="document.body.classList.toggle('zoom');this.textContent=document.body.classList.contains('zoom')?'화면에 맞추기':'확대'">확대</button></nav>
<small>화면에 맞춰 표시합니다. 확대 후 좌우로 움직이거나 두 손가락으로 확대하세요.</small>
<main><img src="{source}" alt="{title}" loading="eager"></main></body></html>"""
    return HTMLResponse(document, headers={
        'Cache-Control':'private, no-store', 'Referrer-Policy':'no-referrer',
        'X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
    })


_stop = threading.Event()
_child: subprocess.Popen | None = None
_server: uvicorn.Server | None = None
_URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')


def _publish(base: str) -> None:
    settings.image_base_url = base
    path = Path(__file__).resolve().parent.parent / 'logs' / 'image-url.txt'
    path.parent.mkdir(exist_ok=True, mode=0o700)
    path.write_text(base + '\n')
    path.chmod(0o600)
    log.info('이미지 전용 HTTPS 연결됨')


def _tunnel(binary: str) -> None:
    global _child
    while not _stop.is_set():
        settings.image_base_url = ''
        try:
            _child = subprocess.Popen(
                [binary, 'tunnel', '--no-autoupdate', '--protocol', 'http2',
                 '--url', f'http://127.0.0.1:{settings.image_port}'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
            candidate = None
            published = False
            for line in _child.stdout:
                if _stop.is_set():
                    break
                match = _URL.search(line)
                if match:
                    candidate = match.group()
                if candidate and not published and 'Registered tunnel connection' in line:
                    _publish(candidate)
                    published = True
            if _child.poll() is None:
                _child.terminate()
                _child.wait(timeout=5)
        except Exception:
            log.warning('이미지 터널 연결 실패, 다시 연결 예정')
            if _child and _child.poll() is None:
                _child.kill()
        finally:
            settings.image_base_url = ''
        if not _stop.is_set():
            log.warning('이미지 터널 종료, 10초 후 다시 연결')
            _stop.wait(10)


def start() -> None:
    global _server
    if not (settings.image_base_url or settings.image_tunnel):
        return
    _stop.clear()
    _server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=settings.image_port,
                                          log_level='warning', access_log=False))
    threading.Thread(target=_server.run, name='image-server', daemon=True).start()
    if settings.image_tunnel:
        binary = shutil.which(settings.cloudflared_bin)
        if not binary:
            raise RuntimeError('IMAGE_TUNNEL에 필요한 cloudflared를 찾지 못했습니다.')
        threading.Thread(target=_tunnel, args=(binary,), name='image-tunnel', daemon=True).start()
    atexit.register(stop)


def stop() -> None:
    _stop.set()
    if _child and _child.poll() is None:
        _child.terminate()
        try:
            _child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _child.kill()
    if _server:
        _server.should_exit = True
