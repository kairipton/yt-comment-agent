"""trend 테스트 — 뜨기 직전 기준(48시간 · 조회수 1,000~5,000 · 시간당 150)과 속도 계산."""

import pytest

from agent.models import RisingRules
from agent.trend import is_rising, recent_velocity, views_per_hour
from tests.conftest import at

RULES = RisingRules(keyword="연애", published_within_hours=48, min_views=1000, max_views=5000,
                    min_views_per_hour=150, remeasure=False, remeasure_min_gap_minutes=30)


def 경계_뜨기직전_조회수와_속도의_끝값에서_갈린다():
    """목적: 조회수 1,000·5,000과 시간당 150은 들고, 999·5,001·149는 빠진다."""
    published, now = at(0), at(5)
    assert [is_rising(v, 150, published, now, RULES) for v in (999, 1000, 5000, 5001)] == [False, True, True, False]
    assert [is_rising(2000, s, published, now, RULES) for s in (149.9, 150)] == [False, True]


def 경계_뜨기직전_48시간을_넘으면_빠진다():
    """목적: 올린 지 딱 48시간은 들고, 그보다 1분이라도 지나면 빠진다."""
    published = at(0, day=18)
    assert is_rising(2000, 300, published, at(0, day=20), RULES)
    assert not is_rising(2000, 300, published, at(0, 1, day=20), RULES)


def 경계_평균속도_1시간보다_짧으면_1시간으로_본다():
    """목적: 올린 지 10분 된 영상은 1시간으로 나눈다(조회수 그대로). 2시간이면 반으로."""
    assert views_per_hour(600, at(3, 0), at(3, 10)) == 600
    assert views_per_hour(600, at(3), at(5)) == 300


def 기능_최근속도_두번_잰_차이를_간격으로_나눈다():
    """목적: 30분 사이 조회수가 150 늘면 시간당 300. 줄었으면 0, 순서가 거꾸로면 예외."""
    assert recent_velocity(1000, at(3, 0), 1150, at(3, 30)) == 300
    assert recent_velocity(1000, at(3, 0), 990, at(3, 30)) == 0
    with pytest.raises(ValueError):
        recent_velocity(1000, at(3, 30), 1100, at(3, 0))
