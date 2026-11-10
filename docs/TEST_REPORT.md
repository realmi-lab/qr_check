# 검증 기록

[README](../README.md) · [문서 목차](README.md)

## 2026-11-10 코드 검증

| 항목 | 확인한 결과 |
|---|---|
| 기준 커밋 | `daf7bec` · 공통 .env·Slack 연결 정리 |
| 로컬 핵심 검사 | 28개 통과 |
| 범위 | 예약 충돌·권한·체크인 경계·노쇼 경쟁·저장·백업·마이그레이션·HTTP·.env 로딩 |
| GitHub CI | [Linux 설치·검사 통과](https://github.com/realmi-lab/slack_qr_check/actions/runs/42729298091) |
| 실제 Slack | 이 검사에서는 연결·메시지 전송을 하지 않음 |

```sh
uv run --locked python -m pytest -q
```

테스트는 임시 또는 메모리 SQLite와 가상 Slack 값을 사용합니다. 결과는 실제 워크스페이스의 토큰·권한·이벤트 수신·모바일 화면 검증과 구분합니다. 테스트 개수는 서비스의 전체 정확도를 나타내지 않습니다.

## 문서·설치 명세 정리

README를 소개·기능·아키텍처·기술 역할·실행 입구·구조·검증으로 정리하고 상세 사용법을 USAGE.md로 이동했습니다. 요구사항과 의존성 명세는 [REQUIREMENTS.md](REQUIREMENTS.md)에 안내합니다. 문서의 상대 링크·경로·제목 연결을 확인하며, 문서 수정만으로 전체 기능 검사를 반복하지 않습니다.

Python 3.12의 빈 임시 가상환경에 requirements.txt만으로 실행 의존성 25개를 설치한 뒤 backend.main·backend.cli·frontend.qr import가 성공했습니다. 개발 의존성이나 빌드 파일 없이 확인했습니다.
