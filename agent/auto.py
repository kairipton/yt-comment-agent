"""자동 모드 — 체크한 영상들을 사람 승인 없이 차례로 초안 → 게시까지 돌린다(D-25). 명세: docs/systems/auto.md

안전장치는 문지기와 게시 검사(poster.check_post)뿐이라, 자동 모드도 ③ 한 번 누름과 「같은 게시 경로」(begin_post)를 탄다.
이 파일은 차례를 돌리는 순서만 안다. 초안 쓰기 · 게시 시작 · 기다리기는 서버가 AutoDeps로 넘긴다(테스트는 가짜).

한 영상의 차례: 멈춤 확인 → 하루 상한 확인(닿으면 전체 멈춤) → 초안 → 게시(검사에 걸리면 건너뜀) → 끝까지 기다림 → 간격.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from agent import guard
from agent.models import Attempt, Draft, PostStatus, Settings, VideoItem
from agent.store import Store

log = logging.getLogger(__name__)

# 차례 상태 — 화면에 그대로 보인다.
WAITING, DRAFTING, POSTING, DONE, SKIPPED, FAILED, STOPPED = (
    "대기", "초안 쓰는 중", "게시 중", "게시 완료", "건너뜀", "실패", "멈춤")
QUOTA_STOP = "오늘 게시 상한에 닿아 멈췄어요."
CRASH_STOP = "자동 실행이 오류로 멈췄어요 — 설정 파일이나 기록장을 확인해 주세요."
USER_STOP = "멈추기를 눌러 시작하지 않았어요."


@dataclass
class AutoItem:
    video: VideoItem
    state: str = WAITING
    reason: str | None = None
    attempt_id: int | None = None
    comment_url: str | None = None


@dataclass
class AutoJob:
    """한 번의 자동 실행. 서버가 뒤에서 run_auto를 돌리는 동안 화면은 snapshot으로 상태를 읽는다."""

    items: list[AutoItem]
    profile: str | None = None            # 직접 고르기일 때 고른 프로필(자동 배정이면 None)
    finished: bool = False
    _stop: threading.Event = field(default_factory=threading.Event)

    def stop(self) -> None:
        """지금 하던 영상까지만 하고 멈춘다. 간격을 기다리던 중이면 바로 멈춘다."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def snapshot(self) -> dict:
        return {"finished": self.finished, "stopping": self.stopping,
                "items": [{"video_id": i.video.id, "title": i.video.title, "state": i.state, "reason": i.reason,
                           "attempt_id": i.attempt_id, "comment_url": i.comment_url} for i in self.items]}


@dataclass(frozen=True)
class AutoDeps:
    open_store: Callable[[], tuple[Settings, Store]]
    write_draft: Callable[[Settings, Store, VideoItem], Draft]    # 저장된 초안이 없으면 쓴다
    begin_post: Callable[[Settings, Store, VideoItem, str], int]  # 시도 생성 → 검사 → 로봇 or 거부. 첫 시도 id
    pick_profile: Callable[[Settings, Store], str]
    wait_post: Callable[[int], Attempt]                           # 그 누름이 끝날 때까지 기다려 마지막 줄
    wait: Callable[[float], bool]                                 # 간격 기다리기. 멈춤 신호가 오면 True
    now: Callable[[], object]


def run_auto(job: AutoJob, deps: AutoDeps) -> None:
    """차례대로 돈다. 끝나면(다 돌았거나 · 상한 · 멈춤) job.finished가 참이 된다."""
    try:
        for index, item in enumerate(job.items):
            if job.stopping:
                _end_rest(job, index, STOPPED, USER_STOP)
                return
            settings, store = deps.open_store()
            since, until = guard.quota_window(deps.now(), store.last_quota_reset())
            if not guard.check_quota(False, True, store.submitted_count(since, until), settings.daily_max).ok:
                _end_rest(job, index, STOPPED, QUOTA_STOP)
                return
            launched = _one(item, settings, store, deps)
            # 실제로 로봇을 띄운 영상 뒤에만, 남은 차례가 있을 때만 간격을 둔다.
            if launched and index < len(job.items) - 1 and deps.wait(settings.auto_gap_seconds):
                _end_rest(job, index + 1, STOPPED, USER_STOP)
                return
    except Exception:  # 차례 사이의 오류(설정 파일을 잘못 고침 · 기록장 잠김 등) — 삼키지 않고 남은 차례에 이유를 남긴다.
        log.error("자동 실행이 오류로 멈춤", exc_info=True)
        _end_rest(job, 0, FAILED, CRASH_STOP)
    finally:
        job.finished = True


def _one(item: AutoItem, settings: Settings, store: Store, deps: AutoDeps) -> bool:
    """영상 하나: 초안 → 게시 → 결과. 로봇을 띄웠으면 True(간격을 둘지 정하려고)."""
    try:
        item.state = DRAFTING
        deps.write_draft(settings, store, item.video)
        item.state = POSTING
        aid = deps.begin_post(settings, store, item.video, deps.pick_profile(settings, store))
        item.attempt_id = aid
        last = deps.wait_post(aid)
    except Exception as e:  # 초안 실패(키·API) 등 — 이 영상만 실패로 적고 다음으로 간다.
        log.error("자동 모드: %s 처리 중 오류", item.video.id, exc_info=True)
        item.state, item.reason = FAILED, getattr(e, "detail", None) or str(e) or type(e).__name__
        return False
    if last.status is PostStatus.REJECTED:
        # 게시 검사에 걸렸다(허용 범위 밖 · 이미 단 영상 · 불합격 초안 …) — 로봇은 뜨지 않았다.
        item.state, item.reason = SKIPPED, last.reason
        return False
    item.reason, item.comment_url = last.reason, last.comment_url
    item.state = DONE if last.status is PostStatus.CONFIRMED else FAILED
    return True


def _end_rest(job: AutoJob, start: int, state: str, reason: str) -> None:
    for item in job.items[start:]:
        if item.state == WAITING:
            item.state, item.reason = state, reason
