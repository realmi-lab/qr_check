# Slack 앱 공통 설정

두 저장소는 같은 `.env` 이름·읽기 순서·Socket Mode 연결 방식을 사용합니다.

```dotenv
SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=
SLACK_NOTIFY_CHANNEL=
APP_TIMEZONE=Asia/Seoul
```

| 이름 | 공통 의미 |
| --- | --- |
| `SLACK_BOT_TOKEN` | 해당 앱을 설치한 워크스페이스의 Bot User OAuth Token (`xoxb-`) |
| `SLACK_APP_TOKEN` | 해당 앱의 connections:write App-Level Token (`xapp-`) |
| `SLACK_NOTIFY_CHANNEL` | 봇이 게시할 채널 ID (`C...` 또는 `G...`). 회의실 앱은 노쇼 알림용으로 선택, 면접 앱은 앱 홈에서 시작한 조율의 게시 채널로 필수 |
| `APP_TIMEZONE` | 날짜·시간을 해석하는 IANA 시간대. 기본 Asia/Seoul |
| `SQLITE_PATH` | 로컬 저장 파일. 상대 경로는 각 저장소 루트 기준. 기본 파일은 앱마다 다르며 DB를 공유하지 않음 |

## 같은 순서로 설치하기

1. 각 저장소의 매니페스트로 Slack 앱을 만들고 설치합니다. 앱마다 자신의 토큰을 사용합니다.
2. Socket Mode를 켜고 connections:write 앱 토큰을 만듭니다. Interactivity와 매니페스트의 이벤트도 켭니다.
3. `cp .env.example .env` 후 값을 입력하고, 게시할 채널에 봇을 초대합니다. Windows PowerShell은 `Copy-Item .env.example .env`입니다.
4. 설정 검사 후 실행합니다.

| 작업 | slack_qr_check | slack-interview-app |
| --- | --- | --- |
| 의존성 설치 | `uv sync --locked` | `npm ci` |
| 설정 검사 | `uv run --locked python -m backend.doctor` | `npm run doctor` |
| 실행 | `uv run --locked python -m backend.main` | `npm start` |
| 핵심 검사 | `uv run --locked python -m pytest -q` | `npm test` |

두 앱 모두 **실행 환경변수 → 저장소 루트 .env → 기본값** 순서입니다. `.env`를 바꾸면 재시작합니다. `.env`·토큰·DB는 Git에 넣지 않습니다. 설정 검사는 로컬 형식만 확인하고 Slack API·메시지 전송은 하지 않습니다. 잘못된 토큰 형식은 시작 전에 오류로 알려 줍니다. 실제 토큰 유효성과 앱 권한은 Slack 연결 후 확인합니다.

## 수정할 때도 같은 위치

- `backend/config.py` / `backend/config.mjs`: `.env` 로딩·설정. 예전 `TZ`, `SLACK_NOTIFICATION_CHANNEL`은 호환 이름으로만 허용합니다. 새 설정은 위 공통 이름을 사용하세요.
- `backend/slack.py` / `backend/slack.mjs`: 토큰 확인·Socket Mode 연결.
- 각 앱의 이벤트 처리 파일: 명령·버튼을 업무 기능으로 연결.
- `frontend/`: Slack Block Kit·화면 출력.

언어는 Python과 JavaScript로 유지합니다. 별도 공통 패키지를 설치할 필요 없이 같은 파일 역할과 설정 이름을 따릅니다. 회의실 앱의 QR·직원·회의실·웹 로그인 설정과 면접 앱의 후보 시간 설정은 각 README와 `.env.example`에 따로 설명합니다.
