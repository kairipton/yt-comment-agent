# chrome — 프로필 크롬 (찾기 · 창 열기 · 로그인 확인)

게시에 쓰는 크롬 프로필(별도 user-data-dir 폴더)을 다룬다. 크롬 실행 파일을 찾고, 프로필 창을 열고,
그 프로필이 **지금 유튜브에 로그인되어 있는지** 확인한다 [사용자].
로그인 여부의 기준은 이 확인 결과 하나다 — `config.yaml`에는 로그인 표시를 두지 않는다 [사용자].

## 구성요소

```python
def chrome_candidates() -> list[Path]                       # 크롬이 흔히 깔리는 자리 셋(Program Files · (x86) · 사용자 AppData)
def find_chrome(candidates: list[Path]) -> Path | None      # 후보 중 실제로 있는 첫 크롬
def profile_args(profile: Profile) -> list[str]             # 프로필 크롬 인수: --user-data-dir=<프로필 폴더> https://www.youtube.com
def open_profile_window(profile: Profile) -> None           # 그 프로필 폴더로 크롬 창을 연다(로그인용). 크롬이 없으면 LookupError
def login_state(profile: Profile, now: datetime) -> LoginState       # 그 프로필의 유튜브 로그인 상태(아래)
def login_states(profiles: tuple[Profile, ...], now: datetime) -> dict[str, LoginState]  # 이름 → 상태
def logged_in_cookies(rows: list[tuple[str, str, int]], now: datetime) -> bool  # 쿠키 줄(호스트 · 이름 · 만료)로 판정 — 읽기와 나눈 순수 함수
```

## 동작

### 로그인 확인 — 크롬 쿠키 파일 읽기

- 프로필 폴더의 쿠키 파일(`Default/Network/Cookies`, 옛 크롬은 `Default/Cookies`)을 **읽기 전용**으로 열어 `cookies` 표에서 호스트가 `youtube.com`으로 끝나는 `LOGIN_INFO` · `SID` 쿠키를 찾는다. 값(암호화됨)은 읽지 않고 이름 · 호스트 · 만료 시각만 본다.
- 하나라도 있고 만료되지 않았으면(만료 0 = 세션 쿠키도 유효) **로그인되어 있음**. 만료 시각은 크롬 시각(1601-01-01부터 마이크로초)이다.
- 상태는 셋이다(`LoginState`):

| 상태 | 언제 | 화면 |
|---|---|---|
| `LOGGED_IN` | 로그인 쿠키가 있다 | 로그인되어 있음 |
| `NEEDS_LOGIN` | 쿠키가 없다 · 프로필 폴더나 쿠키 파일이 없다 | 로그인 필요 |
| `WINDOW_OPEN` | 쿠키 파일을 열 수 없다 — 그 프로필의 크롬 창이 열려 있으면 크롬이 파일을 잠근다(이 PC에서 실측) | 크롬 창 열림 — 닫으면 확인 |

- 한계: 다른 기기에서 강제로 로그아웃시킨 경우처럼 쿠키는 남았는데 실제로는 풀린 상태는 잡지 못한다 — 그때는 게시 로봇이 크롬을 연 뒤 「로그인되어 있지 않아요」로 알린다(poster).
- 한 번 확인에 1초 안쪽(프로필 셋). 대시보드가 몇 초마다 묻는다(dashboard 명세).

### 창 열기

- `open_profile_window`는 `find_chrome(chrome_candidates())`의 크롬으로 `profile_args`를 붙여 띄우고 기다리지 않는다.

## 정체성

- 만료되지 않은 유튜브 로그인 쿠키가 있으면 로그인되어 있다 — `기능_로그인확인_유튜브_로그인쿠키가_있으면_로그인됨이다`
- 만료된 쿠키 · 다른 사이트 쿠키만 있으면 로그인 필요다 — `경계_로그인확인_만료된_쿠키는_로그인이_아니다`
- 프로필 폴더가 없으면 로그인 필요다 — `기능_로그인확인_프로필폴더가_없으면_로그인필요다`
- 쿠키 파일을 열 수 없으면 크롬 창 열림이다 — `기능_로그인확인_파일을_못열면_창열림이다`
- 프로필 크롬은 그 프로필 폴더로 유튜브를 연다 — `기능_크롬_프로필창은_그_폴더로_유튜브를_연다`
- 크롬을 못 찾으면 None — `기능_크롬_후보에_없으면_None이다`

## 테스트 방법

- 쿠키 판정은 순수 함수(`logged_in_cookies`)로, 파일 읽기는 임시 폴더에 만든 가짜 쿠키 파일(SQLite)로 본다. 잠긴 파일은 읽기를 바꿔 끼워 흉내 낸다.
- 실제 크롬 창 열기와, 창이 열린 동안 파일이 잠기는 것은 수동 확인.

## 의존

- 의존하는 것: `models`, 표준 `sqlite3`.
- 이것에 의존하는 것: 서버(`app.py`) · `poster`(게시 검사) · `launcher`(크롬 찾기).

## 미정 사항

- 없음.
