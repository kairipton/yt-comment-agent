# 유튜브 댓글 에이전트

연애 상담 유튜브 영상을 고르면 AI가 영상 속 고민을 한 줄로 정리하고, 시청자 말투로 댓글 초안을 씁니다.
버튼을 누르면 크롬이 열리고 한 글자씩 직접 입력해 댓글을 단 뒤, 제대로 달렸는지까지 확인합니다.

> 채용 과제로 만든 마케팅 AI 에이전트입니다.
> 제작 의도는 이 문서 아래쪽 [「제작 의도」](#제작-의도)에 적었습니다.
>
> 시연 영상: https://youtu.be/z_K3rHRbqck

---

## 준비물

- Windows 10 또는 11
- [구글 크롬](https://www.google.com/chrome)
- API 키 2개 (아래 「키 설정」)
  - **Anthropic API 키** — 필수. 댓글 초안을 씁니다.
  - **YouTube Data API 키** — 권장. 없으면 인기 댓글을 읽지 못하고, 「뜨기 직전 영상 찾기」를 쓸 수 없습니다.

---

## 키 설정 (실행 전에)

1. 프로젝트 폴더의 `.env.example` 파일을 복사해 이름을 **`.env`**로 바꿉니다.
2. 메모장으로 열어 `=` 뒤에 키를 넣고 저장합니다.

```
YOUTUBE_API_KEY=여기에_YouTube_키
ANTHROPIC_API_KEY=여기에_Anthropic_키
```

- Anthropic API 키: [Anthropic Console](https://console.anthropic.com/)에서 만듭니다.
- YouTube Data API 키: [Google Cloud Console](https://console.cloud.google.com/)에서 **YouTube Data API v3**를 사용 설정한 뒤 API 키를 만듭니다.
- 파일 만들기가 번거로우면 먼저 한 번 실행해도 됩니다. 빈 `.env`가 저절로 생기니, 거기에 키를 넣고 대시보드를 새로고침하세요.
- 나중에 키를 바꾸면, 저장한 뒤 대시보드를 **새로고침**하면 바로 반영됩니다.

---

## 실행

**먼저 위의 「키 설정」을 마쳤는지 확인하세요.** 키가 없으면 댓글 초안을 쓸 수 없습니다.

프로젝트 폴더의 **`run_dashboard.bat`을 더블클릭**하세요.

- 처음 실행할 때는 필요한 프로그램을 자동으로 설치하느라 1~2분 걸립니다.
- 준비가 끝나면 크롬에서 대시보드가 열립니다. 위쪽에 키가 「설정됨」으로 보이면 제대로 된 것입니다.
- **검은 창을 닫으면 프로그램이 끝납니다.** 쓰는 동안에는 열어 두세요.

---

## 유튜브 계정 연결 (처음 한 번)

댓글은 평소 쓰는 크롬과 **따로 떨어진 크롬 창**으로 답니다. 여기에 한 번 로그인해 두면 됩니다.

1. 대시보드 위쪽의 **[프로필 관리]**를 누릅니다.
2. 「유튜브1」의 **[크롬 열기]**를 누릅니다.
3. 열린 크롬 창에서 유튜브에 로그인합니다.
4. **로그인했으면 그 창을 닫습니다.** 잠시 뒤 「로그인되어 있음」으로 바뀝니다.

- 유튜브1 하나만 있으면 됩니다. 유튜브2 · 3을 쓰려면 각각 **다른 구글 계정**으로 로그인하세요.
- 댓글을 달기 전에는 그 프로필의 크롬 창을 **닫아 두세요.**

---

## 사용 방법

1. **① [영상 불러오기]** — 지정된 채널 5곳의 최신 영상이 나옵니다.
   - 위쪽 탭으로 「전체 · 지정 채널 · 뜨기 직전 · 테스트 영상」을 나눠 봅니다.
   - 유튜브 링크를 붙여 넣고 [추가]해서 영상을 더할 수도 있습니다.
2. **② 영상을 고릅니다** — AI가 영상을 읽고 10~20초 안에 고민 한 줄과 댓글 초안을 보여 줍니다.
   - 마음에 안 들면 **[마음에 안 들어요, 다시 써 주세요]**.
3. **③ [댓글 달기]** — 크롬이 열리고 댓글이 입력 · 게시되는 과정이 그대로 보입니다.
   - 끝나면 **[유튜브에서 댓글 보기 ↗]**로 달린 댓글을 바로 볼 수 있습니다.
   - 실패하면 이유와 그 순간의 화면이 함께 나옵니다.
4. 맨 아래 **게시 기록**에서 어느 영상에 · 어느 계정으로 · 무슨 댓글을 · 성공했는지 시간순으로 봅니다.

다른 영상을 고르려면 위쪽의 **[← 다른 영상으로 바꾸기]**를 누르세요.

**그 밖의 기능**
- **[뜨기 직전 영상 찾기]** — 「연애」 키워드로 막 뜨고 있는 영상을 찾습니다. 이 영상들은 초안까지만 만들고 댓글은 달지 않습니다.
- **자동으로 달기** — [옵션]에서 「댓글 다는 방식」을 「자동」으로 바꾸면, 목록에서 체크한 영상들에 차례로 댓글을 답니다.

---

## 알아 둘 규칙

댓글이 함부로 달리지 않도록 프로그램이 스스로 막습니다.

- **정해진 곳에만** — 지정 채널 5곳의 영상과 본인 테스트 영상에만 댓글을 답니다.
  처음에는 테스트 영상에만 달리게 되어 있습니다. 지정 채널 영상에도 달려면 [옵션]에서 「지정 채널 영상에도 달기」를 켜세요.
- **영상당 1개** — 한 영상에는 댓글 하나만 답니다.
- **하루 10개까지** — 모든 계정을 합쳐 하루 10개까지 답니다(한국 시간 밤 12시에 다시 셉니다).
- **지어낸 후기 금지** — 「운명원에 가 봤는데 딱 맞더라고요」처럼 직접 써 본 것처럼 꾸민 댓글은 쓰지 않습니다. 링크 · 해시태그도 넣지 않습니다.

---

## 설정 바꾸기

대시보드 위쪽의 **[옵션]**에서 바꿉니다. 칸마다 설명이 붙어 있고, 저장하면 바로 반영됩니다.<br>
하루에 달 댓글 수, 채널 목록, 댓글 글자 수, 댓글 작성 지침 등을 바꿀 수 있습니다.

---

## 제작 의도

저는 이 앱을 개발자가 쓰는 도구라기보다는 개발과는 무관한 직군이 쓰는 앱이라고 가정했습니다.<br>
그래서 사용 편의성에 최대한 집중했고, 가급적 최소한의 절차로 앱을 실행할 수 있는 구조로 만들고 싶었습니다. <br>
파일 하나(`run_dashboard.bat`)로 실행할 수 있게 하고, 대시보드 페이지에서 거의 모든 것을 제어할 수 있게 만든 것이 그 결과입니다.<br>
정말 중요한 내용 몇 가지는 별도로 설정해야 합니다. 하지만 그것은 키를 설정해야 하는 앱이라면 어떤 앱이든 마찬가지라 논외로 쳤습니다. <br>
그것만 설정하고 나면 나머지는 거의 모두 웹페이지에서 바꿀 수 있습니다.<br>
개발 모드 변경, AI 모델 설정 변경, API 키 설정을 빼면 전부 대시보드 안에서 통제할 수 있고, 대시보드 밖에서 설정해야 하는 것들도 사소한 것들이라 어렵지 않습니다.<br>
앱을 사용하는 사람이 간단한 설정만으로도 앱을 쓸 수 있게 한 것이 핵심 제작 의도입니다.<br>

---

## 쓴 오픈소스

| 이름 | 라이선스 |
|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | Apache-2.0 |
| [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) | MIT |
| [Google API Python Client](https://github.com/googleapis/google-api-python-client) | Apache-2.0 |
| [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) | MIT |
| [Requests](https://github.com/psf/requests) | Apache-2.0 |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU |
| [pytest](https://github.com/pytest-dev/pytest) | MIT |
| [Ruff](https://github.com/astral-sh/ruff) | MIT |
| [uv](https://github.com/astral-sh/uv) | MIT / Apache-2.0 |
| [Do Hyeon](https://fonts.google.com/specimen/Do+Hyeon) · [Gowun Dodum](https://fonts.google.com/specimen/Gowun+Dodum) (글꼴) | SIL OFL 1.1 |
