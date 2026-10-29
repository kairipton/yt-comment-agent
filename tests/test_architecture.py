"""구조 규칙 테스트: 결정론이어야 하는 부품이 바깥 세계(입출력·시각·난수)에 손대지 않는지 본다.

문지기(guard)의 계약은 「같은 입력 → 같은 판정」이다. 판정이 인터넷·DB·현재 시각·난수에 기대면
같은 상황에서도 결과가 달라지고, 테스트로 재현할 수도 없게 된다.
그래서 필요한 값(오늘 게시 수 · 현재 시각 등)은 부르는 쪽이 인자로 넘기고, 부품은 판단만 한다.

⚠ 보는 것: import 문(모듈 이름의 첫 마디)과 now()/today()/time() 같은 시각 읽기 호출.
⚠ 안 보는 것: `__import__`·`importlib`로 실행 중에 불러오는 것, 인자로 받은 객체가 몰래 하는 입출력.
  이 둘은 코드 검토(intent 에이전트)가 맡는다.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 결정론이어야 하는 부품(D-19 · docs/system-map.md). 아직 없는 부품은 이유를 남기고 건너뛴다.
PURE_MODULES = [ROOT / "agent" / name for name in ("models.py", "guard.py", "assign.py", "trend.py")]

# 결정론 부품이 불러와도 되는 우리 부품. 나머지(store·config·fetch·draft·poster·auto)는 입출력을 품고 있어
# 이름이 금지 목록에 없어도 불러오는 순간 결정론이 깨진다.
ALLOWED_INTERNAL = {"agent.models"}

# 바깥 세계에 닿는 모듈 — 화면 · 브라우저 · DB · 네트워크 · 외부 API · 프로세스 · 환경 · 시각 · 난수.
BANNED_IMPORTS = {
    "fastapi", "starlette", "uvicorn", "streamlit", "playwright", "sqlite3", "socket", "urllib", "http", "requests", "httpx",
    "googleapiclient", "youtube_transcript_api", "anthropic", "subprocess", "os", "time",
    "random", "secrets", "uuid",
}

# 현재 시각을 스스로 읽는 호출 이름. (datetime 타입을 쓰는 것은 괜찮다 — 읽는 것만 막는다.)
CLOCK_CALLS = {"now", "today", "utcnow", "time", "monotonic", "perf_counter"}


def _parse(path: Path) -> ast.Module:
    """부품 파일을 구문 트리로 읽는다. 아직 없는 부품이면 건너뛴다(이유가 결과에 보인다)."""
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT).as_posix()} 아직 없음")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", PURE_MODULES, ids=lambda p: p.name)
def 기능_결정론부품_입출력모듈을_불러오지_않는다(path):
    """목적: 결정론 부품이 화면·브라우저·DB·네트워크·난수 모듈을 import하지 않는지 확인한다."""
    tree = _parse(path)
    found = []
    for node in ast.walk(tree):
        # `import a.b` 와 `from a.b import c` 둘 다 첫 마디(a)로 판정한다.
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        found += [f"{n} (줄 {node.lineno})" for n in names if n.split(".")[0] in BANNED_IMPORTS]
    assert not found, f"{path.name}이 바깥 세계 모듈을 불러온다: {found}"


def _internal_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """`agent.…` 부품 import를 「agent.이름」 꼴로 모은다. 상대 import(`from .store import x`)도 같은 꼴로 바꾼다."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(a.name, node.lineno) for a in node.names if a.name.split(".")[0] == "agent"]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:  # from .store import x · from . import store
                base = f"agent.{node.module}" if node.module else None
                found += [(base, node.lineno)] if base else [(f"agent.{a.name}", node.lineno) for a in node.names]
            elif node.module == "agent":  # from agent import store
                found += [(f"agent.{a.name}", node.lineno) for a in node.names]
            elif node.module and node.module.startswith("agent."):
                found.append((node.module, node.lineno))
    # agent.models.X 처럼 더 깊은 경로는 부품 이름(앞 두 마디)으로 판정한다.
    return [(".".join(name.split(".")[:2]), line) for name, line in found]


@pytest.mark.parametrize("path", PURE_MODULES, ids=lambda p: p.name)
def 기능_결정론부품_다른_부품을_불러오지_않는다(path):
    """목적: 결정론 부품이 입출력을 품은 우리 부품(store 등)을 불러와 간접적으로 결정론을 깨지 않는지 확인한다."""
    tree = _parse(path)
    found = [f"{name} (줄 {line})" for name, line in _internal_imports(tree)
             if name not in ALLOWED_INTERNAL and name != f"agent.{path.stem}"]
    assert not found, f"{path.name}이 입출력 부품을 불러온다: {found}"


@pytest.mark.parametrize("path", PURE_MODULES, ids=lambda p: p.name)
def 기능_결정론부품_현재시각을_스스로_읽지_않는다(path):
    """목적: 결정론 부품이 now()·today()·time() 등으로 시각을 직접 읽지 않는지 확인한다.

    시각이 필요하면 부르는 쪽이 인자로 넘긴다 — 그래야 「자정 직전·직후」 같은 경계를 테스트할 수 있다.
    """
    tree = _parse(path)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # `datetime.now()` 꼴(속성 호출)과 `now()` 꼴(이름 호출)을 모두 잡는다.
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name in CLOCK_CALLS:
            found.append(f"{name}() (줄 {node.lineno})")
    assert not found, f"{path.name}이 현재 시각을 스스로 읽는다: {found}"
