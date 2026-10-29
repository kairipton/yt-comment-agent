"""프로필 자동 배정 — 로그인함 프로필 중 가장 오래 안 쓴 프로필을 고른다. 명세: docs/systems/assign.md

결정론 부품이다(D-19): 같은 후보·기록이면 언제나 같은 프로필. 입출력·현재 시각 읽기 없이 인자로만 판단한다.
후보(지금 유튜브에 로그인되어 있는 프로필)는 부르는 쪽(서버)이 chrome의 확인 결과로 걸러 넘긴다.
"""

from __future__ import annotations

from datetime import datetime

from agent.models import Attempt


def next_profile(candidates: list[str], history: list[Attempt]) -> str:
    """후보 중 「마지막으로 게시 버튼을 누른 시각」이 가장 오래된 프로필. 한 번도 안 쓴 것이 먼저, 같으면 후보 순서.

    게시 버튼을 누르지 않은 시도(거부 · 버튼 전 실패)는 세지 않는다 — 실패한 프로필의 차례가 넘어가 버리지 않게.
    """
    if not candidates:
        raise ValueError("자동 배정할 프로필이 없어요(유튜브에 로그인되어 있는 프로필이 없음).")
    last_used: dict[str, datetime] = {}
    for a in history:
        if a.submitted_at is not None and a.profile in candidates:
            last_used[a.profile] = max(a.submitted_at, last_used.get(a.profile, a.submitted_at))
    # 안 쓴 프로필은 (0, 순서) — 쓴 프로필 (1, 시각)보다 앞에 온다. 같은 무리 안에서는 순서·시각으로.
    order = {name: i for i, name in enumerate(candidates)}
    return min(candidates, key=lambda n: (0, order[n]) if n not in last_used else (1, last_used[n], order[n]))
