"""서버(app.py) 테스트 — 화면이 부르는 API가 부품을 제대로 잇는지. YouTube·작가·게시 로봇은 가짜를 넣는다.

기록장은 임시 폴더의 진짜 SQLite다. 네트워크·브라우저·LLM은 부르지 않는다.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import poster as poster_mod
from agent.config import load_settings
from agent.draft import DraftResult
from agent.fetch import Measured, VideoList
from agent.models import Draft, LoginState, PostStatus, Source, Verdict, VideoContext
from agent.store import Store
from app import RESTARTED, ROBOT_DIED, Services, create_app
from tests.conftest import CHANNEL_A, CHANNEL_X, GOOD_COMMENT, TEST_ID, at, video, 가짜화면

ROOT = Path(__file__).resolve().parents[1]
TEST_VIDEO = video(TEST_ID, channel_id="", source=Source.TEST)
CHANNEL_VIDEO = video("vid00000001", channel_id=CHANNEL_A)


class 가짜유튜브:
    def test_videos(self, ids):
        return VideoList([TEST_VIDEO], {})

    def latest_videos(self, channels, per_channel):
        return VideoList([CHANNEL_VIDEO], {"리즈시절 LizTV": "피드를 읽지 못했어요"})

    def videos_by_id(self, ids, source):
        if ids == ["abcdefghijk"]:  # 허용 채널 밖의 남의 영상
            return VideoList([video("abcdefghijk", channel_id=CHANNEL_X, source=source)], {})
        return VideoList([], {i: "영상을 찾지 못함(삭제·비공개?)" for i in ids})

    def context(self, v):
        return VideoContext(v, "자막", ())

    measured = 0  # 몇 번 쟀나 — 두 번째부터 조회수가 늘어난다

    def search_recent(self, keyword, published_after):
        return ["rise0000001", "rise0000002", "toomany0001"]

    def measure(self, ids, now):
        # 올린 지 5시간: 2,000뷰(시간당 400) · 1,000뷰(시간당 200) · 9,000뷰(기준 밖). 다시 재면 느린 쪽이 더 많이 는다.
        self.measured += 1
        grow = {"rise0000001": 50, "rise0000002": 500, "toomany0001": 0} if self.measured > 1 else {}
        base = {"rise0000001": 2000, "rise0000002": 1000, "toomany0001": 9000}
        return [Measured(replace(video(i), source=Source.RISING, published_at=at(0)), base[i] + grow.get(i, 0), now)
                for i in ids]


class 가짜작가:
    """늘 같은 합격 초안을 쓴다. 몇 번 불렸는지 센다."""

    def __init__(self):
        self.calls = 0

    def write(self, ctx, others, version):
        self.calls += 1
        return DraftResult(Draft(ctx.video.id, "고민", GOOD_COMMENT, version=version), Verdict.passed(), 1)


class 가짜로봇:
    """게시 로봇 프로세스 흉내. alive가 참이면 아직 도는 중."""

    def __init__(self, alive=True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 0

    def wait(self):
        self.alive = False


class 환경:
    """서버 하나와 그 서버가 쓰는 가짜 부품들."""

    def __init__(self, tmp_path, ai_key="가짜키", channel_posting=False, robot_alive=True):
        base = load_settings(ROOT / "config.yaml", ROOT / "없는.env")
        self.settings = replace(base, db_path=tmp_path / "agent.db", screenshot_dir=tmp_path / "shots",
                                post_mode="manual", dev_mode=True, channel_posting=channel_posting,
                                anthropic_api_key=ai_key, youtube_api_key="가짜유튜브키")
        self.store = Store(self.settings.db_path, self.settings.test_video_ids)
        self.writer = 가짜작가()
        self.spawned: list[int] = []
        self.robot = 가짜로봇(robot_alive)
        self.yt = 가짜유튜브()
        self.clock = at(5)
        self.background: list = []  # 자동 실행 — 테스트가 직접 돌린다(스레드 없음)
        self.spawn_error: Exception | None = None  # 넣어 두면 로봇 띄우기가 이 오류로 실패한다

        def spawn(aid):
            if self.spawn_error is not None:
                raise self.spawn_error
            self.spawned.append(aid)
            return self.robot

        # 프로필 관리: 크롬 창은 열지 않고 연 프로필 이름만 모은다. 로그인 상태는 테스트가 정한다(로그인 상태 고정 장치).
        self.opened: list[str] = []
        # 옵션 페이지가 읽고 쓰는 설정 파일 — 저장소 설정과 지침을 임시 폴더에 복사해 쓴다.
        self.config = tmp_path / "cfg" / "config.yaml"
        (tmp_path / "cfg" / "prompts").mkdir(parents=True)
        self.config.write_bytes((ROOT / "config.yaml").read_bytes())
        (tmp_path / "cfg" / "prompts" / "comment_system.md").write_bytes((ROOT / "prompts" / "comment_system.md").read_bytes())

        services = Services(settings=lambda: self.settings, youtube=lambda s: self.yt,
                            drafter=lambda s: self.writer, spawn=spawn, now=lambda: self.clock,
                            run_background=lambda work: self.background.append(work),
                            open_profile=lambda p: self.opened.append(p.name), config_path=self.config,
                            logins=lambda profiles: {p.name: 로그인상태.get(p.name, LoginState.NEEDS_LOGIN) for p in profiles})
        self.client = TestClient(create_app(services))
        self.client.__enter__()  # 시작 정리(lifespan)를 돌린다
        self.client.post("/api/videos/load")

    def 초안(self, video_id=TEST_ID):
        """② 영상을 고른다 — 초안이 생긴다(승인 단계는 없다)."""
        return self.client.get(f"/api/videos/{video_id}/draft")


# 테스트가 정하는 프로필 로그인 상태 — 서버의 상태 표시(Services.logins)와 게시 검사(poster.login_state)가 같이 본다.
로그인상태: dict[str, LoginState] = {}


@pytest.fixture(autouse=True)
def 로그인_고정(monkeypatch):
    """기본: 유튜브1만 로그인되어 있음. 실제 크롬 쿠키를 읽지 않게 게시 검사의 로그인 확인도 바꿔 끼운다."""
    로그인상태.clear()
    로그인상태["유튜브1"] = LoginState.LOGGED_IN
    monkeypatch.setattr(poster_mod, "login_state",
                        lambda profile, now: 로그인상태.get(profile.name, LoginState.NEEDS_LOGIN))


@pytest.fixture
def env(tmp_path):
    e = 환경(tmp_path)
    yield e
    e.client.__exit__(None, None, None)


# ── ① 불러오기 ────────────────────────────────────────────────────────────────
def 기능_서버_불러오기_테스트영상이_맨뒤이고_실패채널을_알린다(env):
    """목적: 테스트 영상은 연습용이라 목록 맨 뒤에 오고, 못 불러온 채널은 이름과 함께 알린다."""
    body = env.client.post("/api/videos/load").json()
    assert body["videos"][-1]["id"] == TEST_ID and body["videos"][-1]["test"]
    assert any("리즈시절" in f for f in body["failures"])


def 기능_서버_불러오기_게시대상_여부는_문지기_판정이다(env):
    """목적: 채널 게시 스위치가 꺼져 있으면 지정 채널 영상은 「게시 대상 아님」+이유, 테스트 영상은 대상이다."""
    rows = {v["id"]: v for v in env.client.post("/api/videos/load").json()["videos"]}
    assert (rows[TEST_ID]["postable"], rows["vid00000001"]["postable"]) == (True, False)
    assert "채널 게시가 꺼져" in rows["vid00000001"]["block_reason"]


# ── ② 초안 ────────────────────────────────────────────────────────────────────
def 기능_서버_AI키가_없으면_초안_대신_키없음을_돌려준다(tmp_path):
    """목적: AI 키가 없으면 저장된 초안이 있어도 보여 주지 않고 「키 없음」만 돌려준다(D-28)."""
    e = 환경(tmp_path, ai_key=None)
    e.store.save_draft(Draft(TEST_ID, "고민", GOOD_COMMENT), at(3))  # 키가 있던 때 저장된 초안
    try:
        assert e.client.get(f"/api/videos/{TEST_ID}/draft").json() == {"video_id": TEST_ID, "ai": False}
        assert e.client.post(f"/api/videos/{TEST_ID}/rewrite").status_code == 409
    finally:
        e.client.__exit__(None, None, None)


def 기능_서버_저장된_초안이_있으면_다시_쓰지_않는다(env):
    """목적: 같은 영상을 다시 골라도 작가를 다시 부르지 않고 저장된 초안을 보여 준다(비용·일관성)."""
    env.client.get(f"/api/videos/{TEST_ID}/draft")
    env.client.get(f"/api/videos/{TEST_ID}/draft")
    assert env.writer.calls == 1


# ── ③ 게시 ────────────────────────────────────────────────────────────────────
def 기능_서버_게시는_시도를_만들고_로봇을_띄운다(env):
    """목적: 검사를 통과하면 시도 줄을 만들고(개발 모드의 테스트 영상은 예외 표시) 게시 로봇을 그 번호로 띄운다."""
    env.초안()
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    got = env.store.get(aid)
    assert (env.spawned, got.status, got.exempt) == ([aid], PostStatus.QUEUED, True)


def 기능_서버_검사에_걸리면_로봇을_띄우지_않고_거부를_남긴다(env):
    """목적: 규칙 검사에 떨어진 초안으로 게시를 누르면 로봇을 띄우지 않고, 그 시도가 이유와 함께 「거부」로 기록된다."""
    env.store.save_draft(Draft(TEST_ID, "고민", "#광고 댓글", issues=("해시태그가 들어 있어요.",)), at(3))
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    got = env.store.get(aid)
    assert (env.spawned, got.status) == ([], PostStatus.REJECTED)
    assert "규칙을 통과하지 못한 초안" in got.reason


def 기능_서버_진행상태는_재시도_줄을_따라간다(env):
    """목적: 첫 시도 번호로 물으면 재시도 줄(가장 최근 줄)의 상태가 나온다."""
    env.초안()
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    # 로봇이 한 일 흉내: 첫 줄은 버튼 전 실패, 재시도 줄은 입력 중
    env.store.claim(aid, at(3))
    env.store.set_status(aid, PostStatus.FAILED_BEFORE_SUBMIT, at(3), reason="댓글 창을 찾지 못했어요.")
    retry = env.store.start_attempt(TEST_VIDEO, "유튜브1", GOOD_COMMENT, at(3), exempt=True, retry_no=1, root_id=aid)
    env.store.claim(retry, at(3))
    env.store.set_status(retry, PostStatus.TYPING, at(3))
    body = env.client.get(f"/api/posts/{aid}").json()
    assert (body["id"], body["retry_no"], body["status"], body["done"]) == (retry, 1, "typing", False)


def 경계_서버_로봇이_살아있으면_실패줄이어도_끝나지_않았다(env):
    """목적: 재시도 줄이 생기기 직전(최신 줄이 버튼 전 실패) 로봇이 살아 있으면 「끝남」으로 보지 않는다 — 화면이 진행 확인을 멈추지 않게."""
    env.초안()
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    env.store.claim(aid, at(3))
    env.store.set_status(aid, PostStatus.FAILED_BEFORE_SUBMIT, at(3), reason="댓글 창을 찾지 못했어요.")
    alive = env.client.get(f"/api/posts/{aid}").json()["done"]
    env.robot.alive = False
    assert (alive, env.client.get(f"/api/posts/{aid}").json()["done"]) == (False, True)


def 기능_서버_로봇이_못뜨면_대기줄을_실패로_끝낸다(tmp_path):
    """목적: 로봇이 시도를 잡기 전에 끝나 버리면, 대기 줄을 「게시 로봇이 시작하지 못했어요」로 끝낸다."""
    e = 환경(tmp_path, robot_alive=False)
    try:
        e.초안()
        aid = e.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
        body = e.client.get(f"/api/posts/{aid}").json()
        assert (body["status"], body["reason"], body["done"]) == ("failed_before_submit", ROBOT_DIED, True)
    finally:
        e.client.__exit__(None, None, None)


def 기능_서버_시작할때_남은_대기줄을_끝낸다(tmp_path):
    """목적: 서버가 다시 시작되면 지난번에 남은 대기 줄이 「취소」로 끝난다 — 영원히 「대기 중」이 남지 않게."""
    store = Store(tmp_path / "agent.db", {TEST_ID})
    left = store.start_attempt(TEST_VIDEO, "유튜브1", GOOD_COMMENT, at(2), exempt=True)
    e = 환경(tmp_path)  # 같은 기록장으로 서버를 새로 켠다
    try:
        got = e.store.get(left)
        assert (got.status, got.reason) == (PostStatus.FAILED_BEFORE_SUBMIT, RESTARTED)
    finally:
        e.client.__exit__(None, None, None)


# ── 링크 입력 ───────────────────────────────────────────────────────────
def 기능_서버_링크로_넣은_영상은_초안을_쓰고_허용범위를_본다(env):
    """목적: 링크로 넣은 남의 채널 영상은 목록에 들어가 초안을 쓸 수 있지만, 「허용된 채널이 아님」으로 게시 대상이 아니다."""
    v = env.client.post("/api/videos/link", json={"url": "https://youtu.be/abcdefghijk?si=x"}).json()
    assert (v["id"], v["link"], v["postable"]) == ("abcdefghijk", True, False)
    assert "허용된 채널" in v["block_reason"]
    assert env.client.get("/api/videos/abcdefghijk/draft").json()["ai"] is True


def 예외_서버_영상링크가_아니면_400이다(env):
    """목적: 채널 주소 같은 영상 링크가 아닌 글은 400으로 거부하고 이유를 알린다."""
    res = env.client.post("/api/videos/link", json={"url": "https://www.youtube.com/@example_channel"})
    assert res.status_code == 400 and "영상 링크가 아니에요" in res.json()["detail"]


# ── 프로필 배정 ─────────────────────────────────────────────────────────
def 기능_서버_자동배정이면_서버가_프로필을_정한다(tmp_path):
    """목적: 자동 배정(rotate)이면 요청의 프로필을 무시하고, 로그인함 프로필 중 가장 오래 안 쓴 것으로 시도를 만든다."""
    e = 환경(tmp_path)
    base = e.settings
    로그인상태.update({"유튜브2": LoginState.LOGGED_IN, "유튜브3": LoginState.LOGGED_IN})  # 셋 다 로그인되어 있다고 치고
    e.settings = replace(base, profile_assign="rotate")
    try:
        e.초안()
        assert e.client.get("/api/status").json()["next_profile"] == "유튜브1"
        aid = e.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브3"}).json()["attempt_id"]
        assert e.store.get(aid).profile == "유튜브1"
    finally:
        e.client.__exit__(None, None, None)


def 예외_서버_로그인함이_꺼진_프로필은_400이다(env):
    """목적: 직접 고르기에서 로그인함 표시가 꺼진 프로필(유튜브2)을 보내면 시도를 만들지 않고 400으로 거부한다."""
    env.초안()
    res = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브2"})
    assert res.status_code == 400 and env.store.history() == []
    assert env.client.get("/api/status").json()["profiles"] == ["유튜브1"]


# ── 화면 B안이 쓰는 값 ─────────────────────────────────────────────────────────
def 기능_서버_목록에_조회수와_출처를_담는다(env):
    """목적: 목록 한 줄에 조회수(없으면 null)와 출처가 담긴다 — 화면 ①의 종류 탭과 ②의 「조회수 n회」가 쓴다."""
    env.yt.latest_videos = lambda channels, n: VideoList([replace(CHANNEL_VIDEO, view_count=32150)], {})
    rows = {v["id"]: (v["source"], v["view_count"]) for v in env.client.post("/api/videos/load").json()["videos"]}
    assert rows == {TEST_ID: ("test", None), "vid00000001": ("channel", 32150)}


def 기능_서버_초안에_읽은양을_담는다(env):
    """목적: 초안 응답에 AI가 읽은 양(자막 글자 수 · 인기 댓글 수)이 그대로 담긴다 — 화면 ②의 「AI가 읽은 것」."""
    env.store.save_draft(Draft(TEST_ID, "고민", GOOD_COMMENT, transcript_chars=3214, comments_read=20), at(3))
    body = env.초안().json()
    assert (body["transcript_chars"], body["comments_read"]) == (3214, 20)


def 기능_서버_진행상태에_친글자수와_갈래를_담는다(env):
    """목적: 진행 응답에 친 글자 수 · 전체 글자 수 · 글 · 갈래(tone)가 담긴다 — 화면 ③의 「86자 중 45자」와 색."""
    env.초안()
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    env.store.claim(aid, at(3))
    env.store.set_status(aid, PostStatus.TYPING, at(3))
    env.store.set_typed(aid, 45, at(3))
    body = env.client.get(f"/api/posts/{aid}").json()
    assert (body["typed"], body["total"], body["text"], body["tone"]) == (45, len(GOOD_COMMENT), GOOD_COMMENT, "progress")


def 기능_서버_거부면_먼저단댓글을_담는다(env):
    """목적: 이미 단 영상이라 거부되면 그 영상에 먼저 단 댓글(시각 · 프로필 · 글 · 링크)이 진행 응답에 담긴다."""
    env.settings = replace(env.settings, dev_mode=False)  # 예외가 아니어야 영상당 1개가 걸린다
    env.초안()
    first = env.store.start_attempt(TEST_VIDEO, "유튜브1", GOOD_COMMENT, at(3), exempt=False)
    env.store.set_status(first, PostStatus.CONFIRMED, at(3), comment_url="https://youtu.be/x&lc=1")
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    body = env.client.get(f"/api/posts/{aid}").json()
    prev = body["previous"]
    assert (body["tone"], prev["profile"], prev["comment"], prev["comment_url"]) == (
        "blocked", "유튜브1", GOOD_COMMENT, "https://youtu.be/x&lc=1")


def 기능_서버_상태에_전체프로필과_로그인상태를_담는다(env):
    """목적: 화면 표시용 전체 프로필(로그인 상태 포함)을 내되, 고를 수 있는 목록은 지금 로그인되어 있는 프로필만이다."""
    로그인상태["유튜브3"] = LoginState.WINDOW_OPEN
    body = env.client.get("/api/status").json()
    assert body["all_profiles"] == [{"name": "유튜브1", "login": "logged_in"}, {"name": "유튜브2", "login": "needs_login"},
                                    {"name": "유튜브3", "login": "window_open"}]
    assert body["profiles"] == ["유튜브1"]

def 기능_서버_개발모드면_오늘수를_초기화한다(env):
    """목적: 개발 모드에서 [초기화]를 누르면 오늘 게시 수가 0이 된다(기록은 남는다)."""
    aid = env.store.start_attempt(TEST_VIDEO, "유튜브1", GOOD_COMMENT, at(3), exempt=True)
    env.store.set_status(aid, PostStatus.SUBMITTED, at(3))
    assert env.client.get("/api/status").json()["today"] == 1
    assert env.client.post("/api/quota/reset").status_code == 200
    assert env.client.get("/api/status").json()["today"] == 0


def 예외_서버_개발모드가_아니면_초기화하지_않는다(env):
    """목적: 개발 모드가 꺼져 있으면 초기화를 409로 거부하고 오늘 수는 그대로다 — 하루 상한은 운영 규칙이다."""
    env.settings = replace(env.settings, dev_mode=False)
    aid = env.store.start_attempt(TEST_VIDEO, "유튜브1", GOOD_COMMENT, at(3), exempt=False)
    env.store.set_status(aid, PostStatus.SUBMITTED, at(3))
    res = env.client.post("/api/quota/reset")
    assert (res.status_code, env.client.get("/api/status").json()["today"]) == (409, 1)


def 기능_서버_프로필_크롬을_연다(env):
    """목적: [크롬 열기]를 누르면 그 프로필(로그인 전이어도)의 크롬 창을 연다 — 로그인하러 가는 길."""
    res = env.client.post("/api/profiles/유튜브2/open")
    assert (res.status_code, env.opened) == (200, ["유튜브2"])


def 예외_서버_없는_프로필은_열지_않는다(env):
    """목적: 설정에 없는 프로필 이름이면 404이고 크롬을 열지 않는다 — 아무 폴더로나 크롬을 띄우지 못하게."""
    res = env.client.post("/api/profiles/유튜브9/open")
    assert (res.status_code, env.opened) == (404, [])


def 예외_서버_초안이_없으면_게시하지_않는다(env):
    """목적: 초안 없이 [댓글 달기]가 오면 409로 거부하고 시도 줄도 만들지 않는다 — 검사되지 않은 글이 게시되지 않게."""
    res = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"})
    assert (res.status_code, env.store.history()) == (409, [])


def 예외_서버_로봇을_못띄우면_버튼전_실패로_끝낸다(env):
    """목적: 게시 로봇 프로세스를 못 띄우면 시도를 「버튼 전 실패」로 끝낸다 — 대기 줄이 남아 진행이 멈춘 것처럼 보이지 않게."""
    env.초안()
    env.spawn_error = OSError("실행 파일 없음")
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    assert env.store.get(aid).status == PostStatus.FAILED_BEFORE_SUBMIT


def 예외_서버_로봇을_못띄우면_바탕화면을_찍는다(env, monkeypatch):
    """목적: 게시 로봇을 못 띄운 실패에도 바탕화면 캡처를 남긴다 — 실패하면 이유와 화면 캡처."""
    env.초안()
    env.spawn_error = OSError("실행 파일 없음")
    monkeypatch.setattr(poster_mod, "_grab_desktop", 가짜화면)  # 실제 화면 대신
    aid = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
    shot = env.store.get(aid).screenshot
    assert (shot.name, shot.is_file()) == (f"attempt-{aid}-실행.png", True)


def 예외_서버_게시중이면_프로필창을_열지_않는다(env):
    """목적: 게시 로봇이 도는 중이면 409 — 같은 프로필 폴더를 둘이 열면 게시가 실패한다."""
    env.초안()
    env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"})  # 로봇이 살아 있다(가짜)
    res = env.client.post("/api/profiles/유튜브1/open")
    assert (res.status_code, env.opened) == (409, [])


def 기능_서버_로그인상태_감시는_확인결과를_돌려준다(env):
    """목적: 화면이 몇 초마다 묻는 로그인 상태가 확인 결과 그대로다 — 로그인하고 창을 닫으면 다음 물음에서 바뀐다."""
    before = env.client.get("/api/profiles").json()
    로그인상태["유튜브2"] = LoginState.LOGGED_IN  # 사용자가 유튜브2에 로그인하고 창을 닫았다
    after = env.client.get("/api/profiles").json()
    assert (before["profiles"], after["profiles"]) == (["유튜브1"], ["유튜브1", "유튜브2"])


def 예외_서버_로그인한_프로필이_없으면_게시하지_않는다(env):
    """목적: 로그인된 프로필이 하나도 없으면 게시를 400으로 거부하고 시도를 만들지 않는다(자동 배정도 같다)."""
    로그인상태.clear()
    env.초안()
    manual = env.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"})
    env.settings = replace(env.settings, profile_assign="rotate")
    rotate = env.client.post("/api/posts", json={"video_id": TEST_ID})
    status = env.client.get("/api/status").json()
    assert (manual.status_code, rotate.status_code, env.store.history()) == (400, 400, [])
    assert (status["profiles"], status["next_profile"]) == ([], None)


# ── 옵션 페이지 ───────────────────────────────────────────────────────────────
def 기능_서버_옵션을_읽어준다(env):
    """목적: 옵션 페이지가 그릴 설정 값 · 댓글 작성 지침 · 쓸 수 있는 자리표시를 돌려준다."""
    body = env.client.get("/api/options").json()
    assert body["values"]["limits"]["daily_max_comments"] == 10
    assert "{brand}" in body["prompt"] and "brand" in body["prompt_fields"]


def 기능_서버_옵션을_저장하면_다음_요청에_반영된다(env):
    """목적: 옵션을 저장하면 설정 파일이 바뀌어 다음에 읽을 때 그 값이다."""
    values = env.client.get("/api/options").json()["values"]
    values["limits"]["daily_max_comments"] = 5
    res = env.client.put("/api/options", json={"values": values})
    after = env.client.get("/api/options").json()["values"]["limits"]["daily_max_comments"]
    assert (res.status_code, after) == (200, 5)


def 예외_서버_잘못된_옵션은_409와_이유다(env):
    """목적: 검사에 걸리는 옵션은 409 + 이유 목록이고, 설정 파일은 그대로다."""
    original = env.config.read_bytes()
    values = env.client.get("/api/options").json()["values"]
    values["comment"]["min_chars"] = 500  # 최대보다 큰 최소
    res = env.client.put("/api/options", json={"values": values})
    assert res.status_code == 409 and res.json()["reasons"]
    assert env.config.read_bytes() == original


def 예외_서버_다른_사이트에서_온_요청은_막는다(env):
    """목적: 다른 사이트(Origin이 다름)가 몰래 보낸 상태 변경 요청은 403 — 설정이 바뀌지 않는다."""
    original = env.config.read_bytes()
    values = env.client.get("/api/options").json()["values"]
    res = env.client.put("/api/options", json={"values": values}, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403 and env.config.read_bytes() == original


def 기능_서버_대시보드에서_온_요청은_받는다(env):
    """목적: 대시보드 페이지(Origin = 이 서버 주소)에서 온 요청은 그대로 받는다."""
    values = env.client.get("/api/options").json()["values"]
    res = env.client.put("/api/options", json={"values": values}, headers={"Origin": "http://localhost:8501"})
    assert res.status_code == 200


# ── 뜨기 직전 영상 찾기 ─────────────────────────────────────────────────
def 기능_서버_뜨기직전은_기준에_드는_것만_속도순이고_게시대상이_아니다(env):
    """목적: 기준(1,000~5,000 · 시간당 150) 밖은 빠지고, 평균 속도 큰 순이며, 뜨기 직전 영상은 게시 대상이 아니다(D-22)."""
    body = env.client.post("/api/rising/search").json()
    assert [v["id"] for v in body["videos"]] == ["rise0000001", "rise0000002"]
    assert (body["searched"], body["videos"][0]["per_hour"], body["videos"][0]["postable"]) == (3, 400, False)


def 기능_서버_다시측정은_최근속도순이다(env):
    """목적: 30분 뒤 다시 재면 조회수가 더 많이 는 영상(최근 속도 큰 것)이 앞에 온다."""
    env.settings = replace(env.settings, rising=replace(env.settings.rising, remeasure=True))
    env.client.post("/api/rising/search")
    env.clock = at(5, 30)
    body = env.client.post("/api/rising/remeasure").json()
    assert [v["id"] for v in body["videos"]] == ["rise0000002", "rise0000001"]
    assert body["videos"][0]["recent_per_hour"] == 1000


def 예외_서버_다시측정_간격이_짧으면_409다(env):
    """목적: 처음 잰 뒤 30분이 안 지났으면 다시 재지 않고 남은 시간을 알린다."""
    env.settings = replace(env.settings, rising=replace(env.settings.rising, remeasure=True))
    env.client.post("/api/rising/search")
    env.clock = at(5, 10)
    res = env.client.post("/api/rising/remeasure")
    assert res.status_code == 409 and "20분 뒤" in res.json()["detail"]


# ── 자동 모드 ───────────────────────────────────────────────────────────
def 예외_서버_수동모드면_자동실행을_거부한다(env):
    """목적: post_mode가 manual이면 자동 실행을 시작하지 않는다(승인 없이 게시하는 길을 열지 않게)."""
    res = env.client.post("/api/auto", json={"video_ids": [TEST_ID], "profile": "유튜브1"})
    assert res.status_code == 409 and env.background == []


def 예외_서버_자동실행이_돌고있으면_새로_시작하지_않는다(env):
    """목적: 자동 실행이 끝나기 전에는 두 번째 자동 실행을 시작하지 않는다."""
    env.settings = replace(env.settings, post_mode="auto")
    first = env.client.post("/api/auto", json={"video_ids": [TEST_ID], "profile": "유튜브1"})
    second = env.client.post("/api/auto", json={"video_ids": [TEST_ID], "profile": "유튜브1"})
    assert (first.status_code, second.status_code, len(env.background)) == (200, 409, 1)


def 기능_서버_자동실행은_초안을_쓰고_같은_게시경로로_로봇을_띄운다(env):
    """목적: 자동 실행은 초안이 없으면 쓰고(승인 없이), ③과 같은 게시 경로로 로봇을 띄운 뒤 상태에 결과를 남긴다."""
    env.settings = replace(env.settings, post_mode="auto")
    env.client.post("/api/auto", json={"video_ids": [TEST_ID], "profile": "유튜브1"})
    env.background[0]()  # 뒤에서 돌 일을 지금 돌린다
    body = env.client.get("/api/auto").json()
    assert (env.writer.calls, len(env.spawned), body["finished"]) == (1, 1, True)
    assert body["items"][0]["attempt_id"] == env.spawned[0]


# ── 화면 문구 ─────────────────────────────────────────────────────────────────
def 기능_화면문구_설정파일_용어가_보이지_않는다(env):
    """목적: 처음 보는 사람이 읽는 문구(화면 HTML · 목록의 막힌 이유)에 설정 파일 이름·키 이름이 나오지 않는다(사용성).

    API 키를 넣는 안내 상자만은 예외다 — 사용자가 직접 해야 하는 일이라 파일 이름이 필요하다.
    화면 문구 대부분은 app.js · options.js가 그리므로, 그 문자열(따옴표 · 템플릿 글자 — ${…} 안의 코드는 뺀다)도 본다.
    """
    import re
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8") + (ROOT / "web" / "options.html").read_text(encoding="utf-8")
    visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    # 안내 상자 하나만 지운다 — 상자의 마지막 </p> 바로 뒤의 </div>까지(그 뒤 마크업은 남겨 검사한다).
    visible = re.sub(r'<div class="key-alert"[^>]*>.*?</p>\s*</div>', "", visible, count=1, flags=re.S)
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8") + (ROOT / "web" / "options.js").read_text(encoding="utf-8")
    js = re.sub(r"^\s*//.*$|\s//\s.*$", "", js, flags=re.M)  # 주석 줄과 줄 끝 주석
    js_strings = re.findall(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`', js, flags=re.S)
    js_text = re.sub(r"\$\{[^}]*\}", "", " ".join(js_strings))  # 템플릿의 ${s.post_mode} 같은 코드는 문구가 아니다
    reasons = " ".join(v["block_reason"] for v in env.client.post("/api/videos/load").json()["videos"])
    jargon = ("config.yaml", "logged_in", "channel_posting", "post_mode", "dev_mode", "profile_assign")
    assert [w for w in jargon if w in visible or w in reasons or w in js_text] == []


def 기능_서버_자동작성이_꺼지면_고를때_AI를_부르지_않는다(env):
    """목적: 고르자마자 쓰기(draft_on_select)가 꺼져 있으면 영상을 골라도 작가를 부르지 않고, [초안 쓰기](다시 쓰기)를 눌러야 1번째 초안을 쓴다."""
    env.settings = replace(env.settings, draft_on_select=False)
    picked = env.client.get(f"/api/videos/{TEST_ID}/draft").json()
    assert (picked.get("pending"), env.writer.calls) == (True, 0)
    written = env.client.post(f"/api/videos/{TEST_ID}/rewrite").json()
    assert (written["version"], env.writer.calls) == (1, 1)


def 기능_서버_로봇이_중간에_죽으면_줄을_실패로_끝낸다(tmp_path):
    """목적: 로봇이 시도를 잡고(입력 중) 죽으면, 진행을 물을 때 그 줄을 실패로 끝낸다 — 화면이 「진행 중」을 영원히 묻지 않게."""
    e = 환경(tmp_path)
    try:
        e.초안()
        aid = e.client.post("/api/posts", json={"video_id": TEST_ID, "profile": "유튜브1"}).json()["attempt_id"]
        e.store.claim(aid, at(3))
        e.store.set_status(aid, PostStatus.TYPING, at(3))
        e.robot.alive = False  # 입력 중에 프로세스가 죽었다
        body = e.client.get(f"/api/posts/{aid}").json()
        assert (body["status"], body["done"]) == ("failed_before_submit", True)
    finally:
        e.client.__exit__(None, None, None)
