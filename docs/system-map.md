# 시스템 지도

시스템 목록 · 의존 관계 · 구현 순서의 주인. 각 시스템의 규칙은 `docs/systems/<시스템>.md`가 진다.
명세 꼴은 `docs/SPEC-WRITE-GUIDE.md`를 따른다.

⚠ 명세는 **착수할 때** 쓴다. 아래 「명세」 칸이 비어 있으면 아직 착수하지 않은 시스템이다.

## 시스템

| 시스템 | 책임 | 의존 | 명세 |
|---|---|---|---|
| `models` | 부품들이 주고받는 공용 자료형(입출력 없음) | — | `docs/systems/models.md` |
| `config` | `config.yaml`·`.env`를 읽어 설정 객체로 준다 | `models` | `docs/systems/config.md` |
| `store` | SQLite에 게시 시도·초안을 저장하고 조회한다 | `models` | `docs/systems/store.md` |
| `guard` | 허용 범위·영상당 1개·하루 상한·댓글 검증을 **판정만** 한다(I/O 없음, 값은 인자로) | `models` | `docs/systems/guard.md` |
| `fetch` | YouTube에서 최신 영상(RSS)·테스트 영상·자막·인기 댓글을 가져온다 | `models` | `docs/systems/fetch.md` |
| `draft` | Claude로 「영상 속 고민 한 줄」과 댓글 초안을 만들고 문지기로 검사한다 | `models` · `guard` | `docs/systems/draft.md` |
| `assign` | 프로필 자동 배정 — 로그인된 프로필 중 가장 오래 안 쓴 것(결정론) | `models` | `docs/systems/assign.md` |
| `trend` | 뜨기 직전 계산 — 평균 속도 · 최근 속도 · 기준 판정(결정론) | `models` | `docs/systems/trend.md` |
| `auto` | 자동 모드 — 체크한 영상을 차례로 초안 → 게시(③과 같은 경로), 간격·상한 멈춤 | `models` · `guard` · `store` | `docs/systems/auto.md` |
| `poster` | 크롬 프로필을 열어 타이핑·게시·확인한다(별도 프로세스) | `config` · `store` · `guard` | `docs/systems/poster.md` |
| `dashboard` | 세 칸 화면. 위 시스템을 부르기만 하고 판정하지 않는다 | 전부 | `docs/systems/dashboard.md` |
| `chrome` | 프로필 크롬 — 크롬 찾기 · 프로필 창 열기 · 유튜브 로그인 상태 확인(쿠키 파일) | `models` | `docs/systems/chrome.md` |
| `launcher` | `run_dashboard.bat` 더블클릭 한 번으로 준비 · 서버 켜기 · 대시보드 열기 | `config` · `chrome` · `dashboard` | `docs/systems/launcher.md` |

파일 위치: 부품은 `agent/<시스템>.py`이고, `dashboard`는 서버 `app.py` + 화면 `web/`이다(D-02 · D-18).
결정론 부품(`models` · `guard` · `assign` · `trend`)은 `tests/test_architecture.py`의 검사를 통과해야 한다(D-19).

새 기능은 위 시스템의 확장이나 새 시스템으로 여기에 올린다.

## 의존 규칙

- `guard`는 아무 I/O도 부르지 않는다. 필요한 값(오늘 게시 수 · 현재 시각)은 부르는 쪽이 넘긴다.
- `dashboard`는 판정·저장 로직을 갖지 않는다. 화면에서 판정하면 테스트가 못 닿는다.
- 순환 의존을 만들지 않는다.

## 구현 순서

나중에 끼워 넣기 비싼 것부터: `models` → `config` → `store` → `guard` → `fetch` → `draft` → `poster` → `dashboard`.
