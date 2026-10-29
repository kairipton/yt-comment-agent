"""auto 테스트 — 자동 모드의 차례 순서 · 간격 · 상한 멈춤 · 건너뜀 · 멈춤. 스레드 없이 run_auto를 바로 부른다."""

from dataclasses import replace
from pathlib import Path

import pytest

from agent.auto import (
    CRASH_STOP,
    DONE,
    FAILED,
    QUOTA_STOP,
    SKIPPED,
    STOPPED,
    AutoDeps,
    AutoItem,
    AutoJob,
    run_auto,
)
from agent.config import load_settings
from agent.models import Draft, PostStatus
from agent.store import Store
from tests.conftest import GOOD_COMMENT, TEST_ID, at, video

ROOT = Path(__file__).resolve().parents[1]


class 가짜실행:
    """서버가 넘길 일들의 가짜. reject에 든 영상은 게시 검사에서 거부, fail_draft에 든 영상은 초안 실패."""

    def __init__(self, tmp_path, daily_max=10, reject=(), fail_draft=(), stop_after=None):
        base = load_settings(ROOT / "config.yaml", ROOT / "없는.env")
        self.settings = replace(base, db_path=tmp_path / "agent.db", daily_max=daily_max, auto_gap_seconds=60)
        self.store = Store(self.settings.db_path, self.settings.test_video_ids)
        self.reject, self.fail_draft, self.stop_after = set(reject), set(fail_draft), stop_after
        self.posted: list[str] = []
        self.waits: list[float] = []
        self.job: AutoJob | None = None

    def write_draft(self, settings, store, v):
        if v.id in self.fail_draft:
            raise RuntimeError("초안을 만들지 못했어요")
        return Draft(v.id, "고민", GOOD_COMMENT)

    def begin_post(self, settings, store, v, profile):
        if self.stop_after == v.id:  # 이 영상을 처리하는 동안 사용자가 [멈추기]를 누른다
            self.job.stop()
        aid = store.start_attempt(v, profile, GOOD_COMMENT, at(3), exempt=False)
        if v.id in self.reject:
            store.set_status(aid, PostStatus.REJECTED, at(3), reason="지정 채널 게시가 꺼져 있어요")
        else:
            store.claim(aid, at(3))
            store.set_status(aid, PostStatus.SUBMITTED, at(3))  # 로봇이 게시한 것처럼 — 하루 수에 든다
            store.set_status(aid, PostStatus.CONFIRMED, at(3), comment_url=f"https://youtu.be/{v.id}")
            self.posted.append(v.id)
        return aid

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.job.stopping

    def deps(self):
        return AutoDeps(open_store=lambda: (self.settings, self.store), write_draft=self.write_draft,
                        begin_post=self.begin_post, pick_profile=lambda s, st: "유튜브1",
                        wait_post=self.store.latest_in_root, wait=self.wait, now=lambda: at(3))

    def 돌려(self, *ids):
        self.job = AutoJob([AutoItem(video(i)) for i in ids])
        run_auto(self.job, self.deps())
        return [(i.video.id, i.state) for i in self.job.items]


@pytest.fixture
def 실행(tmp_path):
    return 가짜실행(tmp_path)


def 기능_자동모드_차례로_게시하고_사이에만_간격을_둔다(실행):
    """목적: 체크한 순서대로 게시하고, 간격은 게시한 영상 사이에만(마지막 뒤에는 없음) 둔다."""
    result = 실행.돌려("vid00000001", "vid00000002", "vid00000003")
    assert result == [("vid00000001", DONE), ("vid00000002", DONE), ("vid00000003", DONE)]
    assert 실행.waits == [60, 60]
    assert 실행.job.finished


def 기능_자동모드_하루상한에_닿으면_멈춘다(tmp_path):
    """목적: 오늘 상한(여기선 2개)에 닿으면 남은 차례는 게시하지 않고 「상한에 닿아 멈춤」으로 끝난다."""
    e = 가짜실행(tmp_path, daily_max=2)
    result = e.돌려("vid00000001", "vid00000002", "vid00000003")
    assert result[2] == ("vid00000003", STOPPED) and e.job.items[2].reason == QUOTA_STOP
    assert e.posted == ["vid00000001", "vid00000002"]


def 기능_자동모드_검사에_걸리면_건너뛰고_계속한다(tmp_path):
    """목적: 게시 검사에 걸린 영상은 이유와 함께 「건너뜀」이고, 간격 없이 다음 영상으로 간다."""
    e = 가짜실행(tmp_path, reject={"vid00000001"})
    result = e.돌려("vid00000001", "vid00000002")
    assert result == [("vid00000001", SKIPPED), ("vid00000002", DONE)]
    assert "채널 게시가 꺼져" in e.job.items[0].reason and e.waits == []


def 기능_자동모드_멈추면_남은_차례는_멈춤이다(tmp_path):
    """목적: 첫 영상을 게시한 뒤 [멈추기]를 누르면 간격을 기다리지 않고 남은 차례를 「멈춤」으로 끝낸다."""
    e = 가짜실행(tmp_path, stop_after="vid00000001")
    result = e.돌려("vid00000001", "vid00000002", "vid00000003")
    assert result == [("vid00000001", DONE), ("vid00000002", STOPPED), ("vid00000003", STOPPED)]
    assert e.posted == ["vid00000001"]
    # 간격이 없는 경우(건너뛴 영상 뒤)에도 다음 영상을 시작하지 않는다.
    skipped = 가짜실행(tmp_path / "2", reject={"vid00000001"}, stop_after="vid00000001")
    assert skipped.돌려("vid00000001", "vid00000002")[1] == ("vid00000002", STOPPED)


def 기능_자동모드_초안을_못쓰면_그_영상만_실패한다(tmp_path):
    """목적: 한 영상의 초안이 실패해도 그 영상만 「실패」+이유이고 다음 영상은 계속 간다."""
    e = 가짜실행(tmp_path, fail_draft={"vid00000001"})
    result = e.돌려("vid00000001", TEST_ID)
    assert result == [("vid00000001", FAILED), (TEST_ID, DONE)]
    assert "초안" in e.job.items[0].reason



def 기능_자동모드_차례_사이_오류는_남은_차례에_이유를_남긴다(tmp_path):
    """목적: 영상 처리 밖(설정 다시 읽기 등)에서 오류가 나도 남은 차례가 「대기」로 남지 않고 「실패」+이유로 끝난다."""
    e = 가짜실행(tmp_path)
    calls = {"n": 0}

    def 두번째에_고장(*_):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("config.yaml 고장")
        return e.settings, e.store

    e.job = AutoJob([AutoItem(video(i)) for i in ("vid00000001", "vid00000002")])
    run_auto(e.job, replace(e.deps(), open_store=두번째에_고장))
    assert [(i.state, i.reason) for i in e.job.items][1] == (FAILED, CRASH_STOP) and e.job.finished
