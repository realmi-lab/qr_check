# 작업 기준

README.md를 먼저 읽는다. frontend/는 화면·이미지·QR 출력, backend/는 이벤트·API·업무 규칙·저장이다. 전체 원문을 다른 틀로 재작성하지 말고 필요한 함수를 직접 수정한다.

- 시작: `uv run --locked python -m backend.main`
- 설정: 루트 .env. 실제 키·직원·DB·로그는 커밋하지 않는다.
- 데이터 경로·테이블·쿠키·QR 비밀값을 이름 변경 때문에 바꾸지 않는다.
- 변경 후 `uv run --locked python -m pytest -q` 한 번. 실패나 추가 수정이 없으면 반복하지 않는다. 테스트는 예약·권한·체크인·노쇼·저장처럼 실제 위험이 있는 동작만 추가한다.
- 실제 Slack 메시지를 보내는 검증은 테스트 워크스페이스 사용을 명시적으로 승인받았을 때만 한다.
- 원격 게시까지 요청된 변경은 main에 올리고 상태를 확인한다.

Slack 공통 설정은 docs/SLACK_SETUP.md를 따른다. 공통 키는 SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_NOTIFY_CHANNEL, APP_TIMEZONE, SQLITE_PATH다. 설정은 backend/config, Slack 접속은 backend/slack에서 수정한다. 새 기능 때문에 공통 설정 이름을 다르게 만들지 않는다.

README는 code_management의 소개 → 주요 기능 → 아키텍처 → 기술 스택 → 처음 실행하기 → 프로젝트 구조 → 검증 구성을 따른다. 초보자에게 도구 이름보다 역할을 설명한다. 상세 사용법은 docs/USAGE.md, 검증 근거는 docs/TEST_REPORT.md, 문서 목차는 docs/README.md에 둔다. 문서만 바꿀 때는 링크·경로·diff를 확인하고 전체 기능 검사를 반복하지 않는다.

의존성은 pyproject.toml·uv.lock을 기준으로 하고 pip 설치용 requirements.txt를 같은 잠금에서 export한다. 빌드 파일·.venv·실행 DB를 배포 명세에 넣지 않는다.
