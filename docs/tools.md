# 도구 목록

훅·에이전트·커맨드가 **무엇이 있는가**만 한 줄씩 둔다. 무엇을 왜 보는지는 각 파일의 머리 주석이 주인이다.
쓰는 쪽은 기록 담당 에이전트(`record`)다. 등록은 `.claude/settings.json`.

## 훅

| 파일 | 자리 | 한 줄 |
|---|---|---|
| `.claude/hooks/run_tests.py` | PostToolUse(Write·Edit·MultiEdit) | `.py`·`pyproject.toml`이 바뀌면 pytest. 실패·0개 실행·수 감소를 되먹인다. 맨 바깥 `def test_`만 이름 실수로 경고(들여 쓴 메서드는 제외). 테스트를 먼저 쓰는 단계의 빨간불은 의도된 것이다 |
| `.claude/hooks/check_identity_anchors.py` | Stop | 명세 정체성 절이 부른 테스트 이름이 `tests/`에 있는지 대조한다 |

## 에이전트

| 파일 | 한 줄 |
|---|---|
| `.claude/agents/intent.md` | 의도 대조. 작업 전 브리핑 · 작업 후 diff 대조. 읽기 전용 |
| `.claude/agents/record.md` | 기록 담당. PROGRESS · DECISIONS · tools를 갱신한다 |

## 커맨드

| 파일 | 한 줄 |
|---|---|
| `.claude/commands/record.md` | `/record` — 근거를 모아 기록 담당 에이전트를 태우고 결과를 검증한다 |

## 실행 설정

| 파일 | 한 줄 |
|---|---|
| `.claude/launch.json` | 앱 미리보기 실행 설정. `dashboard` = `uv run uvicorn app:app --port 8501 --reload`(FastAPI 서버, 포트 8501) |
| `run_dashboard.bat` | 원클릭 실행기. 크롬 확인 → uv 준비 → `uv sync` → `python -m agent.launcher`(서버 켜기 · 대시보드 열기). 명세 `docs/systems/launcher.md` |

## 참고 자료 (비공개 아티팩트 링크 — 만료될 수 있음)

| 자료 | 한 줄 |
|---|---|
| https://claude.ai/artifact/3EFPwahXSg9M7fMKMKRpNe | 앱 작동 지도(흐름도 + 패키지 설명) |
| https://claude.ai/artifact/YMXtJgPJ376oLyVFv3URWn | 화면 시안 캔버스(A~D + B 다듬은 판 B-1~B-6). 채택안은 D-50 |
