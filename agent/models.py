"""공용 자료형 — 부품들이 주고받는 값의 모양만 정한다. 명세: docs/systems/models.md

입출력·판정이 없는 결정론 부품이다(D-19). 값은 전부 바꿀 수 없는 자료형(frozen dataclass · Enum)이라,
한 부품이 받은 값을 고쳐 다른 부품에 영향을 주는 일이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

# 댓글 지시문 파일(config llm.prompt_file)에서 쓸 수 있는 자리표시 {이름}.
# 설정 읽기(config)가 이 밖의 이름을 오류로 막고, 작가(draft)가 config 값으로 채운다.
PROMPT_FIELDS = ("brand", "brand_desc", "min_chars", "max_chars", "min_count", "max_count", "forbidden_examples")


class Source(StrEnum):
    """영상이 어디서 왔나. 허용 범위 판정(guard.check_target)이 이 값을 본다."""

    CHANNEL = "channel"  # 지정 채널의 최신 영상
    TEST = "test"        # 본인 테스트 영상
    LINK = "link"        # 링크 직접 입력
    RISING = "rising"    # 뜨기 직전 영상 — 게시 금지


@dataclass(frozen=True)
class VideoItem:
    """목록 한 줄."""

    id: str
    title: str
    channel_id: str
    channel_name: str
    published_at: datetime | None
    thumbnail_url: str
    source: Source
    description: str = ""  # 목록을 가져올 때 받아 두어 초안 재료로 다시 쓴다. 기록장에는 저장하지 않는다
    view_count: int | None = None  # 조회수. RSS·videos.list가 줄 때만 있다(oEmbed·기록장에서 꺼낸 영상은 None)


@dataclass(frozen=True)
class VideoContext:
    """초안 재료. 자막이 없으면 transcript가 None이고, 작가는 제목·설명(video.description)으로 대신한다."""

    video: VideoItem
    transcript: str | None
    top_comments: tuple[str, ...] = ()


@dataclass(frozen=True)
class Draft:
    """영상 하나의 고민 한 줄과 댓글 초안.

    version: 몇 번째 초안(사용자가 다시 쓰기를 누른 횟수 + 1).
    issues: 문지기 불합격 이유. 합격이면 빈 튜플이고, 이유가 있으면 게시할 수 없다.
    transcript_chars · comments_read: AI가 읽은 양(화면 ②의 「AI가 읽은 것」).
      transcript_chars는 자막 글자 수(0 = 자막 없음), comments_read는 인기 댓글 수(None = YouTube 키 없음).
      둘 다 None이면 이 값을 남기기 전에 쓴 옛 초안이다.
    """

    video_id: str
    worry: str
    comment: str
    version: int = 1
    issues: tuple[str, ...] = ()
    transcript_chars: int | None = None
    comments_read: int | None = None


@dataclass(frozen=True)
class Verdict:
    """판정 결과. 불합격이면 이유를 전부 담는다 — 작가가 그 이유를 붙여 다시 쓰고, 화면이 그대로 보여 준다."""

    ok: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def passed(cls) -> Verdict:
        return cls(ok=True)

    @classmethod
    def from_reasons(cls, reasons: list[str]) -> Verdict:
        """이유가 하나도 없으면 통과, 있으면 불합격."""
        return cls(ok=not reasons, reasons=tuple(reasons))


class PostStatus(StrEnum):
    """게시 시도의 진행 상태. 값 문자열은 DB에 그대로 저장된다 — 바꾸면 옛 기록을 못 읽는다."""

    QUEUED = "queued"   # 시도의 첫 상태. 게시 로봇이 잡으면(store.claim) OPENING
    OPENING = "opening"
    TYPING = "typing"
    SUBMITTED = "submitted"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    FAILED_BEFORE_SUBMIT = "failed_before_submit"
    FAILED_AFTER_SUBMIT = "failed_after_submit"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        """화면에 보이는 문구."""
        return _LABELS[self]

    @property
    def pressed_submit(self) -> bool:
        """게시 버튼을 누른 뒤의 상태인가. 하루 상한(D-13)과 영상당 1개(D-05)가 이 값 하나로 센다."""
        return self in _PRESSED

    @property
    def finished(self) -> bool:
        """끝난 상태인가. 끝난 시도는 상태를 다시 바꾸지 않는다(store) — 완료 위에 거부가 덮이는 일을 막는다."""
        return self in _FINISHED

    @property
    def tone(self) -> str:
        """화면 갈래 — done(끝났어요) · fix(손봐야 해요: 다시 하거나 확인할 일) · blocked(막혔어요: 규칙상 못 함) · progress.

        ③ 칸과 기록 표가 이 값 하나로 색과 문구를 고른다. 화면이 상태를 다시 나누지 않게 갈래는 여기 한 곳에 둔다.
        """
        return _TONES.get(self, "progress")


_LABELS = {
    PostStatus.QUEUED: "대기 중",
    PostStatus.OPENING: "프로필 여는 중",
    PostStatus.TYPING: "입력 중",
    PostStatus.SUBMITTED: "게시 버튼 누름",
    PostStatus.CONFIRMING: "내 댓글 확인 중",
    PostStatus.CONFIRMED: "게시 완료",
    PostStatus.FAILED_BEFORE_SUBMIT: "실패(게시 전)",
    PostStatus.FAILED_AFTER_SUBMIT: "실패(게시 후 확인)",
    PostStatus.REJECTED: "게시 전 검사 거부",
}
_PRESSED = frozenset({PostStatus.SUBMITTED, PostStatus.CONFIRMING, PostStatus.CONFIRMED,
                      PostStatus.FAILED_AFTER_SUBMIT})
_FINISHED = frozenset({PostStatus.CONFIRMED, PostStatus.FAILED_BEFORE_SUBMIT, PostStatus.FAILED_AFTER_SUBMIT,
                       PostStatus.REJECTED})
# 끝난 상태의 화면 갈래. 여기 없는 상태(대기·진행 중)는 progress.
_TONES = {
    PostStatus.CONFIRMED: "done",
    PostStatus.FAILED_BEFORE_SUBMIT: "fix",
    PostStatus.FAILED_AFTER_SUBMIT: "fix",  # 버튼은 눌렀다 — 실제로 달렸는지 사용자가 확인할 일
    PostStatus.REJECTED: "blocked",
}


@dataclass(frozen=True)
class Attempt:
    """게시 시도 한 줄. 재시도도 새 줄이다(retry_no로 구분).

    root_id: ③ 한 번 누름의 첫 시도 id. 첫 시도는 자기 id, 재시도 줄은 첫 시도의 id — 화면이 재시도를 따라가는 끈.
    """

    id: int
    created_at: datetime
    video: VideoItem
    profile: str
    text: str
    status: PostStatus
    updated_at: datetime
    reason: str | None
    screenshot: Path | None
    comment_url: str | None
    retry_no: int
    exempt: bool
    submitted_at: datetime | None
    root_id: int
    typed_chars: int = 0  # 크롬에 친 글자 수(공백 포함 코드포인트) — 화면의 「86자 중 45자」


@dataclass(frozen=True)
class CommentRules:
    """댓글 규칙. 값의 주인은 config.yaml의 comment 절이다."""

    brand: str
    brand_desc: str
    min_chars: int
    max_chars: int
    min_count: str  # no_spaces | with_spaces
    max_count: str
    max_similarity: float
    max_rewrites: int
    grounding_min_overlap: int
    forbidden_phrases: tuple[str, ...]


@dataclass(frozen=True)
class RisingRules:
    """뜨기 직전 영상 기준. 값의 주인은 config.yaml의 rising 절이다."""

    keyword: str
    published_within_hours: int
    min_views: int
    max_views: int
    min_views_per_hour: float
    remeasure: bool
    remeasure_min_gap_minutes: int


@dataclass(frozen=True)
class Profile:
    """크롬 프로필 — 이름과 그 프로필의 크롬 정보 폴더. 로그인 여부는 여기 두지 않고 chrome.login_state로 그때그때 확인한다."""

    name: str
    user_data_dir: Path


class LoginState(StrEnum):
    """프로필의 유튜브 로그인 상태(chrome.login_state). 값 문자열은 화면 응답에 그대로 실린다."""

    LOGGED_IN = "logged_in"      # 로그인되어 있음
    NEEDS_LOGIN = "needs_login"  # 로그인 필요(쿠키 없음 · 한 번도 연 적 없음)
    WINDOW_OPEN = "window_open"  # 그 프로필 크롬 창이 열려 있어 확인할 수 없음


@dataclass(frozen=True)
class Channel:
    id: str
    name: str


@dataclass(frozen=True)
class Settings:
    """설정 전체. config.load_settings가 채운다. 비밀 값 두 개는 출력(repr)에서 가린다."""

    post_mode: str  # manual | auto
    dev_mode: bool
    channel_posting: bool  # 꺼지면 지정 채널 영상 게시 금지 — 테스트 영상만(D-34)
    profile_assign: str    # manual(직접 고르기) | rotate(로그인함 프로필 중 돌아가며 자동)
    profiles: tuple[Profile, ...]
    channels: tuple[Channel, ...]
    test_video_ids: frozenset[str]
    per_channel: int
    comment: CommentRules
    daily_max: int
    max_retries: int  # ③을 한 번 누를 때 자동 재시도 최대 횟수
    auto_gap_seconds: int
    rising: RisingRules
    llm_model: str
    llm_effort: str  # low | medium | high | xhigh | max
    draft_on_select: bool  # true: 영상을 고르자마자 초안을 쓴다 / false: [초안 쓰기]를 눌러야 쓴다(AI 비용 절약)
    comment_prompt: str  # 댓글 지시문 파일(llm.prompt_file)의 글 — {brand} 같은 자리표시는 작가가 채운다
    fetch_timeout: float
    db_path: Path
    screenshot_dir: Path
    youtube_api_key: str | None = field(default=None, repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)
