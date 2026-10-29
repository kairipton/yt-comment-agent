"""chrome 테스트 — 크롬 찾기 · 프로필 창 인수 · 유튜브 로그인 상태 확인. 실제 크롬은 띄우지 않는다.

로그인 확인은 임시 폴더에 크롬 쿠키 파일과 같은 꼴의 SQLite를 만들어 읽힌다.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agent import chrome
from agent.chrome import find_chrome, logged_in_cookies, login_state, profile_args
from agent.models import LoginState, Profile

NOW = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
# 크롬 시각 = 1601-01-01부터 마이크로초. NOW보다 1년 뒤 · 1년 전.
FUTURE = int((datetime(2027, 10, 21, tzinfo=UTC) - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000)
PAST = int((datetime(2025, 10, 21, tzinfo=UTC) - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000)


def 쿠키파일(folder: Path, rows: list[tuple[str, str, int]]) -> Profile:
    """프로필 폴더 안 Default/Network/Cookies에 (호스트, 이름, 만료) 줄을 담은 쿠키 표를 만들고 그 프로필을 돌려준다."""
    path = folder / "Default" / "Network" / "Cookies"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, expires_utc INTEGER, encrypted_value BLOB)")
        conn.executemany("INSERT INTO cookies VALUES (?, ?, ?, x'00')", rows)
    conn.close()
    return Profile("유튜브1", folder)


def 기능_로그인확인_유튜브_로그인쿠키가_있으면_로그인됨이다(tmp_path):
    """목적: 만료되지 않은 유튜브 로그인 쿠키(LOGIN_INFO · SID)가 있으면 「로그인되어 있음」이다."""
    profile = 쿠키파일(tmp_path / "yt1", [(".youtube.com", "LOGIN_INFO", FUTURE), (".google.com", "NID", FUTURE)])
    assert login_state(profile, NOW) is LoginState.LOGGED_IN


def 경계_로그인확인_만료된_쿠키는_로그인이_아니다():
    """목적: 만료된 유튜브 쿠키와 다른 사이트 쿠키만 있으면 로그인이 아니다. 만료 0(세션 쿠키)은 유효하다."""
    assert logged_in_cookies([(".youtube.com", "SID", PAST), (".google.com", "SID", FUTURE)], NOW) is False
    assert logged_in_cookies([(".youtube.com", "SID", 0)], NOW) is True


def 기능_로그인확인_프로필폴더가_없으면_로그인필요다(tmp_path):
    """목적: 한 번도 연 적 없는 프로필(폴더·쿠키 파일 없음)은 「로그인 필요」다."""
    assert login_state(Profile("유튜브2", tmp_path / "없는폴더"), NOW) is LoginState.NEEDS_LOGIN


def 기능_로그인확인_파일을_못열면_창열림이다(tmp_path, monkeypatch):
    """목적: 쿠키 파일이 있는데 열 수 없으면(그 프로필 크롬 창이 열려 크롬이 잠금) 「크롬 창 열림」이다."""
    profile = 쿠키파일(tmp_path / "yt1", [(".youtube.com", "SID", FUTURE)])

    def 잠김(path):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(chrome, "_read_cookie_rows", 잠김)
    assert login_state(profile, NOW) is LoginState.WINDOW_OPEN


def 기능_크롬_프로필창은_그_폴더로_유튜브를_연다():
    """목적: 프로필 크롬을 열 때 인수가 그 프로필 폴더(--user-data-dir)와 유튜브를 가리킨다 — 로그인한 그 프로필이 열리게."""
    profile = Profile("유튜브2", Path("C:/chrome-profiles/yt2"))
    assert profile_args(profile) == [f"--user-data-dir={Path('C:/chrome-profiles/yt2')}", "https://www.youtube.com"]


def 기능_크롬_후보에_없으면_None이다(tmp_path):
    """목적: 후보 자리에 크롬이 없으면 None — 실행기가 「크롬이 필요해요」로 멈추게. 있으면 그 경로."""
    missing = [tmp_path / "a" / "chrome.exe", tmp_path / "b" / "chrome.exe"]
    assert find_chrome(missing) is None
    missing[1].parent.mkdir()
    missing[1].write_bytes(b"")
    assert find_chrome(missing) == missing[1]
