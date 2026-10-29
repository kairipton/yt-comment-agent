# fetch — 수집 담당

유튜브에서 ① 목록(지정 채널 최신 영상 · 테스트 영상)과 ② 초안 재료(자막 · 설명 · 인기 댓글)를 가져온다.
판정하지 않는다 — 가져온 것을 `models`의 자료형으로 돌려줄 뿐이다.
**YouTube API 키는 없어도 된다(D-31).** 키가 없으면 인기 댓글을 빼고, 테스트 영상 정보를 줄여서 가져온다.

## 구성요소

```python
class FetchError(Exception)                                              # 가져오기 실패. 무엇을 가져오다 실패했는지 담는다
class YouTubeClient:
    def __init__(self, api_key: str | None, timeout: float)             # api_key가 없으면 키 없는 경로만 쓴다
    def latest_videos(self, channels: list[Channel], per_channel: int) -> VideoList          # ① 채널별 최신 N개 + 실패한 채널
@dataclass(frozen=True)
class VideoList:
    videos: list[VideoItem]
    failures: dict[str, str]                                             # 채널 이름 → 실패 이유
    def test_videos(self, ids: list[str]) -> VideoList                 # ① 테스트 영상(일부 공개) + 못 가져온 영상 = videos_by_id(ids, TEST)
    def videos_by_id(self, ids: list[str], source: Source) -> VideoList  # id로 영상 가져오기 — 테스트 영상 · 링크 입력이 함께 쓴다
    def search_recent(self, keyword: str, published_after: datetime) -> list[str]   # 뜨기 직전 후보 검색 — 영상 id 최대 50개
    def measure(self, ids: list[str], now: datetime) -> list[Measured]             # 영상 정보 + 조회수를 잰다(출처 RISING)
    def context(self, video: VideoItem) -> VideoContext                 # ② 초안 재료
```

`VideoItem`에 `description`(설명, 없으면 빈 문자열) 칸을 더한다 — 목록을 가져올 때 이미 받은 설명을 재료로 다시 쓰려고(models 명세도 함께 고친다).

## 동작

### 목록 — `latest_videos`

- 채널마다 **RSS 피드**(`https://www.youtube.com/feeds/videos.xml?channel_id=<id>`)를 읽는다. 키가 필요 없고, 할당량을 쓰지 않는다.
- 피드의 영상 중 올린 날짜가 최신인 것부터 `per_channel`개(D-11). 피드에서 꺼내는 것: id · 제목 · 채널 id·이름 · 올린 날짜 · 썸네일 · 설명 · 조회수(`media:community/media:statistics`의 `views`, 없으면 `None`).
- 출처는 `Source.CHANNEL`. 채널 순서는 config 순서를 따르고, 채널 안에서는 최신이 앞.
- 올린 날짜는 **시간대가 붙은 값**으로 만든다(기록장이 시간대 없는 값을 거부한다).
- 피드에 있는 영상을 가리지 않는다 — 쇼츠·예정된 라이브도 채널 피드에 있으면 넣는다 [사용자].
- **RSS가 실패하면 한 번 더 읽는다.** 피드가 같은 채널에서도 200과 404·500을 오락가락한다(실측) — 한 번 더 읽어 대부분 넘긴다.
- **그래도 실패하고 YouTube 키가 있으면 API로 대신 가져온다 [사용자].** 채널의 업로드 재생목록(채널 id `UC…` → `UU…`)을 `playlistItems.list`(part=contentDetails, 최대 `per_channel`개, 할당량 1)로 읽어 영상 id를 얻고,
  `videos_by_id(ids, Source.CHANNEL)`(할당량 1)로 영상 정보를 채운다 — 채널당 할당량 2. 출처·채널 id는 RSS와 같아 허용 범위 판정이 그대로다. 키가 없으면 대체하지 않는다.
- 대체까지 실패하면 두 이유를 함께 담는다(「RSS 이유 · API 대체도 실패: 이유」).
- **한 채널이 실패해도 나머지는 보여 준다.** 실패한 채널은 `VideoList.failures`에 이유와 함께 담는다 [에이전트].
  화면이 「외롭여 채널을 못 불러왔어요」처럼 알린다. 모든 채널이 실패해도 예외가 아니라 빈 목록 + 실패 목록이다.

### 테스트 영상 — `test_videos`

- 일부 공개 영상은 RSS에 나오지 않는다. 그래서 id로 직접 가져온다.
  - **키가 있으면** YouTube Data API `videos.list`(part=snippet,statistics) — 제목 · 채널 · 올린 날짜 · 썸네일 · 설명 · 조회수를 받는다. 할당량 1(statistics를 붙여도 같다). 조회수가 비공개인 영상도 목록에서 빼지 않는다 — 조회수만 `None`(뜨기 직전 `measure`의 건너뛰기와 다르다).
  - **키가 없으면** oEmbed(`https://www.youtube.com/oembed?url=…`) — 제목 · 채널 이름 · 썸네일만. 올린 날짜·조회수는 없음(None), 설명은 빈 문자열.
    채널 id는 **빈 문자열**로 둔다 — config의 채널 id로 추정해 채우지 않는다(허용 범위 우회 통로가 된다). 테스트 영상은 id로 허용된다(guard).
- 출처는 `Source.TEST`.
- 확인한 것: 테스트 영상 `z_K3rHRbqck`는 키 없이 oEmbed와 한국어 자동 자막(약 250자)이 받아진다.

### 링크로 가져오기 — `videos_by_id`

- 테스트 영상과 같은 길이다: 키가 있으면 `videos.list`, 없으면 oEmbed. 출처만 부르는 쪽이 정한다(`Source.LINK`).
- **키가 없으면 채널 id를 비운다**(테스트 영상과 같은 이유). 그래서 키 없이 들어온 링크는 허용 채널인지 확인할 수 없어 게시 대상이 아니다 — 초안은 쓸 수 있다.

### 뜨기 직전 후보 — `search_recent` · `measure`

- **YouTube 키가 있어야 한다** — 키 없이 검색할 길이 없다. 없으면 `FetchError`(「뜨기 직전 영상 찾기에는 YouTube 키가 필요해요」).
- `search_recent`: `search.list`(q=키워드 · type=video · publishedAfter · regionCode=KR · relevanceLanguage=ko · **order=date(최신순)** · 최대 50개). **할당량 100**(하루 무료 10,000 — 하루 100번쯤).
  실제로 재 본 결과 [에이전트]: 조회수 순은 50개 모두 4만 뷰 이상이라 기준(1,000~5,000)에 0개, 최신순은 50개 중 22개가 1,000~5,000이고 20개가 기준 통과.
- `measure`: `videos.list`(part=snippet,statistics, 50개씩) — 할당량 1. `Measured(video, views, measured_at)`로 돌려준다. 출처는 `Source.RISING`.
- 판정(뜨기 직전인가)은 하지 않는다 — `trend`가 한다.

### 초안 재료 — `context`

| 재료 | 어떻게 | 없으면 |
|---|---|---|
| 자막 | `youtube-transcript-api`로 한국어 자막(사람이 단 것 우선, 없으면 자동 생성)을 이어 붙인다 | `None` — 작가가 제목·설명으로 대신한다 |
| 설명 | 따로 가져오지 않는다 — `video.description`이 그것이다 | 빈 문자열 |
| 인기 댓글 | **키가 있을 때만** `commentThreads.list`(order=relevance, 최대 20개)의 본문. 할당량 1 | 빈 목록 — 화면에 「인기 댓글 없음(YouTube 키 없음)」을 표시한다 [사용자] |

- 자막은 **자르지 않는다.** 지정 채널 영상 하나가 약 1만 4천 자였다 — 모델 입력 한도에 한참 못 미친다.
- 자막을 못 받은 이유(자막 없음 · 막힘)는 기록만 하고 오류로 올리지 않는다 — 작가가 제목·설명으로 대신 쓴다.
- 인기 댓글 API가 실패하면(댓글 꺼짐 등) 빈 목록으로 두고 이유를 기록한다.

### 실패 처리

- 네트워크·응답 형식 오류는 `FetchError`로 드러낸다(삼키지 않는다). 예외: 위 표의 「없으면」 경로와 채널별 실패.
- 요청마다 `timeout`(초)을 건다. 값은 config `fetch.timeout_seconds`(새 키, 코드 기본값 없음). 자막 라이브러리도 시간 한도를 건 세션으로 부른다 — 기본은 한도가 없어 응답이 멎으면 무한히 기다린다.
- API 호출의 오류는 응답 오류(HttpError)뿐 아니라 httplib2 오류·연결 문제(OSError)까지 같은 길로 다룬다.
- 키 값은 오류 문구·기록에 넣지 않는다. Google API 라이브러리의 오류 문구에는 요청 주소(`key=…`)가 실릴 수 있어,
  그 오류를 그대로 올리지 않고 **상태 코드와 무엇을 하다 실패했는지만** 담은 `FetchError`로 바꾼다.

## 정체성

- YouTube API 키가 없어도 목록과 자막·설명은 가져오고, 인기 댓글은 API를 부르지 않고 비운다(D-31) — `기능_재료_키가없으면_인기댓글은_빈목록이다`
- 자막이 없어도 재료 가져오기는 실패하지 않는다 — 제목·설명으로 대신할 수 있게 `None`을 돌려준다 — `기능_재료_자막을_못받아도_None으로_돌려준다`
- 채널 하나가 실패해도 나머지 채널의 목록은 나온다 — `기능_목록_채널하나가_실패해도_나머지는_나온다`
- 키 없이 만든 테스트 영상의 채널 id는 추정해 채우지 않는다 — `기능_테스트영상_키없으면_oEmbed로_채널id를_비운다`
- 키 값은 실패 이유에 새어 나가지 않는다 — `기능_오류문구에_키값이_없다`
- 연결 문제(시간 초과·연결 끊김)도 서버 오류로 터지지 않는다 — 목록은 「연결 문제」 이유로, 자막·인기 댓글은 없는 재료로 대신한다 — `기능_목록_연결이_끊기면_이유를_담아_알린다` · `기능_재료_연결이_끊겨도_초안재료는_대신한다`
- 검색 결과는 id만, 측정은 조회수와 출처 RISING을 담는다 — `기능_뜨기직전_검색은_id를_측정은_조회수를_읽는다`
- 키가 없으면 검색하지 않고 이유를 알린다 — `예외_뜨기직전_키가_없으면_검색하지_않는다`
- 링크로 가져온 영상은 출처가 「링크」이고 키가 있으면 진짜 채널 id를 담는다 — `기능_링크영상_출처는_링크이고_채널id를_담는다`
- RSS가 한 번 실패해도 다시 읽어 목록을 낸다 — `기능_목록_RSS가_한번_실패하면_다시_읽는다`
- RSS가 계속 실패하면 키로 업로드 목록에서 가져오고, 대체도 실패하면 두 이유를 함께 알린다 — `기능_목록_RSS가_계속_실패하면_업로드목록으로_대신한다` · `예외_목록_대체도_실패하면_두_이유를_함께_알린다`
- 업로드 목록 응답에서 영상 id만 꺼낸다 — `기능_목록_업로드목록에서_영상id를_꺼낸다`
- 목록은 조회수를 담고, 조회수가 없는 영상도 빠지지 않는다 — `기능_목록_RSS에서_조회수를_읽는다` · `기능_테스트영상_키있으면_조회수까지_읽는다` · `경계_테스트영상_조회수가_없어도_목록에서_빠지지_않는다`

## 테스트 방법

- 단위 테스트는 네트워크를 쓰지 않는다(TESTING.md). RSS·API 응답을 **저장해 둔 표본**으로 읽는 부분을 검사한다 — 「받아 오기」와 「읽어 풀기」를 함수로 나눈다.
- 실제 유튜브에서 받아 오는지는 수동 확인(지정 채널 영상 · 테스트 영상).

## 의존

- 의존하는 것: `models`, `youtube-transcript-api`, `google-api-python-client`(키가 있을 때), 표준 `urllib`·`xml`.
- 이것에 의존하는 것: 서버 · `auto`.

## 미정 사항

- 없음.
