"""store 테스트 — 영상당 1개 DB 방어선, 하루 수 세기, 초안, 비교 대상."""

from datetime import datetime

import pytest

from agent.models import Draft, PostStatus, Source
from agent.store import DuplicatePostError, Store
from tests.conftest import CHANNEL_A, TEST_ID, at, video


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "agent.db", test_ids={TEST_ID})


def 게시(store: Store, vid=None, status=PostStatus.SUBMITTED, now=None, exempt=False, retry_no=0) -> int:
    """시도를 만들고 status까지 바꾼 뒤 번호를 돌려주는 도우미."""
    v = vid or video()
    aid = store.start_attempt(v, "유튜브1", "댓글", now or at(3), exempt=exempt, retry_no=retry_no)
    store.set_status(aid, status, now or at(3))
    return aid


# ── 시도 ──────────────────────────────────────────────────────────────────────
def 기능_시도_만들면_대기중_상태다(store):
    """목적: 새 시도는 「대기 중」에서 시작하고(게시 로봇이 잡기 전) 영상 정보가 그대로 저장된다."""
    aid = store.start_attempt(video(), "유튜브1", "댓글", at(3), exempt=False)
    got = store.get(aid)
    assert (got.status, got.video.channel_id) == (PostStatus.QUEUED, CHANNEL_A)


def 예외_시각_시간대가_없으면_거부된다(store):
    """목적: 시간대 없는 시각이 섞여 하루 경계가 어긋나는 것을 막는다."""
    with pytest.raises(ValueError):
        store.start_attempt(video(), "유튜브1", "댓글", datetime(2026, 10, 20, 3), exempt=False)


def 예외_예외표시_테스트영상이_아니면_거부된다(store):
    """목적: 판정 쪽 실수로 지정 채널 영상에 예외 표시가 와도 저장에서 막는다(D-05)."""
    with pytest.raises(ValueError):
        store.start_attempt(video(), "유튜브1", "댓글", at(3), exempt=True)


def 기능_기록_최근것이_앞에_온다(store):
    """목적: 기록 표는 최근 시도가 위에 온다."""
    first = 게시(store, video("vid00000001"))
    second = 게시(store, video("vid00000002"))
    assert [a.id for a in store.history()] == [second, first]


# ── 영상당 1개 (DB 방어선) ─────────────────────────────────────────────────────
def 기능_영상당1개_DB가_두번째_게시를_거부한다(store):
    """목적: 예외가 아닌 같은 영상에 게시 버튼 상태가 두 번 저장되지 않는다(D-05)."""
    게시(store)
    second = store.start_attempt(video(), "유튜브2", "댓글", at(4), exempt=False)
    with pytest.raises(DuplicatePostError):
        store.set_status(second, PostStatus.SUBMITTED, at(4))


def 기능_영상당1개_거부된_줄은_상태가_바뀌지_않는다(store):
    """목적: DB가 거부하면 그 줄의 상태 변경도 통째로 취소된다 — 부르는 쪽이 「거부」로 다시 적을 수 있다."""
    게시(store)
    second = store.start_attempt(video(), "유튜브2", "댓글", at(4), exempt=False)
    with pytest.raises(DuplicatePostError):
        store.set_status(second, PostStatus.SUBMITTED, at(4))
    assert store.get(second).status is PostStatus.QUEUED


def 기능_영상당1개_DB는_버튼전_실패_뒤_재시도_줄을_받는다(store):
    """목적: 버튼 전 실패 줄은 자리를 잡지 않아, 같은 영상의 재시도 줄이 게시까지 저장된다(D-12)."""
    게시(store, status=PostStatus.FAILED_BEFORE_SUBMIT)
    retry = store.start_attempt(video(), "유튜브1", "댓글", at(4), exempt=False, retry_no=1)
    store.set_status(retry, PostStatus.SUBMITTED, at(4))
    assert store.get(retry).status is PostStatus.SUBMITTED


def 기능_영상당1개_예외줄은_여러번_게시할_수_있다(store):
    """목적: dev_mode의 테스트 영상(예외 줄)은 같은 영상에 여러 번 게시해도 DB가 막지 않는다(D-17)."""
    test_video = video(TEST_ID, source=Source.TEST)
    게시(store, test_video, exempt=True)
    second = store.start_attempt(test_video, "유튜브1", "댓글", at(4), exempt=True)
    store.set_status(second, PostStatus.CONFIRMED, at(4))
    assert store.get(second).status is PostStatus.CONFIRMED


def 경계_영상당1개_버튼누름을_건너뛰어도_자리를_잡는다(store):
    """목적: 「게시 버튼 누름」을 거치지 않고 바로 「게시 완료」가 돼도 영상당 1개 자리를 잡는다."""
    게시(store, status=PostStatus.CONFIRMED)
    assert store.has_posted(video().id)


def 기능_게시여부_예외_게시는_세지_않는다(store):
    """목적: dev_mode로 단 테스트 영상 게시는 나중에 dev_mode를 꺼도 영상당 1개에 세지 않는다 [사용자]."""
    게시(store, video(TEST_ID, source=Source.TEST), exempt=True)
    assert not store.has_posted(TEST_ID)


# ── 하루 수 세기 ───────────────────────────────────────────────────────────────
def 기능_하루수_예외_게시도_센다(store):
    """목적: 하루 상한은 테스트 영상(예외 줄)에도 적용되므로 그 게시도 센다(D-17)."""
    게시(store, video(TEST_ID, source=Source.TEST), exempt=True)
    assert store.submitted_count(at(0), at(23)) == 1


def 기능_하루수_버튼전_실패와_거부는_세지_않는다(store):
    """목적: 게시 버튼을 누르지 않은 시도는 하루 상한에 세지 않는다(D-13)."""
    게시(store, video("vid00000001"), status=PostStatus.FAILED_BEFORE_SUBMIT)
    게시(store, video("vid00000002"), status=PostStatus.REJECTED)
    assert store.submitted_count(at(0), at(23)) == 0


def 기능_하루수_버튼후_실패는_센다(store):
    """목적: 게시 버튼 뒤 확인만 실패한 시도는 실제로 달렸을 수 있어 센다(D-13)."""
    게시(store, status=PostStatus.FAILED_AFTER_SUBMIT)
    assert store.submitted_count(at(0), at(23)) == 1


def 경계_하루수_구간_끝은_포함하지_않는다(store):
    """목적: 구간 [since, until)의 끝 시각에 누른 게시는 다음 구간으로 간다."""
    게시(store, now=at(15))
    assert (store.submitted_count(at(0), at(15)), store.submitted_count(at(15), at(23))) == (0, 1)


def 기능_초기화_마지막_시각을_돌려주고_기록은_남긴다(store):
    """목적: 초기화는 시각만 남긴다 — 마지막 초기화 시각을 돌려주고, 게시 기록은 지우지 않는다(기록 표 · 영상당 1개 그대로)."""
    게시(store, now=at(1))
    store.reset_quota(at(2))
    store.reset_quota(at(4))  # 여러 번 눌러도 마지막 것만 쓴다
    assert (store.last_quota_reset(), len(store.history())) == (at(4), 1)


# ── 초안 · 비교 대상 ────────────────────────────────────────────────────


def 기능_비교대상_같은영상_글은_빠진다(store):
    """목적: 「영상마다 다르게」 비교에 같은 영상의 이전 글은 넣지 않고 다른 영상의 글만 넣는다."""
    store.save_draft(Draft("vid00000001", "고민", "같은 영상 초안"), at(3))
    store.save_draft(Draft("vid00000002", "고민", "다른 영상 초안"), at(3))
    assert store.comparable_texts("vid00000001") == ["다른 영상 초안"]


# ── 시도의 일생 (게시 로봇) ────────────────────────────────────────────────────
def 기능_잡기_대기중인_시도만_한번_잡힌다(store):
    """목적: 같은 시도를 로봇 둘이 잡으려 해도 첫 번째만 성공한다 — 남의 줄을 덮어쓰지 않게."""
    aid = store.start_attempt(video(), "유튜브1", "댓글", at(3), exempt=False)
    assert [store.claim(aid, at(3)), store.claim(aid, at(3))] == [True, False]
    assert store.get(aid).status is PostStatus.OPENING


def 예외_기록장_끝난_시도는_상태를_바꾸지_않는다(store):
    """목적: 완료된 시도 위에 거부 같은 다른 상태가 덮이지 않는다."""
    aid = 게시(store, status=PostStatus.CONFIRMED)
    with pytest.raises(ValueError):
        store.set_status(aid, PostStatus.REJECTED, at(4), reason="늦게 온 거부")
    assert store.get(aid).status is PostStatus.CONFIRMED


def 기능_재시도묶음_가장_최근_줄을_준다(store):
    """목적: 재시도 줄은 첫 시도의 root_id를 이어받고, 첫 id로 물으면 가장 최근 줄이 나온다 — 화면이 재시도를 따라간다."""
    first = store.start_attempt(video(), "유튜브1", "댓글", at(3), exempt=False)
    retry = store.start_attempt(video(), "유튜브1", "댓글", at(4), exempt=False, retry_no=1, root_id=first)
    assert store.get(first).root_id == first
    assert store.latest_in_root(first).id == retry


def 기능_멈춘줄정리_버튼기록에_따라_전후가_갈린다(store):
    """목적: 죽은 로봇이 남긴 진행 중 줄은 버튼 기록이 있으면 버튼 후 실패, 없으면 버튼 전 실패로 끝난다."""
    # 줄 셋: 입력 중에 멈춤 · 확인 중에 멈춤 · 지금 로봇이 잡은 줄(건드리면 안 됨).
    typing = 게시(store, status=PostStatus.TYPING)
    confirming = 게시(store, vid=video("vid00000002"), status=PostStatus.CONFIRMING)
    mine = store.start_attempt(video("vid00000003"), "유튜브1", "댓글", at(5), exempt=False)
    store.claim(mine, at(5))
    assert sorted(store.fail_unfinished(mine, at(5), "프로그램이 중간에 멈췄어요")) == [typing, confirming]
    statuses = (store.get(typing).status, store.get(confirming).status, store.get(mine).status)
    assert statuses == (PostStatus.FAILED_BEFORE_SUBMIT, PostStatus.FAILED_AFTER_SUBMIT, PostStatus.OPENING)


def 기능_대기줄정리_대기줄만_실패로_끝낸다(store):
    """목적: 서버가 시작할 때 남은 대기 줄은 버튼 전 실패로 끝나고, 진행 중 줄은 그대로 둔다(다음 로봇 몫)."""
    queued = store.start_attempt(video(), "유튜브1", "댓글", at(3), exempt=False)
    running = 게시(store, vid=video("vid00000002"), status=PostStatus.TYPING)
    assert store.fail_queued(at(4), "서버가 다시 시작되어 취소됐어요") == [queued]
    assert (store.get(queued).status, store.get(running).status) == (PostStatus.FAILED_BEFORE_SUBMIT, PostStatus.TYPING)


def 기능_비교대상_불합격_초안은_넣지_않는다(store):
    """목적: 규칙 검사에 떨어진 다른 영상의 초안은 비교 대상이 아니다 — 게시될 수 없는 글 때문에 멀쩡한 초안이 막히지 않게."""
    store.save_draft(Draft("vid00000002", "고민", "합격한 다른 영상 초안"), at(3))
    store.save_draft(Draft("vid00000003", "고민", "떨어진 초안", issues=("너무 비슷해요.",)), at(3))
    assert store.comparable_texts("vid00000001") == ["합격한 다른 영상 초안"]


def 기능_멈춘줄정리_한줄도_버튼기록에_따라_끝나고_끝난줄은_그대로다(store):
    """목적: 멈춘 줄 하나를 끝낼 때도 버튼 기록으로 전/후가 갈리고, 이미 끝난 줄은 건드리지 않는다(None)."""
    typing = 게시(store, status=PostStatus.TYPING)
    done = 게시(store, vid=video("vid00000002"), status=PostStatus.CONFIRMED)
    results = (store.fail_stopped(typing, at(4), "멈춤"), store.fail_stopped(done, at(4), "멈춤"))
    assert results == (PostStatus.FAILED_BEFORE_SUBMIT, None)
    assert store.get(done).status is PostStatus.CONFIRMED


# ── 화면 B안이 쓰는 값 ─────────────────────────────────────────────────────────
# 새 열이 생기기 전의 기록장 파일 모양 — 옛 파일을 열어도 깨지지 않는지 보려고 그대로 둔다.
_OLD_SCHEMA = """
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    video_id TEXT NOT NULL, video_title TEXT NOT NULL, channel_id TEXT NOT NULL, channel_name TEXT NOT NULL,
    published_at TEXT, thumbnail_url TEXT NOT NULL, source TEXT NOT NULL, profile TEXT NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT, screenshot TEXT, comment_url TEXT, retry_no INTEGER NOT NULL,
    exempt INTEGER NOT NULL, submitted_at TEXT, unique_video TEXT UNIQUE, root_id INTEGER);
CREATE TABLE drafts (
    video_id TEXT PRIMARY KEY, worry TEXT NOT NULL, comment TEXT NOT NULL, version INTEGER NOT NULL,
    approved INTEGER NOT NULL, issues TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO drafts VALUES ('vid00000001', '고민', '옛 댓글', 2, 0, '[]', '2026-10-20T03:00:00.000000Z');
"""


def 기능_초안_읽은양을_저장하고_읽는다(store):
    """목적: 초안의 「AI가 읽은 양」(자막 글자 수 · 인기 댓글 수)이 저장되고 그대로 읽힌다 — None(키 없음)도 그대로."""
    store.save_draft(Draft("vid00000001", "고민", "댓글", transcript_chars=3214, comments_read=None), at(3))
    got = store.get_draft("vid00000001")
    assert (got.transcript_chars, got.comments_read) == (3214, None)


def 기능_기록장_옛파일에_새열을_더해_연다(tmp_path):
    """목적: 새 열이 없는 옛 기록장 파일도 열리고, 옛 초안의 읽은 양은 None(「자막 0자」와 다르다), 새 시도도 만들어진다."""
    import sqlite3
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(_OLD_SCHEMA)
    store = Store(path, test_ids={TEST_ID})
    old = store.get_draft("vid00000001")
    aid = store.start_attempt(video(), "유튜브1", "댓글", at(3), exempt=False)
    assert (old.transcript_chars, old.comments_read, store.get(aid).typed_chars) == (None, None, 0)


def 경계_기록장_두번_열어도_열추가가_깨지지_않는다(tmp_path):
    """목적: 서버와 게시 로봇이 같은 파일을 각각 열어도(열이 이미 있어도) 오류 없이 열린다."""
    import sqlite3
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(_OLD_SCHEMA)
    Store(path, test_ids={TEST_ID})
    again = Store(path, test_ids={TEST_ID})  # 두 번째는 더할 열이 없다
    assert again.get_draft("vid00000001").version == 2


def 기능_타이핑진행_친글자수를_적는다(store):
    """목적: 게시 로봇이 친 글자 수를 적으면 시도 줄에 남고, 상태는 그대로다(입력 중)."""
    aid = 게시(store, status=PostStatus.TYPING)
    store.set_typed(aid, 45, at(3, 1))
    got = store.get(aid)
    assert (got.typed_chars, got.status) == (45, PostStatus.TYPING)


def 경계_타이핑진행_끝난줄은_건드리지_않는다(store):
    """목적: 끝난 줄(실패·완료)에는 친 글자 수를 덮어쓰지 않는다 — 늦게 온 기록이 결과를 흐리지 않게."""
    aid = 게시(store, status=PostStatus.TYPING)
    store.set_typed(aid, 15, at(3, 1))
    store.set_status(aid, PostStatus.FAILED_BEFORE_SUBMIT, at(3, 2), reason="창 닫힘")
    store.set_typed(aid, 30, at(3, 3))
    assert store.get(aid).typed_chars == 15


def 기능_먼저단게시_영상당1개_줄을_찾는다(store):
    """목적: 그 영상에 버튼까지 누른(영상당 1개 열이 채워진) 줄을 찾는다 — 버튼 전 실패 줄은 아니다."""
    게시(store, status=PostStatus.FAILED_BEFORE_SUBMIT)  # 버튼 전 실패 — 먼저 단 게시가 아니다
    posted = 게시(store, status=PostStatus.CONFIRMED, now=at(4))
    assert store.posted_attempt("vid00000001").id == posted


def 경계_먼저단게시_개발모드_예외줄은_찾지_않는다(store):
    """목적: 개발 모드로 단 테스트 영상 게시(예외 줄)는 먼저 단 게시가 아니다(D-23) — 영상당 1개에 세지 않는 줄이다."""
    게시(store, vid=video(TEST_ID, source=Source.TEST), status=PostStatus.CONFIRMED, exempt=True)
    assert store.posted_attempt(TEST_ID) is None
