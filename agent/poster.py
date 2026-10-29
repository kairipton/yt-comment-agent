"""게시 로봇 — 게시 시도 한 줄을 받아 크롬 프로필로 한 글자씩 타이핑 → 게시 → 내 댓글 확인. 명세: docs/systems/poster.md

서버와 별도 프로세스로 돈다(D-06): `python -m agent.poster <attempt_id>`.
화면은 기록장(store)의 상태를 0.5초마다 읽으므로, 로봇은 단계마다 기록장에 상태를 적는 것으로 진행을 알린다.

흐름(run):
  시도 잡기(QUEUED→OPENING) → 잠금(한 번에 하나) → 멈춘 줄 정리 → 게시 전 재검사(preflight)
  → 브라우저(post_comment) → 버튼 전 실패면 새 줄로 자동 재시도(최대 2회, D-12).
버튼 경계: 버튼을 누르기 직전에 SUBMITTED를 기록한다(영상당 1개 자리를 DB에서 먼저 잡는다, D-05).
  SUBMITTED 기록이 거부되면 버튼을 누르지 않는다. 기록된 뒤의 실패는 전부 「버튼 후 실패」다 — 이미 달렸을 수 있다.
판정(허용 범위·상한·초안 일치·댓글 규칙)은 전부 guard 함수로 한다. 이 파일에는 비교식을 직접 쓰지 않는다.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import ImageGrab
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from agent import guard
from agent.chrome import login_state
from agent.config import load_settings
from agent.models import Attempt, LoginState, PostStatus, Profile, Settings, Verdict, VideoItem
from agent.store import DuplicatePostError, Store

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
STALE_REASON = "프로그램이 중간에 멈췄어요."
BUSY_REASON = "다른 게시가 진행 중이에요. 끝난 뒤 다시 눌러 주세요."
CANCEL_REASON = "사용자가 크롬 창을 닫았어요."

# post_comment와 같은 모양의 함수. 테스트는 브라우저 대신 정해진 결과를 내는 가짜를 넣는다.
PostFn = Callable[[Profile, str, str, Path, Callable[[PostStatus], None], Callable[[int], None]], str]


class Rejected(Exception):
    """게시 전 거부. 브라우저를 열지 않거나, 열었어도 버튼을 누르지 않는다."""

    def __init__(self, reasons: list[str]):
        super().__init__(" / ".join(reasons))
        self.reasons = reasons


class PostFailed(Exception):
    """브라우저 단계 실패. 버튼 전/후는 여기가 아니라 기록장의 SUBMITTED 기록(submitted_at)으로 가른다."""

    def __init__(self, reason: str, screenshot: Path | None = None, *, cancelled: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.screenshot = screenshot
        self.cancelled = cancelled  # 사용자가 창을 닫음 — 재시도하지 않는다


# ── 게시 전 재검사 ─────────────────────────────────────────────────────────────
def check_post(video: VideoItem, text: str, profile: str, exempt: bool, settings: Settings, store: Store,
               now: datetime) -> Verdict:
    """게시 검사 한 곳 — 서버(③ 누를 때)와 로봇(preflight)이 이것을 부른다. 걸린 이유를 전부 모은다.

    판정은 전부 guard 함수로 한다. 여기서는 기록장·설정에서 값을 꺼내 넘길 뿐이다.
    """
    reasons: list[str] = []
    reasons += guard.check_target(video, {c.id for c in settings.channels}, set(settings.test_video_ids),
                                  settings.channel_posting).reasons
    # 예외 표시는 시도 줄을 믿지 않고 지금 dev_mode로 다시 본다 — 줄을 만든 뒤 dev_mode를 껐을 수 있다.
    reasons += guard.check_exempt(exempt, guard.is_exempt(video, set(settings.test_video_ids),
                                                           settings.dev_mode)).reasons
    since, until = guard.quota_window(now, store.last_quota_reset())  # 오늘(초기화가 있었으면 그 뒤)
    reasons += guard.check_quota(store.has_posted(video.id), exempt,
                                 store.submitted_count(since, until), settings.daily_max).reasons
    draft = store.get_draft(video.id)
    reasons += guard.check_draft(draft, text).reasons
    reasons += guard.check_text(text, settings.comment, store.comparable_texts(video.id)).reasons
    chosen = _profile(settings, profile)
    if chosen is None:
        reasons.append(f"설정에 없는 프로필이에요: {profile}")
    else:
        # 로그인 여부는 지금 그 프로필의 크롬 쿠키로 확인한다(chrome). 창이 열려 있으면 어차피 크롬을 열 수 없다.
        login = login_state(chosen, now)
        if login is LoginState.NEEDS_LOGIN:
            reasons.append(f"{profile}은 유튜브에 로그인되어 있지 않아요 — [프로필 관리]에서 로그인해 주세요.")
        elif login is LoginState.WINDOW_OPEN:
            reasons.append(f"{profile}의 크롬 창이 열려 있어요 — 그 창을 닫고 다시 눌러 주세요.")
    return Verdict.from_reasons(reasons)


def preflight(attempt: Attempt, settings: Settings, store: Store, now: datetime) -> Verdict:
    """브라우저를 열기 전에 다시 본다 — 서버가 이미 봤어도, 로봇을 직접 띄웠어도 같은 검사."""
    return check_post(attempt.video, attempt.text, attempt.profile, attempt.exempt, settings, store, now)


def _profile(settings: Settings, name: str) -> Profile | None:
    return next((p for p in settings.profiles if p.name == name), None)


# ── 한 번 누름의 흐름 ──────────────────────────────────────────────────────────
def run(attempt_id: int, settings: Settings, store: Store, post: PostFn, now: Callable[[], datetime]) -> int:
    """③ 한 번 누름 전체. 종료 코드: 0 = 게시 완료 · 1 = 실패·거부 · 2 = 이미 잡힌 시도(아무것도 안 씀)."""
    # 1. 시도를 잡는다 — 같은 시도를 두 번 띄워도 둘째는 남의 줄을 건드리지 않고 끝난다.
    if not store.claim(attempt_id, now()):
        log.warning("시도 %s는 이미 잡혀 있어 건너뜀", attempt_id)
        return 2

    # 2. 한 번에 하나만 — 하루 상한을 둘이 동시에 세는 일, 같은 프로필을 두 번 여는 일을 막는다.
    lock = _Lock(settings.db_path.parent / "poster.lock")
    if not lock.acquire():
        try:
            store.set_status(attempt_id, PostStatus.REJECTED, now(), reason=BUSY_REASON)
        except ValueError:
            # 잠금을 쥔 로봇이 그 사이 이 줄을 「멈춘 줄」로 이미 끝냈다 — 덮어쓸 것이 없다.
            log.warning("시도 %s는 이미 끝난 상태라 거부를 적지 않음", attempt_id)
        return 1
    try:
        # 3. 잠금을 잡았는데 진행 중인 다른 줄이 있으면, 앞선 로봇이 죽고 남긴 줄이다.
        stale = store.fail_unfinished(attempt_id, now(), STALE_REASON)
        if stale:
            log.warning("멈춘 시도를 실패로 정리: %s", stale)

        current = attempt_id
        while True:
            cancelled = _attempt_once(current, settings, store, post, now)
            attempt = store.get(current)
            if attempt.status is PostStatus.CONFIRMED:
                return 0
            # 버튼 전 실패만, 취소가 아니고 횟수가 남았을 때만 새 줄로 다시(D-12).
            if not guard.can_retry(attempt.status, attempt.retry_no, settings.max_retries, cancelled=cancelled):
                return 1
            current = store.start_attempt(attempt.video, attempt.profile, attempt.text, now(),
                                          exempt=attempt.exempt, retry_no=attempt.retry_no + 1,
                                          root_id=attempt.root_id)
            store.claim(current, now())
            log.info("자동 재시도 %s회째 — 시도 %s", attempt.retry_no + 1, current)
    finally:
        lock.release()


def _attempt_once(aid: int, settings: Settings, store: Store, post: PostFn, now: Callable[[], datetime]) -> bool:
    """시도 한 줄: 재검사 → 브라우저. 결과는 기록장 상태로 남긴다. 사용자 취소였으면 True."""
    attempt = store.get(aid)
    verdict = preflight(attempt, settings, store, now())
    if not verdict.ok:
        store.set_status(aid, PostStatus.REJECTED, now(), reason=" / ".join(verdict.reasons))
        return False

    def on_status(status: PostStatus) -> None:
        # 버튼 직전의 SUBMITTED 기록이 곧 영상당 1개 자리 잡기다. DB가 거부하면 버튼을 누르지 않게 Rejected를 던진다.
        try:
            store.set_status(aid, status, now())
        except DuplicatePostError as e:
            raise Rejected([str(e)]) from None

    def on_typed(count: int) -> None:
        # 친 글자 수만 적는다(상태는 그대로) — 화면 ③이 「86자 중 45자」로 보여 준다.
        store.set_typed(aid, count, now())

    profile = _profile(settings, attempt.profile)
    shots = settings.screenshot_dir / f"attempt-{aid}"
    try:
        url = post(profile, attempt.video.id, attempt.text, shots, on_status, on_typed)
    except Rejected as e:
        store.set_status(aid, PostStatus.REJECTED, now(), reason=" / ".join(e.reasons))
        return False
    except PostFailed as e:
        store.set_status(aid, _failed_status(store, aid), now(), reason=e.reason, screenshot=e.screenshot)
        return e.cancelled
    except Exception as e:  # 예상 못 한 오류도 삼키지 않는다 — 실패로 기록하고 드러낸다.
        log.error("게시 중 예상 못 한 오류 — 시도 %s", aid, exc_info=True)
        store.set_status(aid, _failed_status(store, aid), now(), reason=f"예상 못 한 오류: {type(e).__name__}")
        return False
    store.set_status(aid, PostStatus.CONFIRMED, now(), comment_url=url)
    return False


def _failed_status(store: Store, aid: int) -> PostStatus:
    """버튼 경계 = SUBMITTED 기록. 기록이 있으면(submitted_at) 버튼 후 실패, 없으면 버튼 전 실패."""
    if store.get(aid).submitted_at is not None:
        return PostStatus.FAILED_AFTER_SUBMIT
    return PostStatus.FAILED_BEFORE_SUBMIT


class _Lock:
    """데이터 폴더의 잠금 파일. OS 파일 잠금이라 프로세스가 죽으면 저절로 풀린다."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+b")  # noqa: SIM115 — release()에서 닫는다
        try:
            f.seek(0)
            _lock_byte(f)
        except OSError:
            f.close()
            return False
        self._file = f
        return True

    def release(self) -> None:
        if self._file is None:
            return
        self._file.seek(0)
        _unlock_byte(self._file)
        self._file.close()
        self._file = None


if sys.platform == "win32":
    import msvcrt

    def _lock_byte(f) -> None:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_byte(f) -> None:
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_byte(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_byte(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ── 브라우저 ──────────────────────────────────────────────────────────────────
WATCH = "https://www.youtube.com/watch?v={}"
TYPE_DELAY_MS = 70        # 한 글자씩 — 글자 사이 간격
TYPE_SHOT_EVERY = 15      # 이만큼 칠 때마다 진행 화면을 한 장 남긴다(창이 닫힐 때 쓸 캡처)
LINGER_MS = 3000          # 닫기 전 기다림 — 확인 화면을 눈으로 볼 수 있게
CONFIRM_TIMEOUT_MS = 20000

# 맨 위 댓글 몇 개의 (글, 시각 링크) — 시각 링크의 lc 값이 댓글 하나하나의 id다.
_THREADS_JS = """() => [...document.querySelectorAll('ytd-comments ytd-comment-thread-renderer')].slice(0, 20).map(t => {
  const text = t.querySelector('#content-text');
  const link = t.querySelector('#published-time-text a') || t.querySelector('a[href*="lc="]');
  return [text ? text.innerText : '', link ? link.getAttribute('href') : ''];
})"""


def post_comment(profile: Profile, video_id: str, text: str, shot_prefix: Path,
                 on_status: Callable[[PostStatus], None], on_typed: Callable[[int], None]) -> str:
    """크롬 프로필을 열어 댓글을 단다. 댓글 링크를 돌려준다. 실패는 PostFailed, 버튼 직전 거부는 Rejected.

    on_typed: 타이핑 중 15글자 묶음마다 지금까지 친 글자 수(공백 포함 코드포인트)를 알린다.

    시제품(테스트 영상 게시 성공)의 실행 조건과 선택자를 그대로 쓴다. 선택자는 화면 언어와 무관한 요소 id다.
    """
    shot_prefix.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile.user_data_dir), channel="chrome", headless=False, locale="ko-KR",
                viewport={"width": 1280, "height": 900}, ignore_default_args=["--enable-automation"],
            )
        except PlaywrightError as e:
            # 크롬이 안 떴으니 찍을 브라우저 화면이 없다 — 바탕화면을 찍는다(열려 있는 프로필 창 같은 원인이 보인다).
            raise PostFailed(launch_reason(str(e)), desktop_shot(shot_prefix, "실행")) from None

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        step = "열기"
        fail_reason = "영상 화면을 불러오지 못했어요."  # 지금 단계에서 시간 초과가 나면 보여 줄 이유
        # 진행 중 화면을 한 장씩 남겨 둔다 — 사용자가 창을 닫으면 그 뒤로는 캡처할 수 없어, 닫히기 직전 모습이 이것이다.
        progress = {"shot": None}

        def keep() -> None:
            progress["shot"] = _shot(page, shot_prefix, "진행") or progress["shot"]

        def fail(reason: str, *, cancelled: bool = False) -> PostFailed:
            return PostFailed(reason, _shot(page, shot_prefix, step), cancelled=cancelled)

        try:
            on_status(PostStatus.OPENING)
            page.goto(WATCH.format(video_id), wait_until="domcontentloaded")
            page.wait_for_selector("ytd-watch-flexy", timeout=20000)
            page.wait_for_timeout(3000)
            keep()

            step = "로그인"
            if page.locator("ytd-masthead #avatar-btn").count() == 0:
                raise fail("이 프로필은 유튜브에 로그인되어 있지 않아요.")

            step, fail_reason = "댓글창", "댓글 창을 찾지 못했어요(댓글이 꺼진 영상일 수 있어요)."
            box = page.locator("ytd-comments #simplebox-placeholder")
            for _ in range(15):  # 댓글 영역은 스크롤해야 불러온다
                if box.count() > 0:
                    break
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(1000)
            if box.count() == 0:
                raise fail(fail_reason)
            # 이미 있는 댓글을 적어 둔다 — 같은 글이 이미 있어도 「새」 댓글만 확인하려고.
            # 입력칸은 댓글 목록보다 먼저 뜨므로, 목록이 보일 때까지 잠깐(최대 5초) 기다린 뒤 적는다.
            threads = page.locator("ytd-comments ytd-comment-thread-renderer")
            for _ in range(10):
                if threads.count() > 0:
                    break
                page.wait_for_timeout(500)
            before = page.evaluate(_THREADS_JS)
            box.first.scroll_into_view_if_needed()
            box.first.click()
            editor = page.locator("ytd-comments #contenteditable-root").first
            editor.wait_for(timeout=10000)
            keep()

            step, fail_reason = "입력", "댓글을 입력하지 못했어요."
            on_status(PostStatus.TYPING)
            editor.click()
            # 한 글자씩 치되, 몇 글자마다 진행 화면을 남긴다(창이 닫혀도 「입력 중」 모습이 캡처로 남게).
            for start in range(0, len(text), TYPE_SHOT_EVERY):
                page.keyboard.type(text[start:start + TYPE_SHOT_EVERY], delay=TYPE_DELAY_MS)
                on_typed(min(start + TYPE_SHOT_EVERY, len(text)))  # 화면 ③의 「n자 중 m자」
                keep()
            page.wait_for_timeout(1000)
            if _norm(editor.inner_text()) != _norm(text):
                raise fail("입력된 글이 다르게 들어갔어요.")

            step, fail_reason = "게시", "게시 버튼을 누르지 못했어요."
            on_status(PostStatus.SUBMITTED)  # 자리 잡기. DB가 거부하면 Rejected가 올라가고 버튼을 누르지 않는다
            page.locator("ytd-comments #submit-button").first.click()

            step, fail_reason = "확인", "게시 후 내 댓글을 확인하지 못했어요."
            on_status(PostStatus.CONFIRMING)
            url = _wait_new_comment(page, text, before, video_id)
            _shot(page, shot_prefix, "완료")
            return url
        except (Rejected, PostFailed):
            raise
        except PlaywrightTimeout:
            raise fail(fail_reason) from None
        except PlaywrightError as e:
            if page.is_closed():
                # 창이 닫혀 지금 화면은 찍을 수 없다 — 닫히기 직전에 남겨 둔 진행 화면을 대신 싣는다.
                raise PostFailed(CANCEL_REASON, progress["shot"], cancelled=True) from None
            raise fail(f"{fail_reason} ({type(e).__name__})") from None
        finally:
            try:
                if not page.is_closed():
                    page.wait_for_timeout(LINGER_MS)
                ctx.close()
            except PlaywrightError:
                pass  # 사용자가 이미 닫았다 — 닫을 것이 없다


def _wait_new_comment(page, text: str, before: list[list[str]], video_id: str) -> str:
    """시도의 글과 같고, 게시 전에는 없던 댓글이 나타날 때까지 기다린다. 그 댓글의 링크를 돌려준다.

    글만 비교하면 테스트 영상에 같은 글이 이미 달려 있을 때 이번 게시가 실패해도 「완료」가 된다 — 그래서
    게시 전에 있던 댓글의 lc 값을 적어 두고, 그것이 아닌 댓글만 새 댓글로 본다.
    """
    target = _norm(text)
    known = {_lc(href) for _, href in before} - {None}
    same_before = sum(1 for body, _ in before if _norm(body) == target)
    waited = 0
    while waited <= CONFIRM_TIMEOUT_MS:
        threads = page.evaluate(_THREADS_JS)
        same = [href for body, href in threads if _norm(body) == target]
        for href in same:
            lc = _lc(href)
            if lc is not None and lc not in known:
                return comment_url(href, video_id)
        if len(same) > same_before:
            # 새 댓글은 보이는데 링크 id를 못 얻었다 — 영상 링크로 대신 남긴다 [사용자].
            return WATCH.format(video_id)
        page.wait_for_timeout(500)
        waited += 500
    raise PlaywrightTimeout("새 댓글이 보이지 않음")


def comment_url(href: str, video_id: str) -> str | None:
    """댓글 시각 링크(`/watch?v=…&lc=…`)에서 댓글 하나를 가리키는 링크를 만든다. lc가 없으면 None."""
    lc = _lc(href)
    if lc is None:
        return None
    return f"{WATCH.format(video_id)}&lc={lc}"


def _lc(href: str | None) -> str | None:
    if not href:
        return None
    values = parse_qs(urlsplit(href).query).get("lc")
    return values[0] if values else None


def launch_reason(message: str) -> str:
    """크롬 실행 오류를 사용자가 알아듣는 이유로 바꾼다.

    같은 프로필 크롬 창이 열려 있으면 크롬은 「기존 브라우저 세션에서 여는 중」을 내고 곧바로 끝난다.
    Playwright 쪽에서는 브라우저가 닫혔다는 오류로 보인다.
    """
    lower = message.lower()
    if any(k in lower for k in ("기존 브라우저 세션", "existing browser session", "has been closed",
                                "processsingleton", "already in use")):
        return "프로필 창이 이미 열려 있어요. 그 크롬 창을 닫고 다시 눌러 주세요."
    if "is not found" in lower or "executable doesn't exist" in lower:
        return "크롬을 찾지 못했어요. 구글 크롬이 설치되어 있는지 확인해 주세요."
    return "크롬을 열지 못했어요."


def _norm(text: str) -> str:
    """비교용: 앞뒤 공백을 지우고 줄바꿈·연속 공백을 한 칸으로."""
    return " ".join(text.split())


def _shot(page, prefix: Path, step: str) -> Path | None:
    """지금 화면을 남긴다. 창이 이미 닫혔으면 None — 캡처 실패가 원래 실패 이유를 가리지 않게."""
    path = prefix.with_name(f"{prefix.name}-{step}.png")
    try:
        page.screenshot(path=str(path))
    except PlaywrightError:
        log.warning("화면 캡처 실패: %s", path)
        return None
    return path


def _grab_desktop():
    """바탕화면 전체(모니터가 여럿이면 전부)를 이미지로 찍는다. 테스트는 이 함수를 가짜로 바꿔 끼운다."""
    return ImageGrab.grab(all_screens=True)


def desktop_shot(prefix: Path, step: str) -> Path | None:
    """크롬을 띄우지 못해 찍을 브라우저 화면이 없을 때 바탕화면 전체를 남긴다(실패에는 이유와 화면 캡처를 함께 남긴다).

    다른 창도 함께 찍힐 수 있지만 이 PC의 data/에만 저장된다. 찍지 못하면 None — 캡처 문제가 원래 실패 이유를 가리지 않게,
    예외를 올리지 않고 경고 로그로 드러낸다.
    """
    path = prefix.with_name(f"{prefix.name}-{step}.png")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _grab_desktop().save(path)
    except Exception:  # 화면 장치·권한 문제 등 무엇이든 — 캡처만 포기한다
        log.warning("바탕화면 캡처 실패: %s", path, exc_info=True)
        return None
    return path


# ── 진입점 ────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    """`python -m agent.poster <attempt_id>` — 서버가 ③을 누를 때 이 명령으로 띄운다."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(argv) != 1 or not argv[0].isdigit():
        print("사용법: python -m agent.poster <attempt_id>", file=sys.stderr)
        return 1
    settings = load_settings(ROOT / "config.yaml", ROOT / ".env")
    store = Store(settings.db_path, settings.test_video_ids)
    log.info("시도 %s 시작", argv[0])
    code = run(int(argv[0]), settings, store, post_comment, lambda: datetime.now(UTC))
    log.info("시도 %s 끝 — 종료 코드 %s", argv[0], code)  # data/poster.log에 남는다(서버가 띄울 때)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
