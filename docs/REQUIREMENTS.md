# 설치 요구사항

[README](../README.md) · [문서 목차](README.md)

이 저장소는 소스와 의존성 명세를 제공합니다. 미리 빌드한 앱·실행 파일은 설치에 필요하지 않습니다.

## 프로그램과 파일

| 항목 | 조건·역할 |
|---|---|
| Python | 3.12 이상 |
| Git | 소스 받기·업데이트 |
| uv 또는 pip | uv는 uv.lock, pip는 requirements.txt로 실행 의존성 설치 |
| 한글 폰트 | macOS Apple SD Gothic Neo, Windows 맑은 고딕 자동 검색. Linux는 fonts-noto-cjk 설치 또는 CHART_FONT_PATH 지정 |
| 로컬 저장소 | SQLite 파일을 쓸 수 있는 디스크. 별도 DB 서버는 불필요 |
| 네트워크 | 실행 PC에서 Slack HTTPS·WebSocket 연결 허용 |

## 의존성 명세

- [pyproject.toml](../pyproject.toml): 직접 의존성·개발 의존성·선택형 PostgreSQL 이관 의존성.
- [uv.lock](../uv.lock): 정확한 버전·플랫폼 조건·다운로드 해시.
- [requirements.txt](../requirements.txt): uv.lock에서 추출한 일반 실행용 pip 설치 목록. 테스트 패키지와 선택형 이관 패키지는 포함하지 않습니다.

```sh
# 기본 설치
uv sync --locked

# uv 대신 pip를 사용하는 경우
python -m venv .venv
# Linux/macOS
.venv/bin/python -m pip install -r requirements.txt
# Windows PowerShell
# .venv\Scripts\python.exe -m pip install -r requirements.txt
```

pip 설치 후에는 `uv run --locked python` 대신 `.venv/bin/python`을 사용합니다. 예: `.venv/bin/python -m backend.doctor`, `.venv/bin/python -m backend.main`. Windows는 `.venv\Scripts\python.exe`를 사용합니다.

의존성을 수정했다면 pyproject.toml → uv.lock → requirements.txt 순서로 갱신합니다.

```sh
uv lock
uv export --locked --no-dev --no-hashes --no-emit-project --format requirements-txt --output-file requirements.txt
```

## Slack과 환경변수

1. Slack 앱 생성·설치 권한과 [매니페스트](../slack_manifest.yaml)의 Bot Scopes·Bot Events·Interactivity 설정.
2. Socket Mode와 connections:write 앱 토큰, 설치한 앱의 봇 토큰.
3. 실제 워크스페이스·앱 ID, 직원 정보와 회의실 목록.
4. 루트 `.env`의 공통 토큰·시간대·DB 경로, 서로 다른 임의의 SECRET_KEY·QR_SECRET.
5. 선택 기능: 이미지 표시용 HTTPS 주소, 웹 QR용 HTTPS 주소·Slack OpenID 설정, 임시 이미지 터널용 cloudflared.

전체 환경변수는 [.env.example](../.env.example), 공통 규칙은 [Slack 설정](SLACK_SETUP.md), 생성·설치·권한과 직원 등록은 [상세 안내](USAGE.md)를 따릅니다.
