"""뜨기 직전 계산 — 평균 속도 · 최근 속도 · 뜨기 직전 기준. 명세: docs/systems/trend.md

결정론 부품이다(D-19): 조회수·시각·기준을 전부 인자로 받는다. 현재 시각을 스스로 읽지 않는다.
방법(D-24): 최근 48시간 · 평균 속도(조회수 ÷ 올린 뒤 지난 시간)로 기준(config `rising`)을 거르고,
선택 기능 「다시 측정」은 두 번 잰 조회수 차이 ÷ 간격(최근 속도)으로 정렬한다.
뜨기 직전 영상을 게시하지 않는 것(D-22)은 문지기 check_target의 몫이다 — 여기는 고르기만 한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agent.models import RisingRules

_HOUR = timedelta(hours=1)


def views_per_hour(views: int, published_at: datetime, now: datetime) -> float:
    """평균 속도. 지난 시간이 1시간보다 짧으면 1시간으로 본다 — 올린 지 몇 분 된 영상의 속도가 터무니없이 커지지 않게."""
    hours = max((now - published_at) / _HOUR, 1.0)
    return views / hours


def recent_velocity(v1: int, t1: datetime, v2: int, t2: datetime) -> float:
    """최근 속도 = (두 번째 조회수 − 첫 번째) ÷ 간격(시간). 조회수가 줄었으면(집계 보정) 0."""
    if t2 <= t1:
        raise ValueError("두 번째 측정이 첫 번째보다 늦어야 해요.")
    return max(v2 - v1, 0) / ((t2 - t1) / _HOUR)


def is_rising(views: int, per_hour: float, published_at: datetime, now: datetime, rules: RisingRules) -> bool:
    """뜨기 직전 기준: 올린 지 published_within_hours 안 · 조회수 min~max · 평균 속도 min_views_per_hour 이상. 셋 다 맞아야 참."""
    recent = now - published_at <= timedelta(hours=rules.published_within_hours)
    in_range = rules.min_views <= views <= rules.max_views
    return recent and in_range and per_hour >= rules.min_views_per_hour
