# store — 기록장

SQLite 파일 하나에 **게시 시도**와 **초안**을 저장하고 조회한다. 판정은 하지 않는다 — 무엇이 예외인지·몇 개가 상한인지는 부르는 쪽(guard)이 정해 넘긴다.
서버와 게시 로봇(별도 프로세스, D-06)이 같은 파일을 함께 쓴다.

## 구성요소

```python
class DuplicatePostError(Exception)         # 영상당 1개를 DB가 거부했을 때(마지막 방어선)
class Store:
    def __init__(self, db_path: Path, test_ids: set[str])                    # 파일·표가 없으면 만든다. test_ids는 예외 검증용
    def start_attempt(self, video: VideoItem, profile: str, text: str, now: datetime,
                      *, exempt: bool, retry_no: int = 0, root_id: int | None = None) -> int  # 시도 한 줄 생성(상태 QUEUED)
    def claim(self, attempt_id: int, now: datetime) -> bool                 # QUEUED인 시도만 OPENING으로(원자적). 이미 잡혔으면 False
    def latest_in_root(self, root_id: int) -> Attempt                       # ③ 한 번 누름의 가장 최근 줄(재시도 포함)
    def fail_unfinished(self, except_id: int, now: datetime, reason: str) -> list[int]  # 죽은 로봇이 남긴 진행 중 줄을 실패로 끝낸다
    def fail_stopped(self, attempt_id: int, now: datetime, reason: str) -> PostStatus | None  # 멈춘 줄 하나를 실패로(버튼 기록으로 전/후). 끝난 줄이면 None
    def fail_queued(self, now: datetime, reason: str) -> list[int]          # 남은 대기 줄(QUEUED)을 버튼 전 실패로 끝낸다(서버가 시작할 때)
    def set_status(self, attempt_id: int, status: PostStatus, now: datetime, *, reason: str | None = None,
                   screenshot: Path | None = None, comment_url: str | None = None) -> None  # 진행 상태 갱신
    def set_typed(self, attempt_id: int, count: int, now: datetime) -> None  # 크롬에 친 글자 수만 적는다. 끝난 줄은 건드리지 않는다
    def posted_attempt(self, video_id: str) -> Attempt | None              # 그 영상의 영상당 1개 열이 채워진 줄(먼저 단 게시)
    def get(self, attempt_id: int) -> Attempt
    def history(self, limit: int = 100) -> list[Attempt]                    # 최근 것이 앞
    def submitted_count(self, since: datetime, until: datetime) -> int     # 그 구간에 게시 버튼을 누른 시도 수
    def has_posted(self, video_id: str) -> bool                            # 예외가 아닌 게시 기록이 있나
    def reset_quota(self, now: datetime) -> None                           # 하루 상한 초기화 시각을 남긴다(기록은 지우지 않는다)
    def last_quota_reset(self) -> datetime | None                          # 마지막 초기화 시각(없으면 None)
    def save_draft(self, draft: Draft, now: datetime) -> None              # 영상당 최신 초안 하나를 덮어쓴다
    def get_draft(self, video_id: str) -> Draft | None
    def comparable_texts(self, exclude_video_id: str) -> list[str]         # 「영상마다 다르게」 비교 대상
```

## 동작

### 시도의 일생 (poster 명세)

- 새 시도는 `QUEUED`(대기). 게시 로봇이 `claim`으로 잡아야 `OPENING`이 된다 — `UPDATE … WHERE status='queued'` 한 문장이라 둘이 동시에 잡아도 한쪽만 성공한다.
- `root_id`: 첫 시도는 자기 id, 재시도 줄은 첫 시도의 id. 화면은 첫 id로 묻고 `latest_in_root`가 가장 최근 줄을 준다.
- **끝난 상태**(`PostStatus.finished` — 완료 · 버튼 전/후 실패 · 거부)인 줄은 상태를 다시 바꾸지 않는다(`ValueError`) — 완료 위에 거부가 덮이는 일을 막는다.
- `fail_unfinished`: `except_id`가 아닌 진행 중 줄(`OPENING` · `TYPING` · `SUBMITTED` · `CONFIRMING`)을 「이유」로 끝낸다. `submitted_at`이 있으면 버튼 후 실패, 없으면 버튼 전 실패. 게시 로봇이 잠금을 잡은 뒤에만 부른다(그때 진행 중인 다른 줄은 죽은 프로세스의 것이다).

### 표 두 개

| 표 | 한 줄 = | 주요 열 |
|---|---|---|
| `attempts` | 게시 시도 하나. **재시도도 새 줄**이다 | 시각 · 영상(id · 제목 · 채널 · 출처) · 프로필 · 댓글 · 상태 · 실패 이유 · 캡처 경로 · 댓글 링크 · `retry_no` · `exempt` · `submitted_at` · `unique_video` · `root_id` · `typed_chars`(친 글자 수, 처음 0) |
| `drafts` | 영상 하나의 최신 초안 | 영상 id(기본 키) · 고민 한 줄 · 댓글 · 몇 번째 · 불합격 이유(`issues`) · 읽은 자막 글자 수(`transcript_chars`) · 읽은 인기 댓글 수(`comments_read`) · 시각. (옛 `approved` 열은 쓰지 않는다 — 옛 기록장 파일과 맞추려 남겨 두고 늘 0) |

- 영상 설명(`VideoItem.description`)과 조회수(`view_count`)는 저장하지 않는다 — 기록에서 꺼낸 영상의 설명은 빈 문자열, 조회수는 `None`이다.
- **옛 기록장 파일:** 열 때 없는 열(`attempts.typed_chars` · `drafts.transcript_chars` · `drafts.comments_read`)만 `ALTER TABLE ADD COLUMN`으로 더한다. 서버와 게시 로봇이 동시에 열어 한쪽이 먼저 더했으면 「이미 있는 열」 오류만 넘긴다. 옛 초안의 두 열은 `NULL` → `None`(「자막 0자」와 다르다).
- `set_typed`는 상태를 바꾸지 않고 `typed_chars`만 적는다. 끝난 줄이면 아무것도 쓰지 않는다.
- `posted_attempt`는 `unique_video`가 그 영상 id로 채워진 줄을 돌려준다 — 예외(dev_mode 테스트 영상) 줄은 비어 있어 나오지 않는다. 버튼 후 확인 실패 줄이면 댓글 링크가 없을 수 있다.

### 영상당 1개 — 유니크 열 (D-05)

- `unique_video` 열에 유니크 인덱스를 건다. SQLite는 `NULL`을 여러 개 허용한다.
- 상태가 **처음으로 「게시 버튼을 누른」 상태**(`PostStatus.pressed_submit`)가 될 때 `submitted_at`을 적고,
  그 줄이 `exempt`가 아니면 `unique_video`에 영상 id를 채운다. 같은 영상의 다른 줄이 이미 채워 두었으면 `DuplicatePostError`.
- 그래서 버튼 전에 실패한 줄 · 재시도 줄 · 예외(dev_mode의 테스트 영상) 줄은 서로 부딪치지 않는다(D-12 · D-17 · D-23).
- `has_posted`는 `unique_video`가 채워진 줄이 있는지만 본다. dev_mode를 켜고 단 테스트 영상 게시는 **나중에 dev_mode를 꺼도** 세지 않는다 [사용자] —
  그래서 시연 때(dev_mode 끔) 테스트 영상의 첫 게시는 통과하고 두 번째가 막힌다.
- **게시 자리를 먼저 잡는다:** 게시 로봇은 실제 게시 버튼을 누르기 **직전에** 상태를 `SUBMITTED`로 바꾼다. 여기서 `DuplicatePostError`가 나면 버튼을 누르지 않고 `REJECTED`로 기록한다 —
  DB가 거부한 게시가 화면에서는 이미 달려 버리는 일이 없게 한다.
- **예외 검증:** `start_attempt(exempt=True)`는 영상 id가 `test_ids`에 있을 때만 받는다. 아니면 `ValueError` —
  판정 쪽 실수로 지정 채널 영상이 예외로 와도 저장에서 막는다(D-05의 「마지막 방어선」). dev_mode를 볼지는 여전히 부르는 쪽 몫이다.
- 테스트 영상이면 `dev_mode`가 꺼져 있어도 `exempt=True`를 받는다 — 기록장은 `dev_mode`를 모른다. 지금 `dev_mode`로 다시 보는 것은 게시 로봇의 `preflight`(`guard.check_exempt`)다.
- 「버튼 누름」을 건너뛰고 확인 중·완료·버튼 후 실패로 바로 가도 처음 그 상태가 될 때 똑같이 `submitted_at` · `unique_video`를 채운다.
- 거부(`DuplicatePostError`)는 삼키지 않는다 — 부르는 쪽까지 올라가 실패로 기록된다.

### 하루 수 세기 (D-13)

- `submitted_count(since, until)`는 `submitted_at`이 그 구간에 든 줄 수다. 예외 줄도 센다(하루 상한은 테스트 영상에도 적용, D-17).
- 세는 구간은 `guard.quota_window(now, last_quota_reset())`가 계산해 넘긴다. 기록장은 시간대를 모른다.
- **초기화 [사용자]:** `reset_quota`는 `quota_resets` 표에 시각 한 줄을 더할 뿐이다 — 게시 기록(attempts)은 지우지 않아 기록 표 · 영상당 1개는 그대로다. 개발 모드인지는 부르는 쪽(서버)이 본다.

### 시각

- 시각은 **시간대가 붙은 값**만 받는다. 없으면 `ValueError` — 다른 시간대가 섞여 하루 경계가 어긋나는 것을 막는다.
- 저장은 UTC ISO 문자열, 꺼낼 때 시간대가 붙은 값으로 되돌린다.

### 함께 쓰기

- 연결마다 WAL 모드와 바쁨 대기(5초)를 켠다. 쓰기는 짧은 트랜잭션 하나로 끝낸다.
- 「끝났나 확인 → 바꾸기」는 `BEGIN IMMEDIATE`로 쓰기 잠금을 먼저 잡고 한다 — 서버와 로봇이 동시에 같은 줄을 바꿔도 끝난 줄이 덮이지 않게.

### 「영상마다 다르게」 비교 대상

- `comparable_texts(v)` = 영상 `v`가 **아닌** 다른 영상들의 「게시 버튼을 누른 댓글」 + 「규칙 검사를 통과한 최신 초안」. 불합격 초안은 넣지 않는다 — 게시될 수 없는 글 때문에 멀쩡한 초안이 막히는 꼬임을 막는다 [에이전트].
  같은 영상의 이전 글은 뺀다 — 테스트 영상에 같은 초안을 다시 다는 개발 흐름이 막히지 않게 [에이전트].

## 정체성

- 예외가 아닌 같은 영상에 게시 버튼을 두 번 누른 기록은 저장되지 않는다 — `기능_영상당1개_DB가_두번째_게시를_거부한다`
- 판정 쪽이 지정 채널 영상에 예외 표시를 잘못 붙여도 저장에서 막는다 — `예외_예외표시_테스트영상이_아니면_거부된다`
- 버튼 전 실패는 영상당 1개와 하루 상한 어디에도 세지 않는다 — `기능_영상당1개_DB는_버튼전_실패_뒤_재시도_줄을_받는다` · `기능_하루수_버튼전_실패와_거부는_세지_않는다`
- 하루 상한 수에는 예외(테스트 영상) 게시도 들어간다 — `기능_하루수_예외_게시도_센다`
- 초기화는 마지막 시각을 돌려주고 게시 기록은 지우지 않는다 — `기능_초기화_마지막_시각을_돌려주고_기록은_남긴다`
- dev_mode로 단 테스트 영상 게시는 나중에도 영상당 1개에 세지 않는다 — `기능_게시여부_예외_게시는_세지_않는다`
- 끝난 시도의 상태는 다시 바뀌지 않는다 — `예외_기록장_끝난_시도는_상태를_바꾸지_않는다`
- 같은 시도는 한 번만 잡힌다 — `기능_잡기_대기중인_시도만_한번_잡힌다`
- 남은 대기 줄은 버튼 전 실패로 끝나고, 진행 중 줄은 건드리지 않는다 — `기능_대기줄정리_대기줄만_실패로_끝낸다`
- 멈춘 줄 하나도 버튼 기록으로 전/후가 갈리고, 끝난 줄은 그대로다 — `기능_멈춘줄정리_한줄도_버튼기록에_따라_끝나고_끝난줄은_그대로다`
- 불합격 초안은 「영상마다 다르게」 비교 대상이 아니다 — `기능_비교대상_불합격_초안은_넣지_않는다`
- 죽은 로봇이 남긴 줄은 버튼 기록에 따라 실패로 끝난다 — `기능_멈춘줄정리_버튼기록에_따라_전후가_갈린다`
- 초안의 「AI가 읽은 양」을 저장하고 읽는다 — `기능_초안_읽은양을_저장하고_읽는다`
- 새 열이 없는 옛 기록장 파일도 열리고, 옛 초안의 읽은 양은 `None`이다 — `기능_기록장_옛파일에_새열을_더해_연다` · `경계_기록장_두번_열어도_열추가가_깨지지_않는다`
- 친 글자 수를 적고, 끝난 줄은 건드리지 않는다 — `기능_타이핑진행_친글자수를_적는다` · `경계_타이핑진행_끝난줄은_건드리지_않는다`
- 먼저 단 게시는 영상당 1개 열이 채워진 줄에서만 찾는다 — `기능_먼저단게시_영상당1개_줄을_찾는다` · `경계_먼저단게시_개발모드_예외줄은_찾지_않는다`

## 의존

- 의존하는 것: `models`, 표준 `sqlite3`.
- 이것에 의존하는 것: 서버 · `poster` · `auto` · `draft`(비교 대상 조회).

## 미정 사항

- 없음.
