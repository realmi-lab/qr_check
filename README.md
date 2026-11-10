# Slack QR Check · 회의실 예약·QR 체크인

SQLite로 회의실·직원·예약·참석자·체크인·취소 이력을 저장하는 Slack 앱입니다. 다우오피스 연동은 사용하지 않습니다. 일반 실행에는 PostgreSQL이나 별도 DB 서버가 필요하지 않습니다.

- Slack에서 회의실 현황, 일정, 빈 회의실, 내 예약과 노쇼 통계를 조회합니다.
- 홈과 일정 화면의 탐색은 작은 `⋯` 메뉴로 모았습니다. 메뉴에서 `미팅룸 · 전체 일정 · 내 예약`을 고릅니다. 미팅룸에서 날짜·시작·사용 시간을 고르면 예약 가능한 방과 `예약` 버튼을 바로 보여 줍니다. 인원 입력과 별도 찾기 제출은 없습니다. 예약·체크인은 바로 누르는 버튼을 유지합니다.
- 예약 등록·취소·참석자 추가도 앱에서 처리합니다.
- 회의실 QR → Slack 회의실 채널 → 체크인 버튼으로 출석을 기록합니다. 웹 QR 체크인도 지원합니다.
- 시작 10분 전부터 체크인할 수 있고, 시작 후 10분까지 참석자 누구도 체크인하지 않으면 자동 취소합니다. 이미 체크인한 회의에는 늦게 온 참석자도 합류할 수 있습니다.
- 전체 일정은 고해상도 이미지 아래에 층별 회의실 목록을 함께 표시합니다. 방마다 이름·인원·상태를 짧게 표시하고 현재 또는 다음 예약이 있으면 덧붙입니다. 개별 일정은 이미지와 날짜 선택창 하나로 표시하며 외부 `크게 보기` 버튼은 없습니다. 내 예약은 시간·회의실·제목·예약번호 위주로 표시합니다. Slack 기본 글꼴과 버튼 높이는 Slack이 정하므로 앱은 짧은 버튼 이름과 적은 메뉴를 사용합니다.

## 설치와 실행

### 새 설치에 필요한 것

- Python 3.12 이상, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/). macOS·Linux·Windows에서 터미널 실행 가능하며 자동 시작 스크립트만 macOS 전용입니다.
- Slack 워크스페이스의 앱 생성·설치 권한과 Socket Mode의 외부 HTTPS/WebSocket 연결 허용.
- 봇 토큰(`xoxb-`), `connections:write` 앱 토큰(`xapp-`), 워크스페이스 ID(`T...`), 앱 ID(`A...`).
- 실제 직원 목록(사번·이름·이메일)과 회의실 목록. 이메일은 Slack 프로필과 전체 주소가 일치해야 합니다.
- 쓰기 가능한 로컬 SQLite 저장 경로, 계속 켜 둘 실행 PC/서버. DB 서버·LLM·유료 AI 키는 필요하지 않습니다.
- 일정 이미지용 한글 폰트. macOS는 Apple SD Gothic Neo, Windows는 맑은 고딕을 자동 검색합니다. Ubuntu는 `sudo apt-get install fonts-noto-cjk` 또는 `CHART_FONT_PATH`로 폰트 파일을 지정하세요.

```sh
git clone https://github.com/realmi-lab/slack_qr_check.git
cd slack_qr_check
uv sync --locked
cp .env.example .env
# Windows PowerShell에서는 Copy-Item .env.example .env
# 아래 Slack 앱을 설치하고 .env 설정을 채웁니다.
uv run --locked python -m backend.doctor
uv run --locked python -m backend.cli db init
# 아래 employee-add / room-add로 실제 직원·회의실을 등록합니다.
uv run --locked python -m backend.main
```

`doctor`는 로컬 설정 형식만 검사하며 Slack을 호출하거나 DB를 수정하지 않습니다. 오류가 있으면 종료 코드 1을 반환합니다. 비밀값은 `python -c "import secrets; print(secrets.token_urlsafe(48))"`를 두 번 실행해 각각 생성하고 `.env`의 `SECRET_KEY`, `QR_SECRET`에 넣으세요. `.env`가 읽히도록 저장소 폴더에서 명령을 실행합니다. Linux/macOS에서는 `chmod 600 .env`로 접근을 제한하세요.

### .env 설정표

| 항목 | 필수 여부 / 용도 |
| --- | --- |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack 실행 필수. 설치한 앱의 봇 토큰과 connections:write 앱 토큰 |
| `SLACK_TEAM_ID`, `SLACK_APP_ID` | 필수. 다른 워크스페이스의 ID를 섞지 않음 |
| `SECRET_KEY`, `QR_SECRET` | 필수. 각각 다른 32자 이상 임의 값. 기존 설치에서는 유지 |
| `SQLITE_PATH` | 기본 `./data/roombot.sqlite3`. 운영에는 절대 경로 권장 |
| `APP_TIMEZONE` | 기본 `Asia/Seoul` |
| `ROOM_CHANNELS` | 기본 `true`: Slack 회의실 채널 QR. 공개 웹 주소 없이 사용 가능 |
| `LEAVE_AFTER_CHECKIN` | 기본 `true`: 체크인 후 채널에서 퇴장. 워크스페이스 정책에 맞춰 조정 |
| `PUBLIC_BASE_URL` | 웹 QR 모드에서 휴대폰이 접속 가능한 HTTPS 주소 |
| `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | 웹 QR의 Sign in with Slack 사용 시 필수 |
| `IMAGE_BASE_URL`, `IMAGE_PORT` | 선택. 일정 PNG용 HTTPS 주소와 로컬 포트(기본 8001) |
| `IMAGE_TUNNEL`, `CLOUDFLARED_BIN` | 선택. 설치된 cloudflared로 임시 이미지 주소 제공 |
| `WEB_PORT` | 기본 8000. 웹 QR·healthz 서버 |
| `ADMIN_SLACK_USER` | 선택. 관리자 Slack ID(U...) |
| `SLACK_NOTIFY_CHANNEL` | 선택. 노쇼로 빈 방이 생겼을 때 알림 채널 |
| `MORNING_BRIEF_AT` | 기본 08:30. 빈 값은 아침 브리핑 끔 |
| `CHECKIN_ALLOWED_CIDRS`, `TRUST_PROXY_HEADERS` | 선택. 웹 체크인 IP 정책·신뢰하는 프록시에만 사용 |
| `CHART_FONT_PATH` | 자동 검색되지 않는 환경에서 한글 .ttf/.ttc 절대 경로 |
| `ICON_SET`, `SESSION_DAYS` | 기본 unicode, 웹 세션 30일 |
| `DEMO_MODE`, `APP_NOW`, `NOTIFY_OVERRIDE_SLACK_USER` | 실사용은 false / 빈 값 / 빈 값 |

새 DB는 비어 있습니다. 샘플 직원이나 예약을 자동으로 넣지 않습니다. 기존 운영 기록이 있으면 아래 이관 절차를 먼저 수행하세요. `.env`, SQLite 파일, QR 토큰과 로그는 Git에 올리지 않습니다.

기본 저장 위치는 프로젝트의 `data/roombot.sqlite3`입니다. 운영에서는 `.env`의 `SQLITE_PATH`에 절대 경로를 지정하는 것을 권장합니다. DB 파일을 둘 수 있는 로컬 디스크를 사용하세요.

```dotenv
SQLITE_PATH=/absolute/path/to/meetroom/data/roombot.sqlite3
APP_TIMEZONE=Asia/Seoul
APP_NOW=
WEB_PORT=8000
ROOM_CHANNELS=true
DEMO_MODE=false
```

`DATABASE_URL`은 앱 실행 설정으로 사용하지 않습니다. 아래 일회성 이관 명령에서만 읽습니다. `APP_NOW`는 시연용 가상 시계이므로 실제 운영에서는 비웁니다. `SECRET_KEY`와 `QR_SECRET`은 충분히 긴 임의 값으로 설정하고 보관하세요. `QR_SECRET`을 바꾸면 기존 인쇄 QR이 무효가 됩니다.

## 로컬 정보 등록

직원과 회의실은 운영 PC의 CLI로 등록합니다. 이메일이 전체 주소까지 일치하는 Slack 계정은 최초 사용 시 자동 연결합니다. 이메일의 `@` 앞부분만으로 직원을 추정하지 않습니다. 필요하면 직원 등록 시 확인한 Slack 사용자 ID를 지정할 수 있습니다.

```sh
uv run python -m backend.cli employee-add \
  --emp-no E0001 --name '홍길동' --email 'hong@example.com' \
  --department '운영팀' --position '팀원' --slack-user U0123456789
uv run python -m backend.cli room-add \
  --code 9A --name 9A --floor 9 --capacity 6
uv run python -m backend.cli db status
```

위 값은 명령 형식 예시입니다. 실제 직원과 회의실 정보로 바꿔 입력하세요. 기존 이관 DB에는 등록 명령을 다시 실행할 필요가 없습니다. 회의실을 새로 등록한 뒤 앱을 재시작하면 채널 준비 작업이 실행됩니다.

## Slack 사용법

앱에 등록된 `/room` 또는 `/룸` 명령을 사용합니다. 앱 DM에서도 같은 내용을 입력할 수 있습니다.

| 작업 | 예시 |
|---|---|
| 전체 현황 | `/room 지금` |
| 회의실 일정 | `/room 9A 내일` |
| 미팅룸 | `/room 미팅룸 내일 14:00 1h 6명` |
| 예약 등록 | `/room 예약 9A 내일 14:00 1h 팀 회의` |
| 내 예약·예약번호 | `/room 내예약` |
| 참석자 추가 | `/room 참석자 123 E0002` 또는 연결된 Slack `@멘션` |
| 예약 취소 | `/room 취소 123` |
| 노쇼 통계 | `/room 통계` |
| 도움말 | `/room 도움말` |

예약자는 자동으로 참석자에 포함됩니다. 참석자 추가와 취소는 예약자 또는 `ADMIN_SLACK_USER`에 지정된 관리자만 할 수 있습니다. 시간이 겹치는 예약은 DB에서도 거부합니다. 노쇼 또는 직접 취소한 시간은 새로 예약할 수 있습니다. 직접 취소한 예약과 체크인 이력은 삭제하지 않습니다. 다른 참석자에게 별도 예약 변경 DM을 발송하지 않으므로 변경 내용은 예약자가 공유해 주세요.

관리자 강제 배정은 `/room qwerasdf 9A 내일 14:00 2h 공사`로 미리 본 뒤 버튼으로 확정합니다. 겹치는 예약의 내용과 참석자·체크인 기록을 보존하고 취소 상태로 바꿉니다. 강제 배정 예약에는 노쇼 규칙을 적용하지 않습니다.

## Slack·QR 설정

1. [Slack 앱 관리](https://api.slack.com/apps) → Create New App → From a manifest에서 `slack_manifest.yaml`을 사용합니다.
2. 기본 채널 QR 모드는 매니페스트의 `redirect_urls`와 `scopes.user` 항목을 제거하고 생성하세요. 웹 QR 모드라면 예시 도메인을 실제 `PUBLIC_BASE_URL`로 바꾸고 Sign in with Slack의 Redirect URL에도 같은 `/auth/slack/callback`을 등록합니다.
3. 워크스페이스에 설치해 OAuth & Permissions의 Bot User OAuth Token을 복사합니다.
4. Socket Mode를 켜고 Basic Information → App-Level Tokens에서 `connections:write` 앱 토큰을 만듭니다.
5. Basic Information의 App ID, 워크스페이스 URL/정보의 Team ID를 확인해 `.env`에 채웁니다.
6. 매니페스트의 Bot Events와 Interactivity가 활성화됐는지 확인합니다. 권한을 바꾸면 앱을 다시 설치합니다.

매니페스트에는 기본 채널 QR에 필요한 `channels:read`, `channels:manage`, `channels:join`, `channels:history`, `pins:write`, `member_joined_channel`도 포함합니다. 채널은 공개 채널로 생성됩니다. 워크스페이스가 봇의 채널 생성·퇴장을 금지하면 관리자에게 허용을 요청하거나 웹 QR 모드로 설정하세요. [Slack 채널 관리 권한](https://docs.slack.dev/reference/scopes/channels.manage/)을 참고하세요. `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, 워크스페이스/앱 ID를 `.env`에 입력합니다. 필요하면 `.venv/bin/python -m backend.setup_env`의 로컬 입력 페이지를 사용할 수 있습니다.

회의실 채널을 사용하는 경우 앱에 `channels:read`, `channels:manage`, `channels:join`, `channels:history`, `pins:write` 등 사용하는 Slack API에 맞는 권한과 `member_joined_channel` 이벤트가 필요합니다. 워크스페이스 정책에 따라 봇의 채널 관리 권한도 확인하세요. 기존 운영 앱은 현재 설치된 권한과 채널 연결을 그대로 사용합니다.

```sh
uv run python -m backend.cli qr
```

`qr/index.html`에서 인쇄합니다. 채널 방식에서는 QR이 Slack 채널을 엽니다. 웹 방식(`ROOM_CHANNELS=false`)에서는 휴대폰에서 접근 가능한 HTTPS `PUBLIC_BASE_URL`과 Slack OpenID 설정(`SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `/auth/slack/callback` redirect URL)이 필요합니다. `CHECKIN_ALLOWED_CIDRS`, `TRUST_PROXY_HEADERS`는 실제 네트워크·프록시 구성에 맞춥니다. `DEMO_MODE=true`는 개발용 로그인과 수동 직원 연결을 허용하므로 사용 범위를 확인해 설정하세요.

## 모바일 이미지 표시

공유되지 않은 봇 전용 `slack_file`은 사용자에게 접근 오류가 날 수 있어 이미지 블록에 사용하지 않습니다. 일정 이미지는 서명과 만료가 있는 HTTPS 주소로 제공합니다. 서명 없는 요청은 404입니다. 앱 버튼에는 외부 이미지 뷰어 링크를 넣지 않습니다. 이미지 주소가 없으면 간단한 글자 목록으로 표시합니다.

운영에는 `IMAGE_BASE_URL=https://images.example.com`처럼 **고정 HTTPS 주소**를 지정하고, 이미지 전용 로컬 서버 `127.0.0.1:8001`로 연결하세요 (`IMAGE_PORT`로 변경 가능). 전용 서버는 이미지·보기·상태 확인 경로만 제공하며 QR 로그인·개발용 직원 로그인·예약 API는 노출하지 않습니다. HTTPS 주소가 준비되지 않으면 깨진 이미지 대신 글자 목록만 보여 줍니다. `PUBLIC_BASE_URL`을 HTTPS로 운영 중이면 그 주소도 사용할 수 있습니다.

즉시 연결이 필요한 환경에서는 설치된 `cloudflared`와 `IMAGE_TUNNEL=true`를 사용할 수 있습니다. `CLOUDFLARED_BIN`에 실행 파일 경로를 지정합니다. 앱이 이미지 전용 임시 터널을 실행·종료하고, 연결 종료 시 재연결합니다. `logs/image-url.txt`에서 현재 주소를 확인할 수 있습니다. Quick Tunnel은 임시 연결이므로 고정 운영 주소를 대신하지 않습니다. 재시작으로 주소가 바뀌면 예전 메시지의 이미지 링크는 만료될 수 있어 새로 조회해야 합니다. QR 주소와 토큰은 바꾸지 않습니다.

## 기존 PostgreSQL 기록을 SQLite로 이관

이 도구는 기존 `daou`/`bot` 스키마의 기록을 읽기 전용 트랜잭션으로 복사합니다. 다우오피스 API를 호출하지 않습니다. 운영 앱은 이관 후 PostgreSQL에 연결하지 않습니다.

1. 현재 앱과 같은 DB를 쓰는 CLI·스케줄러를 멈춰 새 기록이 생기지 않게 합니다. 원본 DB를 별도로 백업합니다.
2. 기존 `.env`의 `DATABASE_URL`을 이관 원본으로 유지하고, **아직 없는** SQLite 파일을 대상으로 실행합니다.

```sh
uv run --extra migration python -m backend.migrate \
  --output /absolute/path/to/meetroom/data/roombot.sqlite3
```

3. 출력된 테이블별 건수를 확인합니다. 이관 도구는 각 행의 값, 외래키, SQLite 무결성을 검증하며 기존 파일을 덮어쓰지 않습니다. 원본 DB는 변경하지 않습니다. 직원·예약 ID, QR 교체 토큰, Slack 계정·채널·패널 연결, 체크인, 알림 중복 방지 기록, 관리자 취소 이력과 ID 증가 상한을 보존합니다. 이전 연동 시각은 `legacy_synced_at`에 보관하며 외부 동기화에는 사용하지 않습니다.
4. `.env`에서 `DATABASE_URL`을 제거하고 `SQLITE_PATH`를 대상 파일의 절대 경로로 설정한 뒤 앱을 실행합니다. `db status`, `/healthz`, Slack 연결과 일정을 확인합니다.
5. PostgreSQL 드라이버가 필요 없는 기본 환경으로 정리하려면 앱을 멈춘 상태에서 `uv sync --locked`를 실행합니다. 다른 프로젝트가 쓰는 PostgreSQL 서버는 중지하지 않습니다.

실패한 이관의 새 대상 파일은 삭제됩니다. 원본으로 돌아가야 한다면 SQLite 전환 전 코드·설정을 복구하세요. 전환 후 새로 생긴 SQLite 기록은 원본 PostgreSQL에 자동 복사되지 않으므로 복귀 전에 별도로 보존해야 합니다.

## 저장·백업·운영

SQLite는 외래키 검사, WAL과 쓰기 잠금(`BEGIN IMMEDIATE`)을 사용합니다. 체크인·노쇼 자동 취소·관리자 배정은 쓰기 트랜잭션으로 직렬화합니다. 시각은 UTC로 저장하고 `APP_TIMEZONE`으로 표시합니다. 저장 시각과 JSON 배열의 변환은 앱 DB 모듈에서 수행합니다.

온라인 백업은 WAL까지 포함하는 SQLite backup API를 사용합니다. 실행 중인 DB 파일만 단순 복사하지 마세요.

```sh
uv run python -m backend.cli db backup /absolute/path/to/backups/roombot-2026-11-03.sqlite3
uv run python -m backend.cli db status
curl --fail http://127.0.0.1:8000/healthz
```

복구할 때는 앱·CLI를 모두 멈추고 현재 DB와 `-wal`, `-shm`을 함께 별도 보관한 뒤 백업을 새 SQLite 경로로 복사해 `SQLITE_PATH`를 바꿉니다. 상태를 확인한 뒤 앱을 다시 실행합니다. SQL 뷰는 앱의 `app_now`, `shift_time`, `room_label` 함수를 사용하므로 뷰 조회에는 `backend.db.tx()` 연결을 사용하세요. 일반 테이블은 SQLite 도구로 확인할 수 있습니다.

macOS 선택 기능인 `scripts/autostart.sh install`은 앱과 임시 터널을 launchd에 등록합니다. DB 서버는 실행하거나 중지하지 않습니다. `status`, `restart`, `uninstall`을 지원합니다. 기존 PostgreSQL용 launchd 작업이 있다면 소유·사용 범위를 확인해 따로 정리하세요. 현재 실행 상태는 실제 프로세스와 포트로 확인해야 하며, 이 스크립트가 자동 설치되는 것은 아닙니다. 임시 터널 주소가 바뀌면 웹 QR·OAuth 설정도 확인해야 합니다.

스케줄러는 30초 간격입니다. 노쇼 적용 시작 시각은 `system_settings.noshow_enforced_from`에 저장되어 첫 기동 전에 이미 시작한 회의를 일괄 취소하지 않습니다. `MORNING_BRIEF_AT`을 비우면 아침 브리핑을 끕니다. 로그에는 직원·예약 정보가 포함될 수 있으므로 접근 권한을 제한하세요.

## 검증

```sh
uv sync --locked
PYTHON_DOTENV_DISABLED=1 uv run python -m pytest -q
```

테스트는 임시 SQLite 파일로 실행하며 실제 Slack 메시지를 보내지 않습니다. 예약 충돌과 취소 이력, 체크인 경계, 중복 리마인더, 노쇼 경쟁, 관리자 배정, 시간대, 백업과 로컬 예약 명령을 검증합니다.

## 요청한 가상 일정 생성

운영자가 명시적으로 요청한 시연 데이터만 CLI로 생성합니다. 본인 계정에 연결된 직원 ID를 확인해 `--exclude-employee`에 지정하세요. 기본 범위는 내일부터 7일이며, 주말·공휴일을 제외하고 회의실당 하루 최대 4건을 만듭니다. 기존 예약과 직원의 동시 참석 시간을 피합니다.

```sh
uv run python -m backend.simulations --exclude-employee 123 --days 7
# 위 123은 실제 본인 직원 ID로 바꿉니다. 출력의 batch 값을 보관하세요.
uv run python -m backend.simulations --clear-batch 생성된_batch_값
```

제목에 `[가상]`을 붙이고 별도 생성 묶음으로 추적합니다. 본인은 예약자·참석자 모두에서 제외합니다. 가상 예약은 실제 체크인, 노쇼 자동 취소, 리마인더·아침 브리핑·관리자 취소 DM, 실제 이용 통계에서 제외됩니다. 생성 묶음을 삭제해도 기존 실제 예약은 보존합니다. SQLite v1 DB는 앱 기동 시 v2로 자동 업그레이드되며 기존 기록은 유지됩니다.

## 새 워크스페이스 설치 후 확인

1. 앱 실행 로그의 `슬랙봇 연결됨`과 `curl --fail http://127.0.0.1:8000/healthz`를 확인합니다.
2. Slack 앱 홈과 `/room 지금`에서 등록한 회의실이 보이는지 확인합니다. `/룸`은 매니페스트에 직접 추가해야 하며 기본 명령은 `/room`입니다.
3. 직원 이메일 또는 `--slack-user` 연결 후 실제 예약 1건을 생성하고 참석자 계정으로 QR 체크인·취소를 확인합니다.
4. `missing_scope`는 앱 재설치, `not_in_channel`은 채널 참여, 이미지 누락은 HTTPS 주소·한글 폰트·프록시의 이미지 경로 연결을 확인합니다. QR 생성 전에 앱의 회의실 채널 준비가 끝나야 합니다.

2026-11-10: 의존성은 2주 안정화 정책(2026-10-27 이전 배포)을 유지하며 lock을 갱신했습니다. 새 설치 환경에서 핵심 테스트 26개가 통과했습니다. 실제 Slack 토큰·워크스페이스 설치는 검증하지 않았습니다. CI는 Linux의 Python 3.12 설치·테스트를 수행합니다.

## 수정할 파일 찾기

```text
backend/                실행·Slack 이벤트·웹 인증·업무 로직·SQLite
  main.py               앱 시작
  slack_app.py          명령·버튼 이벤트 연결
  web.py                HTTP·세션·Slack 로그인·체크인
  bookings.py/checkin.py 예약·체크인·노쇼 규칙
  db.py/schema*.sql     저장·마이그레이션
frontend/               화면·이미지·QR 출력
  slack_views.py        Slack 홈·예약 모달
  blocks.py/style.py    공통 Slack 화면 요소
  pages.py              모바일 체크인 HTML
  charts.py/qr.py       일정 PNG·인쇄 QR
  assets/               아이콘·이미지
tests/                  핵심 동작 회귀 검사
```

Slack 화면은 별도 웹 서버로 빌드하지 않습니다. 화면 파일은 Slack Block Kit와 서버가 반환하는 HTML/PNG입니다. 기존 `python -m roombot...` 명령은 `python -m backend...`로 바뀌었습니다. DB 경로·테이블·쿠키·QR 비밀값은 그대로 사용합니다. 기존 launchd 등록은 앱을 멈춘 후 `scripts/autostart.sh install`로 실행 경로를 갱신하세요.

기본 검사는 `uv run --locked python -m pytest -q` 한 번입니다. CI의 OS 행렬과 모양·매니페스트 문자열 고정 검사를 줄였고, 예약 충돌·인증·체크인 경계·노쇼 경쟁·백업·마이그레이션·HTTP 검사는 유지합니다.
