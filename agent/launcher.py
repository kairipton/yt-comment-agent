"""원클릭 실행기의 파이썬 쪽 — run_dashboard.bat이 uv와 부품을 준비한 뒤 `uv run python -m agent.launcher`로 부른다. 명세: docs/systems/launcher.md

흐름: 크롬 확인 → .env 없으면 만들기 → 설정 읽기(잘못됐으면 멈춤)
     → 이미 서버가 켜져 있으면 대시보드만 열고 끝 / 아니면 서버를 켜고, 응답하면 대시보드를 열고 이 창을 최소화.
이 창을 닫으면 서버가 끝난다. 그때 하던 게시 한 건은 게시 로봇이 따로 끝까지 마친다(dashboard 명세).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from agent.chrome import chrome_candidates, find_chrome
from agent.config import ConfigError, load_settings

ROOT = Path(__file__).resolve().parents[1]
PORT = 8501
URL = f"http://localhost:{PORT}"


# ── 경로 계산(단위 테스트 대상) ────────────────────────────────────────────────
def ensure_env(root: Path) -> bool:
    """.env가 없으면 .env.example을 복사해 만든다(값은 비어 있음). 만들었으면 True. 있는 파일은 건드리지 않는다."""
    env = root / ".env"
    if env.exists():
        return False
    shutil.copyfile(root / ".env.example", env)
    return True


# ── 바깥과 닿는 부분(수동 확인) ────────────────────────────────────────────────
def server_running(port: int) -> bool:
    """그 포트에서 우리 서버가 응답하나(/api/status가 우리 모양의 답을 주나)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as res:
            return "daily_max" in json.loads(res.read().decode("utf-8"))
    except (OSError, ValueError):
        return False


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_dashboard(chrome: Path) -> None:
    """평소 크롬(기본 프로필)으로 대시보드를 연다. 게시용 프로필 창과는 별개다."""
    subprocess.Popen([str(chrome), URL])


def _minimize_console() -> None:
    """이 창을 최소화한다. Windows 터미널 앱이 창을 맡는 경우엔 안 될 수 있다 — 그러면 그냥 둔다."""
    if sys.platform != "win32":
        return
    import ctypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 6)  # 6 = SW_MINIMIZE


def _open_when_ready(chrome: Path) -> None:
    """서버가 응답할 때까지(최대 60초) 기다렸다가 대시보드를 열고 이 창을 최소화한다."""
    for _ in range(120):
        if server_running(PORT):
            open_dashboard(chrome)
            print(f"대시보드를 열었어요: {URL}  — 이 창을 닫으면 프로그램이 끝나요.")
            time.sleep(1)
            _minimize_console()
            return
        time.sleep(0.5)
    print("서버가 60초 안에 켜지지 않았어요. 위의 메시지를 확인해 주세요.")


def main() -> int:
    """종료 코드 0 = 정상. 0이 아니면 run_dashboard.bat이 창을 멈춰 이유를 보여 준다."""
    chrome = find_chrome(chrome_candidates())
    if chrome is None:
        print("[!] 구글 크롬이 필요해요. https://www.google.com/chrome 에서 설치한 뒤 다시 실행해 주세요.")
        return 1

    if ensure_env(ROOT):
        print(".env 파일을 만들었어요. API 키를 넣는 방법은 대시보드가 안내해요.")

    try:
        settings = load_settings(ROOT / "config.yaml", ROOT / ".env")
    except ConfigError as e:
        print("[!] 설정 파일(config.yaml)에 문제가 있어요:")
        for reason in e.reasons:
            print(f"   - {reason}")
        return 1

    # 프로필 로그인은 대시보드 [프로필 관리]에서 한다 — 바탕화면 바로가기는 만들지 않는다.
    print(f"프로필 {len(settings.profiles)}개 — 유튜브 로그인은 대시보드의 [프로필 관리]에서 해요.")

    if server_running(PORT):
        print("이미 켜져 있어요. 대시보드만 엽니다.")
        open_dashboard(chrome)
        return 0
    if port_in_use(PORT):
        print(f"[!] {PORT}번 포트를 다른 프로그램이 쓰고 있어요. 그 프로그램을 끄고 다시 실행해 주세요.")
        return 1

    import uvicorn

    sys.path.insert(0, str(ROOT))  # app.py가 저장소 뿌리에 있다
    threading.Thread(target=_open_when_ready, args=(chrome,), daemon=True).start()
    print("서버를 켜는 중이에요…")
    # 이 컴퓨터에서만 접속된다(127.0.0.1) — 다른 기기에서 내 게시 로봇을 부르지 못하게.
    uvicorn.run("app:app", host="127.0.0.1", port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
