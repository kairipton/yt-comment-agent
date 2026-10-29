"""poster 테스트 — 게시 로봇의 흐름(재검사 · 자리 잡기 · 재시도 · 잠금 · 멈춘 줄)을 브라우저 없이 본다.

브라우저 자리(post)에는 정해진 결과를 내는 가짜를 넣는다. 기록장은 임시 폴더의 진짜 SQLite다.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from agent import guard
from agent import poster as poster_mod
from agent.config import load_settings
from agent.models import Draft, LoginState, PostStatus, Profile, Source
from agent.poster import (
    BUSY_REASON,
    CANCEL_REASON,
    STALE_REASON,
    PostFailed,
    _Lock,
    comment_url,
    launch_reason,
    run,
)
from agent.store import Store
from tests.conftest import CHANNEL_A, GOOD_COMMENT, TEST_ID, at, video, 가짜화면

ROOT = Path(__file__).resolve().parents[1]
URL = f"https://www.youtube.com/watch?v={TEST_ID}&lc=Ugx새댓글"


@pytest.fixture
def settings(tmp_path):
    """저장소 config.yaml을 읽고, 기록장·캡처 경로만 임시 폴더로 돌린다. 수동 모드 · 개발 모드 · 채널 게시 꺼짐."""
    base = load_settings(ROOT / "config.yaml", ROOT / "없는.env")
    return replace(base, db_path=tmp_path / "agent.db", screenshot_dir=tmp_path / "shots",
                   post_mode="manual", dev_mode=True, channel_posting=False, max_retries=2)


@pytest.fixture(autouse=True)
def 로그인(monkeypatch):
    """프로필 로그인 상태를 테스트가 정한다(실제 크롬 쿠키를 읽지 않게). 기본: 유튜브1만 로그인되어 있음."""
    states = {"유튜브1": LoginState.LOGGED_IN}
    monkeypatch.setattr(poster_mod, "login_state", lambda profile, now: states.get(profile.name, LoginState.NEEDS_LOGIN))
    return states


@pytest.fixture
def store(settings):
    return Store(settings.db_path, settings.test_video_ids)


def 테스트영상():
    return video(TEST_ID, channel_id="", source=Source.TEST)


def 준비(store, settings, v=None, text=GOOD_COMMENT, *, draft_text=None, issues=()) -> int:
    """초안을 저장하고 시도 한 줄을 만들어 번호를 돌려준다(서버가 ③에서 하는 일)."""
    v = v or 테스트영상()
    store.save_draft(Draft(v.id, "고민", draft_text or text, issues=issues), at(3))
    exempt = guard.is_exempt(v, set(settings.test_video_ids), settings.dev_mode)
    return store.start_attempt(v, "유튜브1", text, at(3), exempt=exempt)


class 가짜게시:
    """브라우저 대신 정해진 결과를 차례로 낸다. 버튼을 실제로 「눌렀는지」(clicked)를 센다."""

    def __init__(self, *outcomes, before_submit=None):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.clicked = 0
        self.before_submit = before_submit  # 버튼 직전에 끼어들 일(다른 프로세스 흉내)

    def __call__(self, profile, video_id, text, shots, on_status, on_typed):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        on_status(PostStatus.OPENING)
        on_status(PostStatus.TYPING)
        on_typed(min(15, len(text)))  # 진짜 로봇처럼 첫 15글자 묶음을 친 뒤 알린다
        if outcome == "버튼전실패":
            raise PostFailed("댓글 창을 찾지 못했어요.")
        if outcome == "취소":
            raise PostFailed(CANCEL_REASON, cancelled=True)
        if self.before_submit:
            self.before_submit()
        on_status(PostStatus.SUBMITTED)  # 진짜 로봇처럼: 자리 잡기가 거부되면 여기서 Rejected가 올라온다
        if outcome == "클릭오류":
            raise RuntimeError("클릭 중 오류")
        self.clicked += 1
        on_status(PostStatus.CONFIRMING)
        if outcome == "버튼후실패":
            raise PostFailed("게시 후 내 댓글을 확인하지 못했어요.")
        return URL


def 돌려(store, settings, aid, post) -> int:
    return run(aid, settings, store, post, lambda: at(3))


# ── 게시 전 재검사 ─────────────────────────────────────────────────────────────
def 기능_게시로봇_검사에_걸리면_브라우저를_열지_않는다(store, settings):
    """목적: 채널 게시 스위치가 꺼진 지정 채널 영상은 거부되고, 브라우저(post)는 한 번도 불리지 않는다."""
    aid = 준비(store, settings, video(channel_id=CHANNEL_A))
    post = 가짜게시("성공")
    assert 돌려(store, settings, aid, post) == 1
    assert (store.get(aid).status, post.calls) == (PostStatus.REJECTED, 0)


def 예외_게시로봇_초안과_글이_다르면_거부한다(store, settings):
    """목적: 저장된 초안과 다른 글(초안을 고른 뒤 바뀐 글)은 게시하지 않는다."""
    aid = 준비(store, settings, text=GOOD_COMMENT + " 진짜요", draft_text=GOOD_COMMENT)
    post = 가짜게시("성공")
    돌려(store, settings, aid, post)
    assert (store.get(aid).status, post.calls) == (PostStatus.REJECTED, 0)


def 예외_게시로봇_불합격_초안은_자동모드에서도_거부한다(store, settings):
    """목적: 자동 모드에서도 문지기 불합격 초안은 게시하지 않는다."""
    auto = replace(settings, post_mode="auto")
    aid = 준비(store, auto, issues=("해시태그가 들어 있어요.",))
    post = 가짜게시("성공")
    돌려(store, auto, aid, post)
    assert (store.get(aid).status, post.calls) == (PostStatus.REJECTED, 0)


def 기능_로봇_타이핑진행을_기록장에_적는다(store, settings):
    """목적: 브라우저가 알린 친 글자 수(on_typed)가 시도 줄에 남는다 — 화면 ③의 「86자 중 15자」."""
    aid = 준비(store, settings)
    # 첫 15글자를 친 뒤 창이 닫혀 끝난 줄 — 친 글자 수가 그대로 남아 있어야 한다.
    돌려(store, settings, aid, 가짜게시("취소"))
    assert store.get(aid).typed_chars == 15


# ── 자리 잡기 · 버튼 경계 ──────────────────────────────────────────────────────
def 기능_게시로봇_DB가_거부하면_버튼을_누르지_않는다(store, settings):
    """목적: 재검사 뒤 다른 프로세스가 같은 영상을 먼저 달았으면, 버튼 직전 자리 잡기에서 거부되고 버튼은 안 눌린다."""
    strict = replace(settings, dev_mode=False)  # 예외가 아니어야 영상당 1개가 걸린다
    aid = 준비(store, strict)

    def 남이_먼저_단다():
        other = store.start_attempt(테스트영상(), "유튜브2", "다른 글", at(3), exempt=False)
        store.set_status(other, PostStatus.SUBMITTED, at(3))

    post = 가짜게시("성공", before_submit=남이_먼저_단다)
    assert 돌려(store, strict, aid, post) == 1
    assert (store.get(aid).status, post.clicked) == (PostStatus.REJECTED, 0)


def 경계_게시로봇_자리잡은_뒤_실패는_버튼후_실패다(store, settings):
    """목적: SUBMITTED를 기록한 뒤에는 클릭 자체의 오류도 버튼 후 실패로 보고 재시도하지 않는다."""
    aid = 준비(store, settings)
    post = 가짜게시("클릭오류")
    돌려(store, settings, aid, post)
    assert (store.get(aid).status, post.calls) == (PostStatus.FAILED_AFTER_SUBMIT, 1)


# ── 재시도 ─────────────────────────────────────────────────────────────────────
def 기능_게시로봇_버튼전_실패는_새줄로_재시도한다(store, settings):
    """목적: 버튼 전 실패 뒤 새 줄(retry_no 1, 같은 root_id)로 다시 시도하고, 그 줄이 완료된다."""
    aid = 준비(store, settings)
    assert 돌려(store, settings, aid, 가짜게시("버튼전실패", "성공")) == 0
    latest = store.latest_in_root(aid)
    assert (store.get(aid).status, latest.id != aid, latest.retry_no, latest.status) == (
        PostStatus.FAILED_BEFORE_SUBMIT, True, 1, PostStatus.CONFIRMED)


def 경계_게시로봇_재시도는_2회까지만(store, settings):
    """목적: 계속 버튼 전에 실패하면 ③ 한 번에 총 3번(재시도 2회)에서 멈춘다(D-12)."""
    aid = 준비(store, settings)
    post = 가짜게시("버튼전실패")
    assert 돌려(store, settings, aid, post) == 1
    assert (post.calls, store.latest_in_root(aid).retry_no) == (3, 2)


def 기능_게시로봇_버튼후_실패는_재시도하지_않는다(store, settings):
    """목적: 버튼 뒤 확인 실패는 이미 달렸을 수 있어 다시 띄우지 않는다."""
    aid = 준비(store, settings)
    post = 가짜게시("버튼후실패")
    돌려(store, settings, aid, post)
    assert (store.get(aid).status, post.calls) == (PostStatus.FAILED_AFTER_SUBMIT, 1)


def 기능_게시로봇_창을_닫으면_재시도하지_않는다(store, settings):
    """목적: 사용자가 크롬 창을 닫으면 취소로 보고 창을 다시 띄우지 않는다 [사용자]."""
    aid = 준비(store, settings)
    post = 가짜게시("취소")
    돌려(store, settings, aid, post)
    got = store.get(aid)
    assert (got.status, got.reason, post.calls) == (PostStatus.FAILED_BEFORE_SUBMIT, CANCEL_REASON, 1)


# ── 성공 ───────────────────────────────────────────────────────────────────────
def 기능_게시로봇_성공하면_댓글링크가_남는다(store, settings):
    """목적: 게시가 확인되면 완료 상태와 댓글 링크가 기록장에 남는다."""
    aid = 준비(store, settings)
    assert 돌려(store, settings, aid, 가짜게시("성공")) == 0
    got = store.get(aid)
    assert (got.status, got.comment_url) == (PostStatus.CONFIRMED, URL)


# ── 한 번에 하나 · 멈춘 줄 ─────────────────────────────────────────────────────
def 예외_게시로봇_이미_잡힌_시도는_건드리지_않는다(store, settings):
    """목적: 끝난 시도를 다시 띄워도 아무것도 쓰지 않는다 — 완료 위에 거부가 덮이지 않는다."""
    aid = 준비(store, settings)
    돌려(store, settings, aid, 가짜게시("성공"))
    again = 가짜게시("성공")
    assert 돌려(store, settings, aid, again) == 2
    assert (store.get(aid).status, again.calls) == (PostStatus.CONFIRMED, 0)


def 예외_게시로봇_다른_게시가_진행중이면_거부한다(store, settings):
    """목적: 다른 로봇이 잠금을 쥐고 있으면 이 시도는 브라우저를 열지 않고 거부된다."""
    aid = 준비(store, settings)
    other = _Lock(settings.db_path.parent / "poster.lock")
    assert other.acquire()  # 다른 로봇 흉내
    try:
        post = 가짜게시("성공")
        돌려(store, settings, aid, post)
    finally:
        other.release()
    got = store.get(aid)
    assert (got.status, got.reason, post.calls) == (PostStatus.REJECTED, BUSY_REASON, 0)


def 기능_게시로봇_멈춘_줄을_실패로_끝낸다(store, settings):
    """목적: 앞선 로봇이 죽어 「입력 중」으로 남은 줄은 다음 로봇이 잠금을 잡은 뒤 실패로 끝낸다."""
    stale = store.start_attempt(video("vid00000009", channel_id=CHANNEL_A), "유튜브1", "댓글", at(2), exempt=False)
    store.claim(stale, at(2))
    store.set_status(stale, PostStatus.TYPING, at(2))
    돌려(store, settings, 준비(store, settings), 가짜게시("성공"))
    got = store.get(stale)
    assert (got.status, got.reason) == (PostStatus.FAILED_BEFORE_SUBMIT, STALE_REASON)


# ── 브라우저 쪽 작은 함수 ──────────────────────────────────────────────────────
def 기능_실행오류_프로필창이_열려있으면_그렇게_알린다():
    """목적: 같은 프로필 크롬 창이 열려 있을 때 나는 오류를 「프로필 창이 이미 열려 있어요」로 바꾼다."""
    message = "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n기존 브라우저 세션에서 여는 중입니다."
    assert "이미 열려" in launch_reason(message)


def 예외_게시로봇_크롬을_못띄우면_바탕화면을_찍는다(tmp_path, monkeypatch):
    """목적: 프로필 창이 이미 열려 있는 등으로 크롬을 못 띄우면, 찍을 브라우저 화면이 없으니 바탕화면을 찍어 실패에 싣는다."""
    from playwright.sync_api import Error as PlaywrightError

    class 가짜크로미움:
        def launch_persistent_context(self, *args, **kwargs):
            raise PlaywrightError("Target page, context or browser has been closed — 기존 브라우저 세션에서 여는 중입니다.")

    class 가짜플레이라이트:  # with sync_playwright() as p: 꼴을 흉내 낸다 — 진짜 크롬은 열지 않는다
        chromium = 가짜크로미움()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(poster_mod, "sync_playwright", 가짜플레이라이트)
    monkeypatch.setattr(poster_mod, "_grab_desktop", 가짜화면)  # 실제 화면 대신
    prefix = tmp_path / "shots" / "attempt-7"
    with pytest.raises(PostFailed) as e:
        poster_mod.post_comment(Profile("유튜브1", tmp_path / "yt1"), TEST_ID, GOOD_COMMENT, prefix,
                                lambda status: None, lambda count: None)
    assert (e.value.screenshot, e.value.screenshot.is_file()) == (tmp_path / "shots" / "attempt-7-실행.png", True)


def 예외_바탕화면캡처_실패하면_없음을_돌려준다(tmp_path, monkeypatch):
    """목적: 바탕화면을 찍지 못해도(화면 장치 문제 등) 예외로 원래 실패 이유를 가리지 않고 캡처 없음(None)으로 끝난다."""
    def 고장():
        raise OSError("화면을 찍을 수 없음")
    monkeypatch.setattr(poster_mod, "_grab_desktop", 고장)
    assert poster_mod.desktop_shot(tmp_path / "attempt-1", "실행") is None


def 기능_댓글링크_시각링크에서_lc를_뽑는다():
    """목적: 댓글 시각 링크의 lc 값으로 그 댓글 하나를 가리키는 링크를 만든다. lc가 없으면 None."""
    assert comment_url(f"/watch?v={TEST_ID}&lc=UgxAbC123", TEST_ID) == (
        f"https://www.youtube.com/watch?v={TEST_ID}&lc=UgxAbC123")
    assert comment_url(f"/watch?v={TEST_ID}", TEST_ID) is None


def 예외_게시로봇_개발모드를_끄면_옛_예외표시를_거부한다(store, settings):
    """목적: 개발 모드에서 만든 시도(예외 표시)를 개발 모드를 끈 뒤 띄우면, 예외를 쓰지 않고 거부한다 — 실제로 쓰는 중에 둘 달리지 않게."""
    aid = 준비(store, settings)  # dev_mode=True → 예외 표시가 붙는다
    assert store.get(aid).exempt
    post = 가짜게시("성공")
    돌려(store, replace(settings, dev_mode=False), aid, post)
    assert (store.get(aid).status, post.calls) == (PostStatus.REJECTED, 0)


def 기능_게시로봇_초기화_뒤에는_상한을_다시_센다(store, settings):
    """목적: 오늘 상한에 닿았어도 초기화 뒤라면 게시 로봇의 재검사도 통과한다 — 서버와 로봇이 같은 구간으로 센다."""
    settings = replace(settings, daily_max=1)
    first = store.start_attempt(테스트영상(), "유튜브1", "먼저 단 댓글", at(1), exempt=True)
    store.set_status(first, PostStatus.SUBMITTED, at(1))  # 오늘 1개 → 상한에 닿음
    store.reset_quota(at(2))
    post = 가짜게시("성공")
    돌려(store, settings, 준비(store, settings), post)
    assert post.calls == 1


def 예외_게시검사_설정에_없는_프로필은_거부한다(store, settings):
    """목적: 설정에 없는 프로필 이름으로는 게시하지 않는다 — 어느 계정으로 달지 모르는 게시를 막는다."""
    v = 테스트영상()
    store.save_draft(Draft(v.id, "고민", GOOD_COMMENT), at(3))
    verdict = poster_mod.check_post(v, GOOD_COMMENT, "없는프로필", True, settings, store, at(3))
    assert any("설정에 없는 프로필" in r for r in verdict.reasons)


class 가짜댓글창:
    """브라우저 없이 댓글 확인을 흉내 낸다. evaluate는 늘 같은 댓글 목록([글, 시각 링크])을 돌려주고, 기다리기는 바로 끝난다."""

    def __init__(self, threads):
        self.threads = threads

    def evaluate(self, _script):
        return self.threads

    def wait_for_timeout(self, _ms):
        pass


def 예외_댓글확인_전부터_있던_같은글은_새댓글이_아니다():
    """목적: 같은 글이 게시 전부터 달려 있으면 그것을 새 댓글로 보지 않는다 — 이번 게시가 실패했는데 「완료」로 적히는 것(거짓 성공)을 막는다."""
    old = [[GOOD_COMMENT, f"/watch?v={TEST_ID}&lc=옛댓글"]]
    with pytest.raises(poster_mod.PlaywrightTimeout):
        poster_mod._wait_new_comment(가짜댓글창(old), GOOD_COMMENT, old, TEST_ID)


def 기능_댓글확인_새로_생긴_같은글의_링크를_돌려준다():
    """목적: 게시 전에 없던 같은 글이 나타나면 그 댓글의 링크(lc)를 돌려준다 — 옛 댓글이 아니라 새 댓글을 가리킨다."""
    old = [[GOOD_COMMENT, f"/watch?v={TEST_ID}&lc=옛댓글"]]
    now = old + [[GOOD_COMMENT, f"/watch?v={TEST_ID}&lc=새댓글"]]
    url = poster_mod._wait_new_comment(가짜댓글창(now), GOOD_COMMENT, old, TEST_ID)
    assert url == f"https://www.youtube.com/watch?v={TEST_ID}&lc=새댓글"


def 예외_게시로봇_로그인이_필요한_프로필은_거부한다(store, settings):
    """목적: 지금 유튜브에 로그인되어 있지 않은 프로필(유튜브2)로 만든 시도는 크롬을 열지 않고 거부한다."""
    v = 테스트영상()
    store.save_draft(Draft(v.id, "고민", GOOD_COMMENT), at(3))
    aid = store.start_attempt(v, "유튜브2", GOOD_COMMENT, at(3), exempt=True)
    post = 가짜게시("성공")
    돌려(store, settings, aid, post)
    got = store.get(aid)
    assert (got.status, post.calls) == (PostStatus.REJECTED, 0) and "로그인" in got.reason


def 예외_게시로봇_프로필창이_열려있으면_거부한다(store, settings, 로그인):
    """목적: 그 프로필의 크롬 창이 열려 있으면(로그인 확인 불가) 크롬을 열지 않고 「창을 닫아 달라」로 거부한다 — 열면 어차피 실패한다."""
    로그인["유튜브1"] = LoginState.WINDOW_OPEN
    aid = 준비(store, settings)
    post = 가짜게시("성공")
    돌려(store, settings, aid, post)
    got = store.get(aid)
    assert (got.status, post.calls) == (PostStatus.REJECTED, 0) and "창" in got.reason
