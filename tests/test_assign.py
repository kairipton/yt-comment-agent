"""assign 테스트 — 로그인함 프로필 중 가장 오래 안 쓴 프로필을 고르는지."""

from agent.assign import next_profile
from agent.models import Attempt, PostStatus
from tests.conftest import at, video

PROFILES = ["유튜브1", "유튜브2", "유튜브3"]


def 시도(profile: str, submitted_hour: int | None) -> Attempt:
    """기록 한 줄. submitted_hour가 None이면 게시 버튼을 누르지 않은 시도(거부·버튼 전 실패)."""
    submitted = at(submitted_hour) if submitted_hour is not None else None
    status = PostStatus.CONFIRMED if submitted else PostStatus.REJECTED
    return Attempt(id=1, created_at=at(1), video=video(), profile=profile, text="댓글", status=status,
                   updated_at=at(1), reason=None, screenshot=None, comment_url=None, retry_no=0, exempt=False,
                   submitted_at=submitted, root_id=1)


def 기능_자동배정_가장_오래_안쓴_프로필을_고른다():
    """목적: 한 번도 안 쓴 프로필이 먼저(후보 순서), 다 썼으면 마지막 게시가 가장 오래된 프로필 — 유튜브1→2→3→1로 돈다."""
    assert next_profile(PROFILES, [시도("유튜브1", 3)]) == "유튜브2"
    assert next_profile(PROFILES, [시도("유튜브1", 3), 시도("유튜브2", 4), 시도("유튜브3", 5)]) == "유튜브1"
    assert next_profile(PROFILES, [시도("유튜브1", 6), 시도("유튜브2", 4), 시도("유튜브3", 5)]) == "유튜브2"


def 기능_자동배정_버튼을_안누른_시도는_세지_않는다():
    """목적: 거부·버튼 전 실패는 그 프로필을 「쓴 것」으로 세지 않는다 — 실패한 프로필 차례가 넘어가지 않게."""
    assert next_profile(PROFILES, [시도("유튜브1", None)]) == "유튜브1"


def 경계_자동배정_후보가_하나면_늘_그것이다():
    """목적: 로그인함 프로필이 하나면(지금: 유튜브1) 기록과 상관없이 늘 그 프로필이다."""
    assert next_profile(["유튜브1"], [시도("유튜브1", 3), 시도("유튜브2", 1)]) == "유튜브1"
