"""테스트 공용 도구 — 영상·규칙·시각을 손쉽게 만든다. 값은 config.yaml과 같은 꼴이지만 테스트가 스스로 정한다."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.models import CommentRules, Source, VideoItem

CHANNEL_A = "UCxlc0Ky0aWoD8uI09ad44Tg"  # 지정 채널(외롭여)
CHANNEL_X = "UCzzzzzzzzzzzzzzzzzzzzzz"  # 지정되지 않은 채널
TEST_ID = "z_K3rHRbqck"                 # 본인 테스트 영상

# 테스트 영상의 자막을 흉내 낸 재료와, 그 재료에 맞춘 좋은 댓글.
SOURCE_TEXT = (
    "썸 타는 사람 답장이 하루씩 늦어요 저만 진심인가요 요즘 썸 타는 사람이 있어요 회사 동기 소개로 만났고 벌써 세 번 만났어요 "
    "만나면 분위기는 정말 좋은데 카톡 답장은 늘 하루 뒤에 와요 지난주에 제가 영화 보자고 했더니 이번 주는 좀 바빠서라는 답만 왔어요"
)
GOOD_COMMENT = (
    "영화 보자고 했는데 '이번 주는 좀 바빠서'라니 저도 똑같이 겪어봐서 너무 공감돼요ㅠ "
    "운명원 연애사주가 잘 본다던데 이 사람이랑 인연인지 한번 보고 싶네요."
)


def video(vid: str = "vid00000001", channel_id: str = CHANNEL_A, source: Source = Source.CHANNEL) -> VideoItem:
    return VideoItem(id=vid, title="제목", channel_id=channel_id, channel_name="채널", published_at=None,
                     thumbnail_url="https://i.ytimg.com/x.jpg", source=source)


def at(hour: int, minute: int = 0, day: int = 20) -> datetime:
    """UTC 시각을 만든다. 한국 시간은 이보다 9시간 빠르다."""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def rules() -> CommentRules:
    return CommentRules(
        brand="운명원", brand_desc="연애 사주 잘 봐 주는 곳으로 유명한 곳",
        min_chars=40, max_chars=120, min_count="no_spaces", max_count="with_spaces",
        max_similarity=0.5, max_rewrites=3, grounding_min_overlap=2,
        forbidden_phrases=("받아봤", "봤는데 맞", "다녀왔", "후기"),
    )


class 가짜화면:
    """바탕화면 캡처 이미지 흉내 — 실제 화면을 찍지 않는다. save만 한다(PIL Image와 같은 이름)."""

    def save(self, path) -> None:
        Path(path).write_bytes(b"png")
