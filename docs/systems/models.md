# models — 공용 자료형

여러 부품이 주고받는 값의 모양만 정한다. 입출력·판정이 없는 결정론 부품이다(D-19).
값은 전부 바꿀 수 없는 자료형(`frozen dataclass` · `Enum`)이다.

## 구성요소

| 타입 | 필드 · 값 | 뜻 |
|---|---|---|
| `Source` | `CHANNEL` · `TEST` · `LINK` · `RISING` | 영상이 어디서 왔나 — 지정 채널 · 테스트 영상 · 링크 입력 · 뜨기 직전 |
| `VideoItem` | `id` · `title` · `channel_id` · `channel_name` · `published_at` · `thumbnail_url` · `source` · `description`(없으면 빈 문자열) · `view_count`(없으면 `None`) | 목록 한 줄. 설명은 목록을 가져올 때 받아 두어 초안 재료로 다시 쓴다(fetch). 조회수는 RSS·videos.list가 줄 때만 있고, oEmbed와 기록장에서 꺼낸 영상은 `None` |
| `VideoContext` | `video` · `transcript`(없으면 `None`) · `top_comments` | 초안 재료. 설명은 `video.description` 한 곳에만 둔다 |
| `Draft` | `video_id` · `worry` · `comment` · `version` · `issues` · `transcript_chars` · `comments_read` | 고민 한 줄과 댓글 초안. `version`은 **몇 번째 초안**(사용자가 다시 쓰기를 누른 횟수 + 1). `issues`는 문지기 불합격 이유(합격이면 빈 목록). 「AI가 읽은 양」: `transcript_chars`는 읽은 자막 글자 수(0 = 자막 없음), `comments_read`는 읽은 인기 댓글 수(`None` = YouTube 키 없음). 둘 다 `None`이면 기록이 없는 옛 초안 |
| `Verdict` | `ok` · `reasons` | 판정 결과. 불합격이면 이유를 **전부** 담는다 |
| `PostStatus` | 아래 표 | 게시 시도의 진행 상태 |
| `Attempt` | `id` · `created_at` · `video`(`VideoItem`) · `profile` · `text` · `status` · `updated_at` · `reason` · `screenshot` · `comment_url` · `retry_no` · `exempt` · `submitted_at` · `root_id` · `typed_chars` | 게시 시도 한 줄. `root_id`는 ③ 한 번 누름의 첫 시도 id — 재시도 줄도 같은 값(poster). `typed_chars`는 크롬에 친 글자 수(공백 포함 코드포인트, 처음 0) |
| `CommentRules` | `brand` · `brand_desc` · `min_chars` · `max_chars` · `min_count` · `max_count` · `max_similarity` · `max_rewrites` · `forbidden_phrases` · `grounding_min_overlap` | 댓글 규칙(config가 채움) |
| `RisingRules` | `keyword` · `published_within_hours` · `min_views` · `max_views` · `min_views_per_hour` · `remeasure` · `remeasure_min_gap_minutes` | 뜨기 직전 기준 |
| `Profile` · `Channel` | `name` · `user_data_dir` / `id` · `name` | 크롬 프로필 · 지정 채널. 로그인 여부는 설정에 두지 않고 `chrome.login_state`로 그때그때 확인한다 [사용자] |
| `LoginState` | `LOGGED_IN` · `NEEDS_LOGIN` · `WINDOW_OPEN` | 프로필의 유튜브 로그인 상태 — 로그인되어 있음 · 로그인 필요 · 크롬 창 열림(확인 불가). 판정은 chrome 명세 |
| `Settings` | 위 규칙들 + `comment_prompt`(지시문 파일의 글, 자리표시 채우기 전) + `post_mode` · `dev_mode` · `channel_posting` · `draft_on_select` · `profile_assign` · `profiles` · `channels` · `test_video_ids` · `per_channel` · `daily_max` · `max_retries` · `auto_gap_seconds` · `llm_model` · `db_path` · `screenshot_dir` · `youtube_api_key` · `anthropic_api_key` | 설정 전체 |

### `PostStatus`

| 값 | 화면 문구 | 게시 버튼을 눌렀나 | 화면 갈래(`tone`) |
|---|---|---|---|
| `QUEUED` | 대기 중 | 아니오 | progress |
| `OPENING` | 프로필 여는 중 | 아니오 | progress |
| `TYPING` | 입력 중 | 아니오 | progress |
| `SUBMITTED` | 게시 버튼 누름 | 예 | progress |
| `CONFIRMING` | 내 댓글 확인 중 | 예 | progress |
| `CONFIRMED` | 게시 완료 | 예 | done |
| `FAILED_BEFORE_SUBMIT` | 실패(게시 전) | 아니오 | fix |
| `FAILED_AFTER_SUBMIT` | 실패(게시 후 확인) | 예 | fix |
| `REJECTED` | 게시 전 검사 거부 | 아니오 | blocked |

- `PostStatus.pressed_submit` 속성이 「게시 버튼을 눌렀나」 열을 돌려준다. 하루 상한(D-13)과 영상당 1개(D-05)가 이 값 하나로 센다.
- `PostStatus.label` 속성이 화면 문구를 돌려준다.
- `PostStatus.tone` 속성이 화면 갈래를 돌려준다 — 끝남(done) · 손봐야 해요(fix: 다시 하거나 확인할 일) · 막혔어요(blocked: 규칙상 못 함) · 진행(progress). ③ 칸과 기록 표가 이 값 하나로 색과 문구를 고른다(화면은 상태를 다시 나누지 않는다).
- `PostStatus.finished` 속성은 끝난 상태(`CONFIRMED` · `FAILED_BEFORE_SUBMIT` · `FAILED_AFTER_SUBMIT` · `REJECTED`)에서 참이다. 끝난 시도는 상태를 다시 바꾸지 않는다(store).
- `QUEUED`는 시도의 첫 상태다. 게시 로봇이 그 시도를 잡으면 `OPENING`이 된다(poster · store `claim`).
- `Settings`의 `youtube_api_key` · `anthropic_api_key`는 출력(`repr`)에서 가린다.

## 정체성

- 「게시 버튼을 눌렀나」는 `SUBMITTED` · `CONFIRMING` · `CONFIRMED` · `FAILED_AFTER_SUBMIT`에서만 참이다 — `기능_게시상태_버튼을_누른_상태만_참이다`
- 비밀 값은 출력에 찍히지 않는다 — `기능_설정_출력에_API키가_보이지_않는다`
- 끝난 상태는 완료·실패 둘·거부 넷뿐이다 — `기능_게시상태_끝난_상태는_넷이다`
- 화면 갈래는 완료=done, 실패 둘=fix, 거부=blocked, 나머지=progress다 — `기능_상태_갈래는_완료_손봐야_막힘_진행이다`

## 의존

- 의존하는 것: 없음(표준 라이브러리만).
- 이것에 의존하는 것: 모든 부품.

## 미정 사항

- 없음.
