# trend — 뜨기 직전 계산

「연애」 검색으로 찾은 영상 중 **뜨기 직전**인 것을 가려내는 계산. 결정론 부품이다(D-19) — 조회수·시각·기준을 인자로 받는다.
방법은 D-24 [사용자]: 최근 48시간 · 평균 속도로 기준을 거르고, 선택 기능 「다시 측정」은 최근 속도로 정렬한다.

## 구성요소

```python
def views_per_hour(views: int, published_at: datetime, now: datetime) -> float          # 평균 속도 = 조회수 ÷ 올린 뒤 지난 시간
def recent_velocity(v1: int, t1: datetime, v2: int, t2: datetime) -> float              # 최근 속도 = 두 번 잰 조회수 차이 ÷ 간격(시간)
def is_rising(views: int, per_hour: float, published_at: datetime, now: datetime, rules: RisingRules) -> bool  # 뜨기 직전 기준에 드나
```

## 동작

- **평균 속도:** 지난 시간이 1시간보다 짧으면 1시간으로 본다 — 올린 지 몇 분 된 영상의 속도가 터무니없이 커지지 않게 [에이전트].
- **뜨기 직전**(config `rising`) — 셋 다 맞아야 참:
  - 올린 지 `published_within_hours`(48) 시간 안,
  - 조회수 `min_views`(1,000) 이상 `max_views`(5,000) 이하,
  - 평균 속도 `min_views_per_hour`(150) 이상.
- **최근 속도:** 두 번째 측정이 첫 번째보다 늦어야 한다(아니면 `ValueError`). 조회수가 줄었으면(집계 보정) 0으로 본다.
- 뜨기 직전 영상은 **게시하지 않는다** — 고정 규칙(D-22). 그 판정은 문지기 `check_target`(출처 `RISING`)이 한다. 여기는 고르기만.

## 정체성

- 기준(48시간 · 1,000~5,000 · 시간당 150)의 경계에서 정확히 갈린다 — `경계_뜨기직전_조회수와_속도의_끝값에서_갈린다` · `경계_뜨기직전_48시간을_넘으면_빠진다`
- 갓 올린 영상의 평균 속도는 1시간으로 나눈다 — `경계_평균속도_1시간보다_짧으면_1시간으로_본다`
- 최근 속도는 두 측정의 차이 ÷ 간격이다 — `기능_최근속도_두번_잰_차이를_간격으로_나눈다`
- 입출력·시각 읽기·다른 부품 import를 하지 않는다 — 구조 검사(`tests/test_architecture.py`)

## 의존

- 의존하는 것: `models`만.
- 이것에 의존하는 것: 서버(`app.py`).

## 미정 사항

- 없음.
