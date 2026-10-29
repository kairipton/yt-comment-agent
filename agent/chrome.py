"""프로필 크롬 — 크롬 찾기 · 프로필 창 열기 · 유튜브 로그인 상태 확인. 명세: docs/systems/chrome.md

게시에 쓰는 크롬 프로필은 별도 폴더(user-data-dir)다. 로그인 여부는 설정에 적지 않고 여기서 그때그때 확인한다.

로그인 확인 방법:
  프로필 폴더의 크롬 쿠키 파일(SQLite)을 읽기 전용으로 열어, youtube.com 로그인 쿠키(LOGIN_INFO · SID)가
  만료되지 않은 채 있는지 본다. 쿠키 값(암호화됨)은 읽지 않고 이름 · 호스트 · 만료 시각만 본다.
  그 프로필의 크롬 창이 열려 있으면 크롬이 파일을 잠가 열 수 없다 → 「크롬 창 열림」으로 알린다.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agent.models import LoginState, Profile

YOUTUBE = "https://www.youtube.com"
# 로그인되어 있으면 youtube.com에 생기는 쿠키. 둘 중 하나라도 살아 있으면 로그인으로 본다.
LOGIN_COOKIES = ("LOGIN_INFO", "SID")
# 크롬 쿠키 파일 자리 — 새 크롬은 Network 아래, 옛 크롬은 Default 바로 아래.
COOKIE_FILES = (Path("Default") / "Network" / "Cookies", Path("Default") / "Cookies")
_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)  # 크롬 시각의 기준점(이때부터 마이크로초)


# ── 크롬 찾기 · 창 열기 ────────────────────────────────────────────────────────
def chrome_candidates() -> list[Path]:
    """크롬이 흔히 깔리는 자리 셋 — run_dashboard.bat과 같은 자리를 본다."""
    bases = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")]
    return [Path(b) / "Google" / "Chrome" / "Application" / "chrome.exe" for b in bases if b]


def find_chrome(candidates: list[Path]) -> Path | None:
    """후보 중 실제로 있는 첫 크롬. 없으면 None."""
    return next((c for c in candidates if c.is_file()), None)


def profile_args(profile: Profile) -> list[str]:
    """프로필 크롬을 여는 인수 — 그 프로필 폴더(user-data-dir)로 유튜브를 연다. 로그인할 때 쓴다."""
    return [f"--user-data-dir={profile.user_data_dir}", YOUTUBE]


def open_profile_window(profile: Profile) -> None:
    """그 프로필 폴더로 크롬 창을 연다(대시보드 [프로필 관리]의 [크롬 열기]). 크롬을 못 찾으면 LookupError."""
    chrome = find_chrome(chrome_candidates())
    if chrome is None:
        raise LookupError("구글 크롬을 찾지 못했어요.")
    subprocess.Popen([str(chrome), *profile_args(profile)])


# ── 로그인 확인 ────────────────────────────────────────────────────────────────
def logged_in_cookies(rows: list[tuple[str, str, int]], now: datetime) -> bool:
    """쿠키 줄(호스트, 이름, 만료)로 판정한다. youtube.com의 로그인 쿠키가 만료 전이면 참. 만료 0은 세션 쿠키라 유효."""
    now_chrome = int((now - _CHROME_EPOCH).total_seconds() * 1_000_000)
    return any(host.endswith("youtube.com") and name in LOGIN_COOKIES and (expires == 0 or expires > now_chrome)
               for host, name, expires in rows)


def _read_cookie_rows(path: Path) -> list[tuple[str, str, int]]:
    """쿠키 파일에서 youtube.com 쿠키의 (호스트, 이름, 만료)만 읽는다. 크롬이 잠가 두었으면 sqlite3.OperationalError."""
    # 읽기 전용으로 연다 — 크롬의 파일을 바꾸지 않게.
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT host_key, name, expires_utc FROM cookies WHERE host_key LIKE '%youtube.com'").fetchall()
    finally:
        conn.close()


def login_state(profile: Profile, now: datetime) -> LoginState:
    """그 프로필이 지금 유튜브에 로그인되어 있는지. 크롬 창이 열려 있어 파일을 못 읽으면 WINDOW_OPEN."""
    path = next((profile.user_data_dir / f for f in COOKIE_FILES if (profile.user_data_dir / f).is_file()), None)
    if path is None:
        return LoginState.NEEDS_LOGIN  # 한 번도 연 적 없는 프로필 — 쿠키 파일이 아직 없다
    try:
        rows = _read_cookie_rows(path)
    except (sqlite3.Error, OSError):
        return LoginState.WINDOW_OPEN
    return LoginState.LOGGED_IN if logged_in_cookies(rows, now) else LoginState.NEEDS_LOGIN


def login_states(profiles: tuple[Profile, ...], now: datetime) -> dict[str, LoginState]:
    """프로필 이름 → 로그인 상태. 대시보드가 몇 초마다 부른다(프로필 셋에 1초 안쪽)."""
    return {p.name: login_state(p, now) for p in profiles}
