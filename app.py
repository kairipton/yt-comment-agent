"""대시보드 서버 — 화면(web/)이 부르는 /api/* 를 진짜 부품(agent/)에 연결한다. 명세: docs/systems/dashboard.md

흐름: 브라우저가 web/index.html을 열고, 화면의 버튼은 이 서버의 /api/* 를 부른다.
  ① 불러오기 → fetch(테스트 영상 + 지정 채널 최신 영상)를 메모리에 둔다. 링크로 한 개씩 더할 수도 있다
  ② 고르기   → 저장된 초안이 있으면 그것, 없으면 fetch.context → draft(작가) → store에 저장
  ③ 게시     → store에 시도 한 줄 → poster.check_post(로봇과 같은 검사)
               → 통과면 게시 로봇을 별도 프로세스로 띄우고, 걸리면 로봇 없이 「거부」로 기록
             → 화면은 0.5초마다 진행을 묻고, 서버는 기록장(store)에서 그 누름의 가장 최근 줄을 읽어 돌려준다
서버는 판정 로직을 갖지 않는다(D-18). 게시 검사는 게시 로봇과 같은 함수 하나(poster.check_post)를 부를 뿐이다.

실행: run_dashboard.bat 더블클릭(원클릭 실행기) · 개발 중에는 uv run uvicorn app:app --port 8501
"""

from __future__ import annotations

import logging
import math
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import assign, guard, trend
from agent.auto import AutoDeps, AutoItem, AutoJob, run_auto
from agent.chrome import login_states, open_profile_window
from agent.config import (
    ConfigError,
    load_settings,
    read_options,
    read_prompt,
    save_options,
    save_prompt,
)
from agent.draft import Drafter, DraftUnavailable
from agent.fetch import FetchError, Measured, YouTubeClient
from agent.models import (
    PROMPT_FIELDS,
    Attempt,
    Draft,
    LoginState,
    PostStatus,
    Profile,
    Settings,
    Source,
    VideoItem,
)
from agent.poster import check_post, desktop_shot
from agent.store import Store

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

# ③ 진행 단계 — 화면에 차례로 보이는 상태. 대기(QUEUED)는 첫 단계와 같은 칸에 보인다.
STEPS = [PostStatus.OPENING, PostStatus.TYPING, PostStatus.SUBMITTED, PostStatus.CONFIRMING, PostStatus.CONFIRMED]
NO_AI_KEY = "AI API 키가 설정되지 않았어요 — ② 칸의 안내대로 키를 넣고 새로고침해 주세요."
SPAWN_FAILED = "게시 로봇을 띄우지 못했어요."
ROBOT_DIED = "게시 로봇이 시작하지 못했어요 — 다시 눌러 보고, 계속되면 프로그램을 다시 시작해 주세요."  # 자세한 원인은 data/poster.log
ROBOT_STOPPED = "게시 로봇이 중간에 멈췄어요 — 게시 기록과 유튜브에서 댓글이 달렸는지 확인해 주세요."
RESTARTED = "서버가 다시 시작되어 취소됐어요."
DASHBOARD_ORIGINS = {"http://localhost:8501", "http://127.0.0.1:8501"}  # 상태 변경 요청을 받는 출처(같은 PC의 대시보드)


# ── 부품 묶음 ─────────────────────────────────────────────────────────────────
@dataclass
class Services:
    """서버가 쓰는 부품. 테스트는 가짜 YouTube·작가·로봇 띄우기를 넣는다."""

    settings: Callable[[], Settings]               # 요청마다 다시 읽는다 — 키를 넣고 새로고침하면 바로 반영
    youtube: Callable[[Settings], YouTubeClient]
    drafter: Callable[[Settings], Drafter]
    spawn: Callable[[int], subprocess.Popen]      # 게시 로봇 프로세스 띄우기
    now: Callable[[], datetime]
    run_background: Callable[[Callable[[], None]], None] = lambda work: threading.Thread(target=work, daemon=True).start()
    open_profile: Callable[[Profile], None] = open_profile_window  # 프로필 관리 [크롬 열기] — 테스트는 연 이름만 모은다
    config_path: Path = ROOT / "config.yaml"   # 옵션 페이지가 읽고 쓰는 설정 파일 — 테스트는 임시 복사본
    # 프로필 이름 → 유튜브 로그인 상태(크롬 쿠키로 확인) — 테스트는 정해진 상태를 돌려준다
    logins: Callable[[tuple[Profile, ...]], dict[str, LoginState]] = lambda profiles: login_states(profiles, datetime.now(UTC))

    @classmethod
    def real(cls) -> Services:
        return cls(
            settings=lambda: load_settings(ROOT / "config.yaml", ROOT / ".env"),
            youtube=lambda s: YouTubeClient(s.youtube_api_key, s.fetch_timeout),
            drafter=Drafter,
            spawn=_spawn_poster,
            now=lambda: datetime.now(UTC),
        )


def _spawn_poster(attempt_id: int) -> subprocess.Popen:
    """게시 로봇을 띄운다: 서버와 같은 파이썬(uv 가상환경)으로 `-m agent.poster <id>`.

    창 없이, 서버 창과 따로 띄운다 — 서버 창을 닫아도 하던 게시 한 건은 끝까지 마치고 스스로 끝난다(사용자 결정).
    출력은 data/poster.log에 이어 쓴다.
    """
    log_path = ROOT / "data" / "poster.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "agent.poster", str(attempt_id)]
    with open(log_path, "ab") as out:
        if sys.platform != "win32":
            return subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        # 서버는 uv가 만든 작업 묶음(job object) 안에서 돈다. 그 묶음은 창을 닫을 때 안의 프로세스를 함께 끝낼 수 있어,
        # 로봇은 묶음 밖으로 빼서 띄운다. 묶음이 빼기를 허락하지 않으면(거부) 묶음 안에서라도 띄운다.
        try:
            return subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except OSError:
            log.warning("게시 로봇을 작업 묶음 밖으로 띄우지 못해 안에서 띄움 — 서버 창을 닫으면 같이 끝날 수 있음")
            return subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, creationflags=flags)


class LinkRequest(BaseModel):
    url: str


class AutoRequest(BaseModel):
    video_ids: list[str]
    profile: str | None = None  # 직접 고르기일 때 고른 프로필


class OptionsRequest(BaseModel):
    values: dict


class PromptRequest(BaseModel):
    text: str


class PostRequest(BaseModel):
    video_id: str
    profile: str | None = None  # 자동 배정(rotate)이면 비워도 된다


# ── 서버 ──────────────────────────────────────────────────────────────────────
def create_app(services: Services) -> FastAPI:
    videos: dict[str, VideoItem] = {}         # 마지막 불러오기의 영상(설명 포함) — 초안 재료·시도 생성에 쓴다
    robots: dict[int, subprocess.Popen] = {}  # 첫 시도 id → 띄운 게시 로봇
    rising: dict[str, Measured] = {}          # 마지막 뜨기 직전 찾기의 첫 측정 — 다시 측정의 기준

    def profile_logins(settings: Settings) -> tuple[list[str], list[dict]]:
        """(고를 수 있는 프로필 = 지금 로그인되어 있는 것, 화면 표시용 전체 프로필 + 로그인 상태). 기준은 chrome의 확인 결과 하나다."""
        states = services.logins(settings.profiles)
        ready = [p.name for p in settings.profiles if states.get(p.name) is LoginState.LOGGED_IN]
        return ready, [{"name": p.name, "login": states.get(p.name, LoginState.NEEDS_LOGIN).value} for p in settings.profiles]

    def open_store() -> tuple[Settings, Store]:
        settings = services.settings()
        return settings, Store(settings.db_path, settings.test_video_ids)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 서버가 꺼지기 전에 로봇이 잡지 못한 대기 줄은 이제 아무도 잡지 않는다 — 취소로 끝낸다.
        try:
            _, store = open_store()
            cancelled = store.fail_queued(services.now(), RESTARTED)
            if cancelled:
                log.warning("남은 대기 줄을 취소로 정리: %s", cancelled)
        except ConfigError as e:
            log.error("설정 오류로 시작 정리를 건너뜀: %s", e.reasons)
        yield

    app = FastAPI(title="유튜브 댓글 에이전트", lifespan=lifespan)

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        """다른 사이트의 몰래 요청을 막는다 — 상태를 바꾸는 /api/* 요청에 Origin 머리가 있으면 이 대시보드 주소일 때만 받는다.

        브라우저는 다른 사이트 페이지가 이 PC의 대시보드로 요청을 보낼 때 그 사이트 주소를 Origin에 싣는다.
        머리가 없는 요청(같은 PC의 도구 · 테스트)은 받는다.
        """
        origin = request.headers.get("origin")
        if (request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/")
                and origin is not None and origin not in DASHBOARD_ORIGINS):
            return JSONResponse(status_code=403, content={"detail": "이 대시보드에서 온 요청만 받아요."})
        return await call_next(request)

    @app.exception_handler(ConfigError)
    async def config_error(_request: Request, e: ConfigError) -> JSONResponse:
        # 설정 파일이 잘못되면 모든 API가 이유를 돌려준다 — 화면 맨 위 빨간 띠로 보인다.
        return JSONResponse(status_code=500, content={"detail": "설정 파일에 문제가 있어요 — 아래 이유를 고친 뒤 새로고침해 주세요.",
                                                      "reasons": e.reasons})

    def known(video_id: str) -> VideoItem:
        video = videos.get(video_id)
        if video is None:
            raise HTTPException(404, "영상을 먼저 불러와 주세요.")
        return video

    def require_ai(settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise HTTPException(409, NO_AI_KEY)

    def write_draft(settings: Settings, store: Store, video: VideoItem, version: int) -> dict:
        """재료를 받아 작가에게 쓰게 하고 저장한다. 실패는 503 + 이유(화면이 [다시 시도]를 보여 준다)."""
        try:
            ctx = services.youtube(settings).context(video)
            result = services.drafter(settings).write(ctx, store.comparable_texts(video.id), version)
        except (DraftUnavailable, FetchError) as e:
            raise HTTPException(503, f"초안을 만들지 못했어요: {e}") from None
        store.save_draft(result.draft, services.now())
        return _draft_json(store.get_draft(video.id))

    # ── 머리 표시줄 ──
    @app.get("/api/status")
    def status() -> dict:
        settings, store = open_store()
        since, until = guard.quota_window(services.now(), store.last_quota_reset())
        ready, everyone = profile_logins(settings)
        return {
            "today": store.submitted_count(since, until), "daily_max": settings.daily_max,
            "post_mode": settings.post_mode, "dev_mode": settings.dev_mode,
            "channel_posting": settings.channel_posting, "max_retries": settings.max_retries,
            "draft_on_select": settings.draft_on_select,
            # 고를 수 있는 프로필은 지금 유튜브에 로그인되어 있는 것뿐이다(chrome 확인). 자동 배정이면 다음 차례도 알려 준다.
            "profiles": ready, "profile_assign": settings.profile_assign,
            # 화면 표시용 전체 프로필 + 로그인 상태(로그인되어 있음 · 로그인 필요 · 크롬 창 열림).
            "all_profiles": everyone,
            "next_profile": assign.next_profile(ready, store.history(1000))
            if settings.profile_assign == "rotate" and ready else None,
            "ai_key": settings.anthropic_api_key is not None, "youtube_key": settings.youtube_api_key is not None,
            # 화면 안내 문구에 쓰는 기준값 — 화면에 숫자를 박아 두지 않고 설정을 따른다.
            "comment": {"min_chars": settings.comment.min_chars, "max_chars": settings.comment.max_chars,
                        "min_count": settings.comment.min_count, "max_count": settings.comment.max_count},
            "rising": {"keyword": settings.rising.keyword, "hours": settings.rising.published_within_hours,
                       "min_views": settings.rising.min_views, "max_views": settings.rising.max_views,
                       "min_per_hour": settings.rising.min_views_per_hour},
        }

    # ── ① 불러오기 ──
    @app.post("/api/videos/load")
    def load_videos() -> dict:
        settings, store = open_store()
        client = services.youtube(settings)
        tests = client.test_videos(sorted(settings.test_video_ids))
        latest = client.latest_videos(list(settings.channels), settings.per_channel)
        found = latest.videos + tests.videos  # 테스트 영상은 연습용이라 맨 뒤에 둔다
        videos.clear()
        videos.update({v.id: v for v in found})
        rising.clear()  # 목록을 새로 받았으니 지난 뜨기 직전 결과(다시 측정 기준)도 버린다
        failures = {**tests.failures, **latest.failures}
        return {"videos": [_video_json(v, settings, store) for v in found],
                "failures": [f"{name}: {reason}" for name, reason in failures.items()]}

    @app.post("/api/videos/link")
    def add_link(req: LinkRequest) -> dict:
        """① 링크로 영상 추가. 허용 범위는 판정하지 않고 목록 모양(postable)으로 돌려준다 — 판정은 check_target."""
        settings, store = open_store()
        video_id = guard.parse_video_id(req.url)
        if video_id is None:
            raise HTTPException(400, "유튜브 영상 링크가 아니에요. 예: https://youtu.be/영상id")
        # 테스트 영상 id면 테스트 영상으로 가져온다 — 링크로 넣어도 같은 영상이다.
        source = Source.TEST if video_id in settings.test_video_ids else Source.LINK
        result = services.youtube(settings).videos_by_id([video_id], source)
        if not result.videos:
            raise HTTPException(404, f"영상을 찾지 못했어요: {result.failures.get(video_id, '삭제·비공개 영상일 수 있어요')}")
        video = result.videos[0]
        videos[video.id] = video
        return _video_json(video, settings, store)

    # ── ① 뜨기 직전 영상 찾기 ──
    def rising_row(m: Measured, settings: Settings, store: Store, per_hour: float, recent: float | None = None) -> dict:
        return {**_video_json(m.video, settings, store), "views": m.views, "per_hour": round(per_hour),
                "recent_per_hour": None if recent is None else round(recent)}

    @app.post("/api/rising/search")
    def rising_search() -> dict:
        """「연애」 검색 → 조회수 측정 → 기준으로 거름(trend) → 평균 속도 큰 순. 뜨기 직전 영상은 게시 대상이 아니다(D-22)."""
        settings, store = open_store()
        if not settings.youtube_api_key:
            raise HTTPException(409, "뜨기 직전 영상 찾기에는 YouTube API 키가 필요해요(README의 「키 설정」 참고).")
        rules, now = settings.rising, services.now()
        client = services.youtube(settings)
        try:
            ids = client.search_recent(rules.keyword, now - timedelta(hours=rules.published_within_hours))
            measured = client.measure(ids, now)
        except FetchError as e:
            raise HTTPException(503, f"뜨기 직전 영상을 찾지 못했어요: {e}") from None
        picked = []
        for m in measured:
            per_hour = trend.views_per_hour(m.views, m.video.published_at, now)
            if trend.is_rising(m.views, per_hour, m.video.published_at, now, rules):
                picked.append((m, per_hour))
        picked.sort(key=lambda pair: -pair[1])
        rising.clear()
        rising.update({m.video.id: m for m, _ in picked})
        videos.update({m.video.id: m.video for m, _ in picked})
        return {"videos": [rising_row(m, settings, store, per_hour) for m, per_hour in picked],
                "searched": len(ids), "matched": len(picked), "remeasure": rules.remeasure}

    @app.post("/api/rising/remeasure")
    def rising_remeasure() -> dict:
        """선택 기능 「다시 측정」 — 지난 찾기의 조회수를 다시 재서 최근 속도(차이 ÷ 간격) 큰 순(D-24)."""
        settings, store = open_store()
        rules, now = settings.rising, services.now()
        if not rules.remeasure:
            raise HTTPException(409, "다시 측정 기능이 꺼져 있어요.")
        if not rising:
            raise HTTPException(409, "먼저 [뜨기 직전 영상 찾기]를 눌러 주세요.")
        wait = timedelta(minutes=rules.remeasure_min_gap_minutes) - (now - min(m.measured_at for m in rising.values()))
        if wait > timedelta(0):
            raise HTTPException(409, f"처음 잰 뒤 {rules.remeasure_min_gap_minutes}분이 지나야 해요 — "
                                     f"{math.ceil(wait.total_seconds() / 60)}분 뒤에 다시 잴 수 있어요.")
        try:
            again = services.youtube(settings).measure(list(rising), now)
        except FetchError as e:
            raise HTTPException(503, f"조회수를 다시 재지 못했어요: {e}") from None
        rows = []
        for m in again:
            first = rising[m.video.id]
            recent = trend.recent_velocity(first.views, first.measured_at, m.views, now)
            rows.append((m, trend.views_per_hour(m.views, m.video.published_at, now), recent))
        rows.sort(key=lambda row: -row[2])
        return {"videos": [rising_row(m, settings, store, per, recent) for m, per, recent in rows],
                "searched": len(rising), "matched": len(rows), "remeasure": True}

    # ── ② 초안 ──
    @app.get("/api/videos/{video_id}/draft")
    def get_draft(video_id: str) -> dict:
        settings, store = open_store()
        video = known(video_id)
        if not settings.anthropic_api_key:
            # 초안 대신 「키 없음」만 — 저장된 초안도 보여 주지 않는다(D-28).
            return {"video_id": video_id, "ai": False}
        saved = store.get_draft(video_id)
        if saved is not None:
            return _draft_json(saved)
        if not settings.draft_on_select:
            # 고르자마자 쓰기가 꺼져 있다 — AI를 부르지 않고, 화면이 [초안 쓰기]를 보여 준다(비용 절약).
            return {"video_id": video_id, "ai": True, "pending": True}
        return write_draft(settings, store, video, version=1)

    @app.post("/api/videos/{video_id}/rewrite")
    def rewrite(video_id: str) -> dict:
        settings, store = open_store()
        video = known(video_id)
        require_ai(settings)
        current = store.get_draft(video_id)
        return write_draft(settings, store, video, version=(current.version if current else 0) + 1)

    # ── ③ 게시 ──
    def pick_profile(settings: Settings, store: Store, requested: str | None) -> str:
        """배정 방식대로 프로필: 자동 배정이면 로그인된 프로필 중 가장 오래 안 쓴 것, 직접 고르기면 요청한 것(로그인된 것만)."""
        ready, _ = profile_logins(settings)
        if not ready:
            raise HTTPException(400, "로그인한 프로필이 없어요 — [프로필 관리]에서 유튜브에 로그인해 주세요.")
        if settings.profile_assign == "rotate":
            return assign.next_profile(ready, store.history(1000))
        if requested in ready:
            return requested
        raise HTTPException(400, f"고를 수 없는 프로필이에요(로그인되어 있지 않거나 없는 프로필): {requested}")

    def begin_post(settings: Settings, store: Store, video: VideoItem, profile: str) -> int:
        """게시 한 번 시작 — ③ 버튼과 자동 모드가 같이 쓴다. 시도 줄 생성 → 로봇과 같은 검사 → 로봇 띄우기 or 거부 기록."""
        draft = store.get_draft(video.id)
        if draft is None:
            raise HTTPException(409, "이 영상의 초안이 없어요.")
        now = services.now()
        exempt = guard.is_exempt(video, set(settings.test_video_ids), settings.dev_mode)
        aid = store.start_attempt(video, profile, draft.comment, now, exempt=exempt)

        # 로봇과 같은 검사. 걸리면 크롬을 열지 않고 거부를 기록한다 — 화면 진행 칸과 기록에 이유가 남는다.
        verdict = check_post(video, draft.comment, profile, exempt, settings, store, now)
        if not verdict.ok:
            store.set_status(aid, PostStatus.REJECTED, now, reason=" / ".join(verdict.reasons))
            return aid
        try:
            robots[aid] = services.spawn(aid)
        except OSError:
            log.error("게시 로봇을 띄우지 못함 — 시도 %s", aid, exc_info=True)
            # 크롬을 띄울 로봇조차 없으니 바탕화면을 찍어 남긴다(실패하면 이유와 화면 캡처 — poster 명세)
            shot = desktop_shot(settings.screenshot_dir / f"attempt-{aid}", "실행")
            store.set_status(aid, PostStatus.FAILED_BEFORE_SUBMIT, services.now(), reason=SPAWN_FAILED, screenshot=shot)
        return aid

    def settle(store: Store, root_id: int) -> tuple[Attempt, bool]:
        """그 누름의 가장 최근 줄과 로봇이 살아 있나.

        로봇 프로세스가 끝났는데 줄이 안 끝났으면(시작 전에 죽음 · 중간에 죽음) 그 줄을 실패로 끝낸다 —
        화면이 「진행 중」을 영원히 묻지 않게. 버튼을 눌렀던 줄이면 버튼 후 실패로 적힌다(store.fail_stopped).
        """
        latest = store.latest_in_root(root_id)
        robot = robots.get(root_id)
        alive = robot is not None and robot.poll() is None
        if robot is not None and not alive and not latest.status.finished:
            reason = ROBOT_DIED if latest.status is PostStatus.QUEUED else ROBOT_STOPPED
            store.fail_stopped(latest.id, services.now(), reason)
            latest = store.get(latest.id)
        return latest, alive

    # ── 프로필 관리 (크롬 열기 · 로그인 표시) ──
    def find_profile(settings: Settings, name: str) -> Profile:
        profile = next((p for p in settings.profiles if p.name == name), None)
        if profile is None:
            raise HTTPException(404, f"설정에 없는 프로필이에요: {name}")
        return profile

    @app.post("/api/profiles/{name}/open")
    def open_profile(name: str) -> dict:
        """프로필 관리 [크롬 열기] — 그 프로필 폴더로 크롬 창을 연다(로그인용). 게시 로봇이 도는 중이면 열지 않는다."""
        settings = services.settings()
        profile = find_profile(settings, name)
        # 같은 프로필 폴더를 크롬 둘이 열면 게시 로봇의 크롬이 뜨지 못한다 — 게시가 끝난 뒤 열게 한다.
        if any(robot.poll() is None for robot in robots.values()):
            raise HTTPException(409, "댓글을 다는 중이에요. 게시가 끝난 뒤 프로필 창을 열어 주세요.")
        try:
            services.open_profile(profile)
        except LookupError as e:
            raise HTTPException(409, str(e)) from None
        return {"ok": True}

    @app.get("/api/profiles")
    def profiles() -> dict:
        """프로필 로그인 상태 감시 — 화면이 몇 초마다 묻는다. 로그인하고 창을 닫으면 다음 물음에서 「로그인되어 있음」."""
        ready, everyone = profile_logins(services.settings())
        return {"profiles": ready, "all_profiles": everyone}

    @app.post("/api/posts")
    def start_post(req: PostRequest) -> dict:
        settings, store = open_store()
        video = known(req.video_id)
        require_ai(settings)
        profile = pick_profile(settings, store, req.profile)
        return {"attempt_id": begin_post(settings, store, video, profile)}

    @app.get("/api/posts/{attempt_id}")
    def post_status(attempt_id: int) -> dict:
        settings, store = open_store()
        try:
            latest, alive = settle(store, attempt_id)
        except LookupError:
            raise HTTPException(404, "없는 시도예요.") from None
        # 버튼 전 실패 줄이 최신이어도 로봇이 살아 있으면 곧 재시도 줄이 생긴다 — 아직 끝나지 않았다.
        retrying = latest.status is PostStatus.FAILED_BEFORE_SUBMIT and alive
        # 거부 줄이면 그 영상에 먼저 단 게시(영상당 1개 열이 채워진 줄)를 함께 준다 — 화면 ③ 「막혔어요」의 「먼저 단 댓글」.
        earlier = store.posted_attempt(latest.video.id) if latest.status is PostStatus.REJECTED else None
        return {**_attempt_json(latest, settings), "steps": [s.label for s in STEPS],
                "step": _step(latest.status), "done": latest.status.finished and not retrying,
                "max_retries": settings.max_retries,
                "previous": None if earlier is None else {
                    "time": _kst(earlier.created_at, "%Y-%m-%d %H:%M"), "profile": earlier.profile,
                    "comment": earlier.text, "comment_url": earlier.comment_url}}

    # ── 자동 모드 (D-25) ──
    jobs: dict[str, AutoJob] = {}  # "last" → 지금(또는 마지막) 자동 실행
    auto_lock = threading.Lock()

    def wait_post(root_id: int) -> Attempt:
        """그 누름이 끝날 때까지 기다린다 — 로봇 프로세스가 끝나면 마지막 줄을 돌려준다(거부면 곧바로)."""
        robot = robots.get(root_id)
        if robot is not None:
            robot.wait()
        _, store = open_store()
        return settle(store, root_id)[0]

    @app.post("/api/auto")
    def start_auto(req: AutoRequest) -> dict:
        settings, store = open_store()
        if settings.post_mode != "auto":
            raise HTTPException(409, "지금은 수동 모드라 자동으로 달 수 없어요.")
        require_ai(settings)
        items = [AutoItem(known(vid)) for vid in req.video_ids]
        if not items:
            raise HTTPException(400, "자동으로 달 영상을 하나 이상 체크해 주세요.")
        pick_profile(settings, store, req.profile)  # 고를 수 없는 프로필이면 시작 전에 400
        job = AutoJob(items, profile=req.profile)
        # 「돌고 있나 확인 → 등록」을 한 번에 — 요청 둘이 동시에 와도 자동 실행은 하나만 돈다.
        with auto_lock:
            current = jobs.get("last")
            if current is not None and not current.finished:
                raise HTTPException(409, "자동 실행이 이미 돌고 있어요. 끝나거나 멈춘 뒤 다시 눌러 주세요.")
            jobs["last"] = job

        def write(s: Settings, st: Store, video: VideoItem) -> Draft:
            saved = st.get_draft(video.id)
            if saved is not None:
                return saved
            write_draft(s, st, video, version=1)
            return st.get_draft(video.id)

        deps = AutoDeps(open_store=open_store, write_draft=write, begin_post=begin_post,
                        pick_profile=lambda s, st: pick_profile(s, st, job.profile), wait_post=wait_post,
                        wait=job._stop.wait, now=services.now)
        services.run_background(lambda: run_auto(job, deps))
        return job.snapshot()

    @app.get("/api/auto")
    def auto_status() -> dict:
        job = jobs.get("last")
        return job.snapshot() if job else {"finished": True, "stopping": False, "items": []}

    @app.post("/api/auto/stop")
    def stop_auto() -> dict:
        job = jobs.get("last")
        if job is None or job.finished:
            raise HTTPException(409, "돌고 있는 자동 실행이 없어요.")
        job.stop()
        return job.snapshot()

    # ── 오늘 게시 수 초기화(개발 모드 전용) ──
    @app.post("/api/quota/reset")
    def reset_quota() -> dict:
        """오늘 게시 수를 0부터 다시 센다. 기록은 지우지 않는다. 하루 상한은 운영 규칙이라 개발 모드에서만 받는다."""
        settings, store = open_store()
        if not settings.dev_mode:
            raise HTTPException(409, "개발 모드에서만 초기화할 수 있어요.")
        store.reset_quota(services.now())
        return {"ok": True}

    # ── 옵션 페이지 ──
    @app.get("/api/options")
    def get_options() -> dict:
        """옵션 페이지가 그릴 값 — config.yaml 값 그대로 · 댓글 작성 지침 · 지침에 쓸 수 있는 자리표시."""
        return {"values": read_options(services.config_path), "prompt": read_prompt(services.config_path),
                "prompt_fields": list(PROMPT_FIELDS)}

    @app.put("/api/options")
    def put_options(req: OptionsRequest) -> dict:
        """옵션 저장 — 검사(load_settings)를 통과해야 config.yaml이 바뀐다. 걸리면 409 + 이유, 파일 그대로."""
        try:
            save_options(services.config_path, req.values)
        except ConfigError as e:
            return JSONResponse(status_code=409, content={"detail": "저장하지 못했어요 — 아래 이유를 고쳐 주세요.", "reasons": e.reasons})
        return {"ok": True}

    @app.put("/api/options/prompt")
    def put_prompt(req: PromptRequest) -> dict:
        """댓글 작성 지침 저장 — 자리표시 검사를 통과해야 쓴다. 다음 초안부터 바로 쓰인다."""
        try:
            save_prompt(services.config_path, req.text)
        except ConfigError as e:
            return JSONResponse(status_code=409, content={"detail": "지침을 저장하지 못했어요.", "reasons": e.reasons})
        return {"ok": True}

    # ── 기록 ──
    @app.get("/api/history")
    def history() -> list[dict]:
        settings, store = open_store()
        return [_attempt_json(a, settings) for a in store.history(100)]

    @app.get("/shots/{name}")
    def shot(name: str) -> FileResponse:
        settings = services.settings()
        path = settings.screenshot_dir / name
        if Path(name).name != name or not path.is_file():  # 폴더 밖 파일을 열지 못하게 이름만 받는다
            raise HTTPException(404, "캡처가 없어요.")
        return FileResponse(path)

    # 화면 파일. API 경로를 먼저 등록한 뒤 마지막에 붙여야 /api/* 가 가려지지 않는다.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


# ── 응답 모양 ─────────────────────────────────────────────────────────────────
def _kst(dt: datetime | None, fmt: str) -> str:
    return dt.astimezone(guard.KST).strftime(fmt) if dt else ""


def _video_json(v: VideoItem, settings: Settings, store: Store) -> dict:
    target = guard.check_target(v, {c.id for c in settings.channels}, set(settings.test_video_ids),
                                settings.channel_posting)
    return {"id": v.id, "title": v.title, "channel": v.channel_name, "date": _kst(v.published_at, "%Y-%m-%d"),
            "thumbnail": v.thumbnail_url, "source": v.source.value, "view_count": v.view_count,
            "test": v.source is Source.TEST, "link": v.source is Source.LINK,
            "posted": store.has_posted(v.id),
            "postable": target.ok, "block_reason": " ".join(target.reasons)}


def _draft_json(d: Draft) -> dict:
    return {"video_id": d.video_id, "ai": True, "worry": d.worry, "comment": d.comment, "version": d.version,
            "issues": list(d.issues), "transcript_chars": d.transcript_chars, "comments_read": d.comments_read}


def _attempt_json(a: Attempt, settings: Settings) -> dict:
    s = a.status
    result = "success" if s is PostStatus.CONFIRMED else "fail" if s.finished else "progress"
    shot = f"/shots/{a.screenshot.name}" if a.screenshot and (settings.screenshot_dir / a.screenshot.name).is_file() else None
    return {"id": a.id, "root_id": a.root_id, "time": _kst(a.created_at, "%Y-%m-%d %H:%M:%S"),
            "video": a.video.title, "channel": a.video.channel_name, "profile": a.profile, "comment": a.text,
            "status": s.value, "label": s.label, "result": result, "ok": s is PostStatus.CONFIRMED,
            "tone": s.tone,  # 화면 갈래(끝남·손봐야·막힘·진행) — 화면은 상태를 다시 나누지 않고 이것으로 색을 고른다
            "reason": a.reason, "comment_url": a.comment_url, "screenshot": shot, "retry_no": a.retry_no,
            "text": a.text, "typed": a.typed_chars, "total": len(a.text)}


def _step(status: PostStatus) -> int:
    """진행 칸에서 지금 몇 번째 단계인가. 대기는 첫 단계, 실패·거부는 -1(실패 칸이 따로 보인다)."""
    if status is PostStatus.QUEUED:
        return 0
    return STEPS.index(status) if status in STEPS else -1


app = create_app(Services.real())
