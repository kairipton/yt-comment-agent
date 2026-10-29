# config — 설정 읽기

`config.yaml`(튜닝 값 · 토글)과 `.env`(비밀 값)를 읽어 검증한 `Settings`를 돌려준다.
값의 주인은 `config.yaml`이다(D-20) — 이 문서는 값을 적지 않고 읽는 규칙만 진다.

## 구성요소

```python
class ConfigError(Exception)                                   # 잘못된 값이 있을 때. 이유 목록을 전부 담는다
def load_settings(config_path: Path = Path("config.yaml"), env_path: Path = Path(".env")) -> Settings
def read_options(config_path: Path) -> dict                  # config.yaml 값을 사전 그대로(옵션 페이지가 그린다)
def save_options(config_path: Path, values: dict) -> None    # 임시 파일에 써 load_settings로 검사 → 통과면 교체. 아니면 ConfigError, 원래 파일 그대로
def read_prompt(config_path: Path) -> str                    # llm.prompt_file의 글
def save_prompt(config_path: Path, text: str) -> None        # prompt_problems가 없을 때만 쓴다. 아니면 ConfigError
def prompt_problems(text: str) -> list[str]                  # 빈 글 · 짝 안 맞는 중괄호 · 모르는 자리표시 — 읽을 때 검사와 같은 함수
```


## 동작

- **옵션 페이지가 쓴다 [사용자]:** 대시보드 [옵션] 페이지에서 `config.yaml`의 모든 값(저장 위치 포함)과 댓글 작성 지침을 바꾼다. 값의 주인은 그대로 `config.yaml`이다.
  - **`config.yaml`에는 주석을 두지 않는다 [사용자]** — 옵션 설명은 옵션 페이지가 진다. 그래서 저장은 일반 YAML 쓰기(키 순서 유지)다.
  - 저장은 같은 폴더의 임시 파일에 먼저 쓰고 `load_settings`로 검사한 뒤, 통과해야 원래 파일과 바꿔 끼운다. 검사에 걸리면 원래 파일은 그대로이고 이유 목록으로 `ConfigError`.
  - 지침 글은 `prompt_problems`로 검사한다(읽을 때와 같은 검사).
  - **AI 모델(`llm.model`)과 생각하는 깊이(`llm.effort`)는 옵션 페이지에서 바꾸지 못한다 [사용자]** — `save_options`는 이 두 값을 보내온 값과 상관없이 지금 파일의 값으로 둔다(`LOCKED_OPTIONS`). 바꾸려면 `config.yaml`을 직접 고친다.
  - **개발 모드(`dev_mode`)는 `config.yaml`로만 켠다 [사용자]** — 옵션 페이지에 칸이 없고, `LOCKED_OPTIONS`에 들어 있어 저장해도 지금 파일의 값 그대로다.

- **언제 읽나:** 부르는 쪽이 정한다. 서버는 요청마다 다시 읽는다(dashboard 명세) — 설정·키·지시문을 고치면 새로고침만으로 반영된다. 게시 로봇은 띄울 때마다 읽는다.
- **비밀 값:** `.env`의 `YOUTUBE_API_KEY` · `ANTHROPIC_API_KEY`. **없어도 오류가 아니다** — `None`으로 두고, 쓰는 쪽이 「키 없음」으로 처리한다(AI 키 D-28 · YouTube 키 D-31).
  `.env` 파일이 없어도 된다. 이미 설정된 환경 변수가 있으면 그것을 쓴다.
- **검사하는 것** — 하나라도 어기면 `ConfigError`, 이유를 전부 모아 한 번에 알린다:
  - 필수 키가 있다: `post_mode` · `dev_mode` · `channel_posting` · `profiles` · `channels` · `comment` · `limits` · `storage`.
  - `post_mode`는 `manual` · `auto` 중 하나. `dev_mode` · `channel_posting`은 참/거짓. `channel_posting`은 지정 채널 영상 게시 스위치다(D-34, poster 명세).
  - `profiles`가 하나 이상이고 이름이 겹치지 않는다. 프로필마다 `name` · `user_data_dir`. **로그인 여부는 설정에 두지 않는다** — `chrome.login_state`가 그때그때 확인한다 [사용자]. 옛 설정 파일에 `logged_in`이 남아 있어도 읽지 않는다(오류 아님).
  - `llm.draft_on_select`는 참/거짓 — 참이면 ②에서 영상을 고르자마자 초안을 쓰고(기본 흐름), 거짓이면 [초안 쓰기]를 눌러야 쓴다(AI 비용을 아끼려는 사람용) [사용자].
  - `profile_assign`은 `manual`(직접 고르기) · `rotate`(돌아가며 자동) 중 하나(assign 명세) [사용자].
  - `channels`가 하나 이상이고, 채널 id는 `UC`로 시작하는 24자다.
  - `comment.min_chars < comment.max_chars`. `min_count` · `max_count`는 `no_spaces` · `with_spaces` 중 하나.
  - `comment.max_similarity`는 0~1. `max_rewrites`는 0 이상.
  - **금지 조항이 조용히 꺼지지 않게:** `comment.brand`가 비어 있지 않고, `forbidden_phrases`가 하나 이상, `grounding_min_overlap`이 1 이상.
  - `limits.daily_max_comments`는 1 이상. `max_retries_per_post`는 0 이상.
  - **코드에 기본값을 두지 않는다.** `Settings`가 쓰는 키가 `config.yaml`에 빠져 있으면 오류다 — 값의 주인은 그 파일 하나다.
- **이유 문구의 꼴:** 설정 하나에 걸린 이유는 `경로: 설명`(예: `comment.min_chars: comment.max_chars보다 작아야 해요`), 목록의 한 줄이면 `channels[1]: …`. 설명은 「~요」로 끝낸다. 옵션 페이지가 앞의 경로를 칸 이름으로, 설명 안의 경로를 「칸 이름」으로 바꿔 보여 주므로, 설명 안의 경로 뒤에는 받침에 따라 바뀌는 조사(은/는 · 이/가 · 을/를 · 와/과)를 붙이지 않는다.
- **검사하지 않는 것:** 프로필 폴더가 실제로 있는지 · 로그인됐는지 · 채널 id가 유튜브에 실재하는지. 그것은 게시 로봇·수집 담당이 실행할 때 실패 이유로 알린다.
- 경로 값(`db_path` · `screenshot_dir` · `llm.prompt_file`)은 `config.yaml`이 있는 폴더 기준으로 푼다.
- **댓글 지시문 파일**(`llm.prompt_file`) [사용자]: 작가가 Claude에 주는 지시문을 코드가 아니라 이 파일에 둔다 — 언제든 고칠 수 있게. 읽을 때 파일이 없거나 · 비었거나 · 모르는 자리표시(`{이름}`)가 있으면 오류(쓸 수 있는 자리표시는 draft 명세). 서버는 요청마다 설정을 다시 읽으므로 **고친 지시문은 다음 초안부터 바로** 쓰인다(재시작 불필요).
- **비밀 값은 화면·로그·오류 문구에 찍히지 않는다** — `Settings`를 출력해도 키 값은 보이지 않고, 오류 문구에는 키 이름만 나온다.

## 정체성

- 운영 규칙 값(재시도 횟수 · 하루 상한의 타입 · 프로필 · 채널)이 잘못되면 그 경로로 거부된다 — `예외_설정읽기_운영규칙_값이_잘못되면_거부된다`
- 옵션을 저장하면 다시 읽었을 때 그 값이고, 잘못된 값이면 파일이 그대로다 — `기능_옵션저장_바꾼_값이_다시_읽힌다` · `예외_옵션저장_잘못된_값이면_파일이_그대로다`
- 옵션 저장은 AI 모델 · 생각 깊이를 바꾸지 않는다 — `기능_옵션저장_모델과_생각깊이는_바뀌지_않는다`
- 옵션 저장은 개발 모드를 바꾸지 않는다 — `기능_옵션저장_개발모드는_바뀌지_않는다`
- 지침은 검사를 통과해야 저장된다 — `기능_지침저장_올바른_글은_저장된다` · `예외_지침저장_모르는_자리표시는_거부된다`
- 잘못된 설정은 조용히 기본값으로 바뀌지 않는다 — 오류로 알린다 — `예외_설정읽기_빠진_키는_이유와_함께_거부된다`
- 설정 하나에 걸린 이유는 그 경로로 시작한다(옵션 페이지가 칸을 찾는 실마리) — `기능_설정읽기_이유는_설정경로로_시작한다`
- 금지 표현 목록이 비거나 겹침 기준이 0이면 오류다 — 금지 조항 검사가 꺼진 채 돌지 않는다 — `예외_설정읽기_금지표현이_비면_거부된다` · `예외_설정읽기_겹침기준이_0이면_거부된다`
- API 키가 없어도 설정 읽기는 성공한다(D-28 · D-31) — `기능_설정읽기_API키가_없어도_성공한다`.
  키를 어떻게 쓰고 없을 때 무엇을 보여 줄지는 2단계 fetch · draft 명세가 진다.
- 비밀 값은 출력에 찍히지 않는다 — `기능_설정_출력에_API키가_보이지_않는다`
- 지시문 파일이 없거나 모르는 자리표시가 있으면 거부한다 — `예외_설정읽기_지시문파일이_없으면_거부된다` · `예외_설정읽기_지시문의_모르는_자리표시는_거부된다`

## 의존

- 의존하는 것: `models`, `pyyaml`, `python-dotenv`.
- 이것에 의존하는 것: 서버(`app.py`) · `poster` · `auto`.

## 미정 사항

- 없음.
