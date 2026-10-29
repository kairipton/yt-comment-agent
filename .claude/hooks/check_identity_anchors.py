"""Stop 훅: 명세가 「이 성질을 지킨다」며 부른 테스트가 실제로 있는지 본다.

왜 필요한가:
  명세의 정체성 문장(「한 영상에는 댓글 1개만」 같은 것)은 글일 뿐이라 코드가 그 성질을 잃어도 조용하다.
  그래서 문장마다 **지키는 테스트 이름**을 백틱으로 병기한다(docs/SPEC-WRITE-GUIDE.md §정체성).
  병기한 테스트가 삭제·개명되면 그 문장은 다시 아무도 안 지키는 글이 된다 — 그 순간을 잡는다.

보는 것: docs/systems/**/*.md 안의 백틱 속 한글 테스트 이름(기능_·예외_·경계_로 시작)
        ↔ tests/**/*.py 의 `def 기능_…` 선언.

출구(Stop 훅의 출력 규칙 때문에 셋으로 갈린다):
  · 어긋남 + 첫 턴      → stderr + exit 2. 턴을 되돌리고 그 내용이 Claude에게 간다.
  · 어긋남 + 되돌아온 턴 → 막지 않고 stdout JSON `systemMessage`로 사용자에게 알린다.
    (exit 0의 stderr는 어디에도 안 보인다 — 거기 쓰면 지적이 유실된다.)
  · 검사 자체가 죽음    → 「지적 없음」으로 읽지 않고 `systemMessage`로 「검사 꺼짐」을 알린다.
⚠ stdout에는 JSON만 내보낸다. 다른 출력이 섞이면 파싱이 깨져 알림이 통째로 사라진다.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
SPECS = ROOT / "docs" / "systems"
TESTS = ROOT / "tests"

ANCHOR = re.compile(r"`((?:기능|예외|경계)_[^`\s]+)`")
DECLARED = re.compile(r"^\s*def\s+((?:기능|예외|경계)_\w+)\s*\(", re.MULTILINE)


def notice(message: str) -> None:
    """막지 않고 사용자 화면에만 띄운다."""
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))


def find_missing() -> list[str]:
    """명세가 부른 테스트 중 선언이 없는 것을 `이름 ← 파일:줄` 꼴로 돌려준다."""
    if not SPECS.is_dir():
        return []
    declared = set()
    if TESTS.is_dir():
        for f in TESTS.rglob("*.py"):
            declared.update(DECLARED.findall(f.read_text(encoding="utf-8")))

    missing, seen = [], set()
    for spec in sorted(SPECS.rglob("*.md")):
        for i, line in enumerate(spec.read_text(encoding="utf-8").splitlines(), 1):
            for name in ANCHOR.findall(line):
                if name not in declared and name not in seen:
                    seen.add(name)
                    missing.append(f"{name}  ←  {spec.relative_to(ROOT).as_posix()}:{i}")
    return missing


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8") or "{}"
    retry = bool(json.loads(raw).get("stop_hook_active"))

    try:
        missing = find_missing()
    except Exception as e:  # 검사가 죽으면 「지적 없음」이 아니라 「검사 꺼짐」이다.
        notice(f"정체성 앵커 검사가 돌지 못했습니다({type(e).__name__}: {e}) — 검사가 꺼진 상태입니다.")
        return 0

    if not missing:
        return 0

    lines = "\n".join(f"   • {m}" for m in missing)
    if retry:
        notice(f"📄 정체성 앵커 {len(missing)}건이 아직 무방비입니다(되돌아온 턴이라 막지 않음):\n{lines}")
        return 0

    print("🛑 정체성 앵커 대조: 명세가 부른 테스트가 tests/ 에 없습니다.", file=sys.stderr)
    print(lines, file=sys.stderr)
    print("   테스트를 지웠거나 이름을 바꿨다면 그 성질이 지금 무방비입니다.", file=sys.stderr)
    print("   앵커를 일부러 거둔 것이라면 명세를 고치고 그 사실을 사용자에게 보고하세요.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
