"""PostToolUse 훅: 파이썬 코드·테스트·pytest 설정이 바뀌면 즉시 pytest를 돌린다.

통과 → exit 0(조용). 실패 → exit 2 + stderr(실패 이유) → Claude가 받아 고친다.

⚠ 종료 코드만 믿지 않는다. 테스트 파일이 있는데 **0개가 실행되면 실패**로 본다.
  이 프로젝트는 한글 접두어(기능_·예외_·경계_)로만 테스트를 수집하므로, 이름을 잘못 지으면
  테스트가 조용히 빠지고 pytest는 「테스트 없음」으로 끝난다 — 그때 초록불로 착각하면 그물이 사라진다.
⚠ 실행 수가 직전보다 줄면 막지는 않고 알린다. 테스트 삭제는 정당한 작업일 수 있지만,
  **조용히** 줄어드는 것은 막아야 한다.
⚠ 실패 메시지는 앞부분을 남긴다. 실패가 여럿이면 먼저 난 것이 대개 근본 원인이다.
⚠ Bash로 파일을 고친 경우는 보지 않는다(경로를 명령 문자열에서 가려낼 수 없다).
  그 편집 뒤에는 직접 `uv run pytest`를 돌린다.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE = ROOT / ".claude" / "hooks" / ".state"
LAST_LOG = STATE / "last-test.log"

# pytest 결과에 영향을 주는 파일만 본다(문서·설정 YAML 편집엔 돌지 않는다).
TRIGGER = re.compile(r"(\.py|pyproject\.toml)$")


def edited_path(payload: dict) -> str:
    """도구 입력에서 편집 대상 경로를 꺼낸다. 없으면 빈 문자열."""
    tool_input = payload.get("tool_input") or {}
    return str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")


def has_test_files() -> bool:
    """tests/ 아래에 테스트 파일이 하나라도 있는가. 없으면 아직 테스트를 쓰기 전이다."""
    tests = ROOT / "tests"
    return tests.is_dir() and any(tests.rglob("test_*.py"))


def count_ran(output: str) -> int:
    """pytest 요약 줄(예: `3 passed, 1 failed in 0.1s`)에서 실행된 테스트 수를 센다."""
    total = 0
    for num, _kind in re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)", output):
        total += int(num)
    return total


def previous_count() -> int | None:
    """직전 초록 실행의 테스트 수. 기록이 없거나 직전이 실패였으면 None."""
    if not LAST_LOG.exists():
        return None
    m = re.search(r"통과 — (\d+)개", LAST_LOG.read_text(encoding="utf-8"))
    return int(m.group(1)) if m else None


def write_log(text: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LAST_LOG.write_text(f"[{stamp}] {text}\n", encoding="utf-8")


def misnamed_tests() -> list[str]:
    """`def test_…`로 지은 맨 바깥 함수 — 이 프로젝트에선 수집되지 않으므로 이름 실수다.

    들여 쓴 메서드(가짜 클래스의 `test_videos` 같은 것)는 원래 수집 대상이 아니라 보지 않는다.
    """
    found = []
    for f in (ROOT / "tests").rglob("test_*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"def test_", line):
                found.append(f"{f.relative_to(ROOT).as_posix()}:{i}")
    return found


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError as e:
        # 경로를 모르면 돌릴지 말지도 모른다 — 조용히 넘기지 않고 알린다(exit 1: 막지 않는 오류).
        print(f"⚠ run_tests 훅이 입력을 읽지 못해 테스트를 건너뛰었습니다: {e}", file=sys.stderr)
        return 1
    if not TRIGGER.search(edited_path(payload)):
        return 0
    if not has_test_files():
        return 0  # 테스트를 쓰기 전 단계. 돌릴 그물이 없다.

    proc = subprocess.run(
        ["uv", "run", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        # Windows 기본 출력 인코딩(cp949)이면 한글 테스트 이름·메시지가 깨진다 — UTF-8로 고정한다.
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    out = proc.stdout + proc.stderr
    ran = count_ran(out)

    wrong = misnamed_tests()
    if wrong:
        print("⚠ `def test_…`는 수집되지 않습니다(이름은 기능_·예외_·경계_ — docs/TESTING.md):",
              file=sys.stderr)
        print("\n".join(f"   • {w}" for w in wrong), file=sys.stderr)

    if proc.returncode == 0 and ran > 0:
        prev = previous_count()
        shrink = f" ⚠ 직전 {prev}개 → {ran}개로 줄었습니다" if prev and ran < prev else ""
        write_log(f"✅ 통과 — {ran}개{shrink}")
        if shrink or wrong:
            print(f"✅ pytest 통과 — {ran}개{shrink}. 의도한 변화가 아니면 보고하세요.", file=sys.stderr)
            return 2
        return 0

    if proc.returncode == 5 or ran == 0:
        write_log("❌ 실패 — 테스트 파일은 있는데 0개가 실행됐다")
        print("❌ pytest: 테스트 파일은 있는데 0개가 실행됐습니다 — 통과가 아니라 그물이 사라진 것입니다.",
              file=sys.stderr)
        print("   테스트 이름 접두어(기능_·예외_·경계_)와 수집 오류를 확인하세요.", file=sys.stderr)
        print("\n".join(out.splitlines()[:20]), file=sys.stderr)
        return 2

    fails = [ln for ln in out.splitlines()
             if re.search(r"FAILED|ERROR|Error|assert|^E ", ln)][:30]
    write_log("❌ 실패\n" + "\n".join(fails))
    print("❌ pytest 실패 — 회귀가 발생했습니다:", file=sys.stderr)
    print("\n".join(fails) or out[-2000:], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
