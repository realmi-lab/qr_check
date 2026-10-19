# 회의실봇 — 다우오피스 회의실 조회 + QR 체크인 노쇼 방지

다우오피스의 회의실 예약을 슬랙에서 조회하고, 회의실 QR 체크인으로 노쇼를 막는 슬랙봇입니다.
지금은 다우오피스에 연결하지 않고 PostgreSQL 샘플 DB로 동작합니다.

## 규칙 (1차)

| 항목 | 내용 |
|---|---|
| 슬랙봇 범위 | **조회만** (예약 생성·변경은 다우오피스에서) |
| 체크인 | 회의실 QR을 휴대폰으로 찍고 Slack 로그인 → **참석자 중 누구나** 체크인하면 인정 |
| 체크인 가능 시간 | 시작 **10분 전 ~ 10분 후** (앞 회의가 일찍 끝나 겹쳐도 다음 회의 체크인 가능, 현황은 미리 체크인한 다음 회의로) |
| 노쇼 | 시작 후 **10분** 안에 아무도 체크인하지 않으면 자동 취소 → 참석자 DM + 채널 공지 |
| 대신 체크인 | 예약자가 휴가 등으로 못 오면, 대신 가는 사람을 **다우오피스에서 그 예약의 참석자로 추가** → 바로 체크인 가능 (봇은 바꾸지 않음, 참석자가 아닌 사람이 찍으면 이렇게 안내) |
| 알림 | 시작 10분 전 "체크인해 주세요", 시작 5분 후 "5분 남음" 경고, 취소 시 안내 + "지금 빈 회의실 찾기" 버튼, 매일 아침 브리핑 |
| 체크인 확인 정보 | 화면과 슬랙 DM에 **체크인한 사람 / 회의실 / 체크인 시간** + 회의·예약자 |

## 구성

```
휴대폰 QR 스캔 ─→ [QR 체크인 웹 /c/BTC?t=…] ─ Slack 로그인 ─┐
                                                          ├─→ [PostgreSQL]
슬랙 /room 명령 ─→ [슬랙봇 (Socket Mode)] ─────────────────┤     daou: 다우오피스 (예약만)
                                                          │     bot : 체크인·노쇼 기록
                   [노쇼 스케줄러 30초마다] ─ DM·채널 공지 ──┘
```

| 파일 | 역할 |
|---|---|
| [db/01_schema.sql](db/01_schema.sql) | 테이블·뷰. 회의실 상태(사용중/체크인 대기/노쇼 취소…)는 저장하지 않고 예약+체크인 기록으로 계산 |
| [db/02_seed.sql](db/02_seed.sql) | 다우오피스 샘플 데이터 (예약만) |
| [db/03_demo_activity.sql](db/03_demo_activity.sql) | 시연용: 오늘 체크인·노쇼가 있었던 것처럼 bot 데이터 생성 |
| [roombot/rooms.py](roombot/rooms.py) | 조회: 현황, 회의실 일정, 빈 회의실, 내 예약 |
| [roombot/checkin.py](roombot/checkin.py) | QR 체크인, 노쇼 자동취소, 알림 대상 계산 |
| [roombot/commands.py](roombot/commands.py) | `/룸` 명령 해석과 답변 (슬랙·CLI 공용) |
| [roombot/style.py](roombot/style.py) | 슬랙 화면 표기 규칙: 상태 아이콘·이름, 기능 아이콘 (여기만 고치면 전체가 바뀜) |
| [roombot/web.py](roombot/web.py) | QR 체크인 웹 + Sign in with Slack |
| [roombot/slack_app.py](roombot/slack_app.py) | 슬랙 `/룸`, 앱 홈 탭, 봇 DM |
| [roombot/scheduler.py](roombot/scheduler.py) | 노쇼 처리·리마인더 |
| [roombot/qr.py](roombot/qr.py) | 회의실별 QR 이미지 + 인쇄용 페이지 |
| [slack_manifest.yaml](slack_manifest.yaml) | 슬랙 앱 설정 (붙여넣기만 하면 됨) |

## 회의실

건물은 6~10층, 22~25층이고 회의실은 아래 20개입니다 (6~8층에는 회의실 없음).
`/룸` 에는 코드(`9A`, `BTC`), 이름(`bitcoin`), 층(`22층`) 어느 것이든 쓸 수 있어요.

| 층 | 회의실 (코드 · 인원) |
|---|---|
| 9층 | 9A·4 · 9B·6 · 9C·8 · 9D·12 |
| 10층 | 10A·4 · 10B·6 · 10C·8 · 10D·12 |
| 22층 | Bitcoin (BTC·6) · Ethereum (ETH·8) · Solana (SOL·12) |
| 23층 | Ripple (XRP·6) · Cardano (ADA·8) · Polkadot (DOT·12) |
| 24층 | Avalanche (AVAX·8) · Chainlink (LINK·12) · Polygon (POL·16) |
| 25층 | Cosmos (ATOM·10) · Litecoin (LTC·20) · Stellar (XLM·20) |

## 슬랙 기능

명령어는 **`/룸`** (예전 `/room`도 그대로 됨).

| 명령 / 화면 | 내용 |
|---|---|
| `/룸 지금` (now, 현황) · `/룸 22층` | 층별 현황표 [회의실 \| 지금·다음]. 위에 층별로 묶인 "🚪 회의실 일정 보기" 메뉴 하나 |
| `/룸 BTC` · `/룸 9A 내일` · `/룸 bitcoin 10/20` | 하루 일정표 [시간 \| 회의·참석자], 빈 시간도 시간 순서대로. ◀ ▶ 날짜 이동 |
| `/룸 22층 내일` | 그 날 층의 회의실별 예약 수와 빈 시간 |
| `/룸 빈방 오후 2시 1시간 6명` (free, `14:00 1h`도 됨) | 빈 회의실 찾기. 없으면 가까운 다른 시간대 버튼 |
| `/룸 찾기` | 날짜·시간·사용 시간·인원을 골라서 찾는 창 |
| `/룸 내예약` (my) | 내 예약과 체크인 상태 |
| `/룸 통계` | 최근 7일 체크인·노쇼 수, 노쇼율, 노쇼 많은 회의실·예약자 |
| 앱 홈 탭 | 대시보드: 빠른 버튼, 내 다음 회의와 체크인 안내, 층별 현황 |
| 봇 DM | `now`, `BTC 내일`처럼 적어도 됨. 체크인 완료·리마인더·노쇼 취소 알림, 매일 아침 브리핑(`MORNING_BRIEF_AT`, 기본 08:30) |

슬랙 계정은 **슬랙 프로필 이메일**로 다우오피스 직원과 자동 연결됩니다. 이메일이 똑같으면 바로, 아니면 **@ 앞 아이디**가 같은 사람으로 찾아요(도메인만 다른 경우). 아이디가 겹치는 사람이 둘 이상이면 연결하지 않아요.

QR을 찍으면 그 사람의 회의 시각으로 안내해요: "회의 시작 30분 전입니다. 20분 후에 체크인하세요." / "체크인은 3분 안에 해주세요 (10:05까지)." / "회의 시작 4분 전에 미리 체크인했어요."

**그림(현황판·타임라인)** 은 슬랙에 직접 올려서 넣습니다(봇 권한 `files:write`). 임시 터널 주소로 넣으면 주소가 바뀌는 순간
예전 메시지의 그림이 깨지고, 휴대폰은 "메시지를 표시할 수 없음 … 이미지가 삭제되었거나 액세스 권한이 없음"으로 보여 주기 때문입니다.
- 같은 그림은 5분 단위로 한 번만 올리고, 올린 뒤 약 2초 기다렸다가 씀 (슬랙 처리 시간)
- 07–21시에는 오늘 현황판을 미리 올려 둠 → 홈 탭이 바로 뜸
- 30일 지난 그림은 슬랙에서 지움 (`bot.slack_images`에 기록)
- 권한이 없으면 예전처럼 서명된 주소(`/img/…png`, GET·HEAD 모두 응답)를 씀

## 아이콘

상태 표시는 4가지뿐: 🔴 사용중 · 🟡 체크인 대기 · ⚪ 종료 · 🟢 예약없음 (예정 회의는 표시 없음, 시작 전에 미리 체크인하면 사용중, 규칙은 `roombot/style.py`)

`.venv/bin/python -m roombot.assets` 로 `assets/` 에 만들어집니다.

| 파일 | 쓰는 곳 |
|---|---|
| `assets/app-icon.png` (1024px) | 슬랙 앱 아이콘 — api.slack.com/apps → 회의실봇 → Basic Information → Display Information → App icon 에 올리기 |
| `assets/emoji/rb-*.png` (8개) | 상태 커스텀 이모지 (선택). 워크스페이스에 같은 이름으로 올린 뒤 `.env`에 `ICON_SET=custom` |
| `assets/favicon.png`, `qr-logo.png` | 체크인 웹 파비콘, QR 가운데 로고 |
| QR 가운데 | 기본은 **방 코드 글자 배지**(둥근 테두리 + 굵은 글자). 방마다 따로 넣고 싶으면 `assets/qr-icons/custom/{코드}.png` (지금은 9D만). Codex로 만든 캐릭터 그림은 `assets/qr-icons/v1`, `v2`에 보관 |

## QR 체크인 (슬랙 채널 방식, 기본)

회의실마다 슬랙 채널(`#회의실-9a`, `#회의실-bitcoin` …)이 있고, 회의실 QR은 그 채널을 여는 **슬랙 링크**예요.
채널 주소는 바뀌지 않아서 **QR은 한 번만 인쇄**하면 되고, 도메인·터널·로그인 화면이 필요 없어요.

**체크인은 QR로 연 회의실 채널에서만** 할 수 있어요. 봇 DM·홈·명령어로는 체크인되지 않아요.

| 순서 | 하는 일 |
|---|---|
| 1 | 회의실 입구 QR을 휴대폰으로 찍음 → 슬랙 앱에서 그 회의실 채널이 열림 |
| 2 | 패널에 "체크인하시겠습니까? 유효시간은 ○○까지입니다." → **[채널 참여]** 를 누름 = 체크인 (결과가 바로 보이고 봇 DM으로도) |
| 다음부터 | 이미 채널에 들어와 있으니 QR → 채널 → 체크인 시간에만 보이는 패널의 **[✅ 체크인]** (채널에서 내보내지 않음: 내보내면 슬랙이 '퇴장' 알림을 보냄) |

QR 주소는 `https://realmihq.slack.com/archives/<채널ID>` 예요. 휴대폰에 슬랙 앱이 있고 **그 워크스페이스에 로그인돼 있으면** 앱에서 바로 열려요.
(`slack.com/app_redirect` 주소는 휴대폰에서 브라우저 로그인 화면으로 가서 쓰지 않아요. 다른 워크스페이스만 로그인돼 있어도 앱이 열리지 않아요.)

- 결과(체크인 완료 / 예약한 회의실이 아님 / 아직 시간 전 …)는 **누른 사람에게만 보이는 메시지**로 채널에 뜨고, 봇 DM으로도 와요.
- 패널은 지금·다음 회의가 바뀔 때마다 자동으로 고쳐지고, 무료 플랜 90일 보관 때문에 30일마다 새로 올라가요.
- 채널은 서버가 켜질 때 자동으로 준비돼요. 이미 있으면 그대로 써요.
- 예전 웹 체크인 방식으로 돌아가려면 `.env`의 `ROOM_CHANNELS=false` 후 QR을 다시 만드세요.
- 슬랙 앱 권한: `channels:manage` `channels:read` `channels:join` `pins:write` (+ 슬랙이 같이 붙인 `groups:read` `mpim:read`), 이벤트 `member_joined_channel`.

## QR 인쇄

- QR 토큰은 `.env`의 `QR_SECRET`으로 계산해서 **DB를 다시 만들어도 바뀌지 않아요.** `QR_SECRET`은 절대 바꾸지 마세요.
- 슬랙 채널 방식에서는 QR 안에 슬랙 채널 링크만 들어가서 **바로 인쇄해도 돼요.** (웹 방식일 때만 임시 주소 문제가 있음)
- `.venv/bin/python -m roombot.cli qr` → `qr/index.html` 을 열어 A4로 인쇄 (한 장에 4개).
- 특정 회의실 QR만 바꾸려면 `bot.room_qr_tokens` 에 그 회의실의 새 토큰을 넣고 다시 만들면 돼요.

## 성능

다우오피스 실데이터처럼 예약이 1년치(33,082건) 쌓인 DB로 측정 (`scripts/bench.py`):

| 항목 | 결과 |
|---|---|
| 지금 회의실 현황 조회 | 12.9ms → **0.27ms** (지금 시각에 걸친 예약만 먼저 고르고, 회의실·시간 인덱스 사용) |
| 모든 `/룸` 명령 | 최대 **4ms** 이내 |
| 알림 발송 | 한 명씩 → **8명씩 동시에** 보냄. 슬랙에 없는 사람은 다시 찾지 않음 |
| 서버 시작 | 첫 노쇼 처리를 기다리지 않고 바로 시작 |

## 샘플 데이터

| 항목 | 내용 |
|---|---|
| 기간 | 2026-10-01 ~ 10-24 (지난 기간 + 오늘 + 이후 7일). 평일 09~18시, 공휴일 제외. 오늘은 요일과 상관없이 포함 |
| 회의실 / 직원 | 20개 (9·10층, 22~25층, 4~20인) / 200명 (이름 중복 없음, 한 사람이 같은 시간에 두 회의에 들어가지 않음) |
| 예약 | 2,363건, 점유율 71.0% |
| 다우오피스 영역 | 예약 건만 있음 (노쇼·직접 취소 상태 없음) |

| DB | 용도 |
|---|---|
| `daouoffice` | 본 DB. 체크인·노쇼 기록 없음 (시스템을 처음 켠 상태) |
| `daouoffice_demo` | 시연용. 10/17 10:03 기준으로 체크인 26건·노쇼 취소 1건이 있는 상태 |
| `daouoffice_test` | 테스트가 매번 새로 만듦 |

## 실행 (이 Mac)

PostgreSQL은 Postgres.app(16)으로 설치되어 있고, 아래 '상시 운영'의 자동 실행으로 켜집니다.
Python 패키지는 `uv`로 설치되어 있습니다 (`~/.local/bin/uv`).

**터미널에서 바로 확인 (슬랙 없이)**

```bash
DATABASE_URL=postgresql://$USER@localhost:5432/daouoffice_demo .venv/bin/python -m roombot.cli --now 2026-10-17T10:03 room now
```

```bash
DATABASE_URL=postgresql://$USER@localhost:5432/daouoffice_demo .venv/bin/python -m roombot.cli --now 2026-10-17T10:03 room free 14:00 1h 10명
```

```bash
DATABASE_URL=postgresql://$USER@localhost:5432/daouoffice_demo .venv/bin/python -m roombot.cli --now 2026-10-17T10:03 --as D0030 room my
```

**전체 실행 (웹 + 스케줄러 + 슬랙봇)**

```bash
cp .env.example .env
```

`.env`에 슬랙 토큰 등을 채운 뒤:

```bash
.venv/bin/python -m roombot.main
```

슬랙 토큰이 없으면 웹과 스케줄러만 켜지고, 알림은 터미널에 출력됩니다.
`DEMO_MODE=true`면 Slack 로그인 대신 사번으로 로그인할 수 있습니다.

**테스트**

```bash
.venv/bin/python -m pytest -q
```

**DB 다시 만들기**

```bash
/Applications/Postgres.app/Contents/Versions/16/bin/psql -d daouoffice -f db/01_schema.sql -f db/02_seed.sql -f db/verify.sql
```

```bash
PGOPTIONS="-c app.now=2026-10-17T10:03+09" /Applications/Postgres.app/Contents/Versions/16/bin/psql -d daouoffice_demo -f db/01_schema.sql -f db/02_seed.sql -f db/03_demo_activity.sql
```

## 슬랙에 연결하기

1. https://api.slack.com/apps → **Create New App → From a manifest** → [slack_manifest.yaml](slack_manifest.yaml) 붙여넣기 (`YOUR-DOMAIN`을 웹 주소로 변경)
2. **Install to Workspace**
3. **Basic Information → App-Level Tokens**에서 `connections:write` 토큰(xapp-) 생성
4. `.env`에 `SLACK_BOT_TOKEN`(xoxb-), `SLACK_APP_TOKEN`(xapp-), `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_APP_ID`, `SLACK_TEAM_ID` 입력
   — 토큰 3개는 `.venv/bin/python -m roombot.setup_env` 로 여는 로컬 입력 페이지(http://127.0.0.1:8765)에 붙여넣어도 됩니다 (봇 토큰은 저장할 때 바로 확인)
5. 휴대폰에서 열 수 있는 **HTTPS 주소** 준비 → `PUBLIC_BASE_URL`에 입력 (개발 중에는 cloudflared나 ngrok 터널)
6. `.venv/bin/python -m roombot.cli qr` → `qr/index.html` 인쇄해서 회의실마다 부착

샘플 직원 이메일(user001@example.com…)은 실제 슬랙에 없으므로, 시연할 때는
`DEMO_MODE=true` + `/룸 link D0030`(사번으로 내 슬랙 계정 연결) + `NOTIFY_OVERRIDE_SLACK_USER=내 슬랙 ID`(모든 DM을 나에게)를 쓰세요.

## 상시 운영 (자동 실행 · 재시작 · 잠자기 방지)

`scripts/autostart.sh install` 로 아래 3개를 macOS 로그인 항목(launchd)으로 등록해 두었습니다.
Mac에 로그인하면 자동으로 켜지고, 멈추면 10초 안에 다시 켜집니다.

| 이름 | 하는 일 | 로그 |
|---|---|---|
| `com.roombot.postgres` | PostgreSQL 16 (Postgres.app의 데이터 폴더 그대로 사용) | `logs/postgres.log` |
| `com.roombot.server` | 회의실봇 (QR 웹 + 노쇼 스케줄러 + 슬랙봇). `caffeinate`로 감싸서 도는 동안 **잠자기 방지** (전원 연결 시) | `logs/server.log` |
| `com.roombot.tunnel` | cloudflared 임시 터널. 주소가 바뀌면 `.env`를 고치고 서버를 다시 시작 | `logs/tunnel.log` |

```bash
scripts/autostart.sh status
```

```bash
scripts/autostart.sh restart
```

```bash
scripts/autostart.sh uninstall
```

- 상시 운영은 **본 DB(`daouoffice`) + 실제 시각**으로 돌아요. 시연할 때만 `.env`의 `DATABASE_URL`을 `daouoffice_demo`로, `APP_NOW`를 켜고 `scripts/autostart.sh restart`.
- 지금 공개 주소는 `logs/public-url.txt` 에 있어요.
- 맥북이라면 **덮개를 닫으면** 잠자기에 들어가요 (외부 모니터·전원 연결 상태 제외). 데스크톱은 상관없어요.
- Postgres.app은 따로 켜지 않아도 돼요. (켜면 이미 실행 중으로 보여요.)

### 임시 주소가 바뀌었을 때

터널을 다시 켜면(재부팅·네트워크 끊김 등) `…trycloudflare.com` 주소가 바뀝니다. `.env`와 서버는 자동으로 맞춰지지만 아래 두 가지는 직접 바꿔야 해요.

1. **슬랙 앱 로그인 주소**: api.slack.com/apps → 회의실봇 → OAuth & Permissions → Redirect URLs 에 `새주소/auth/slack/callback` 추가 → Save URLs
   (안 하면 휴대폰 QR 체크인의 Slack 로그인이 실패해요)
2. **QR**: `.venv/bin/python -m roombot.cli qr` 로 다시 만들기 — 그래서 **인쇄는 고정 주소를 정한 뒤에** 하세요.

## 실제 다우오피스 연동 시 확인할 것

- `daou` 스키마를 읽는 부분(`roombot/rooms.py`, `roombot/checkin.py`의 예약 조회)을 다우오피스 API/DB 조회로 교체
- **노쇼 자동취소를 다우오피스에 반영하는 API**가 있는지 확인 필요. 지금은 `bot.noshow_cancellations`에만 기록하고, `synced_to_daou_at` 컬럼을 연동용으로 비워 둠
- 다우오피스 직원 이메일과 슬랙 프로필 이메일이 같은지 확인

## 보안 메모

- QR 토큰이 들어간 `qr/index.html`은 외부에 공유하지 마세요. 토큰을 바꾸면 예전 QR은 무효가 됩니다.
- QR 사진을 찍어 원격으로 체크인하는 것을 막으려면 `CHECKIN_ALLOWED_CIDRS`로 회사 네트워크만 허용하세요.
- 노쇼 규칙은 시스템을 처음 켠 시각 이후 시작하는 회의부터 적용됩니다 (`bot.system_settings.noshow_enforced_from`).
