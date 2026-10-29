"""launcher 테스트 — 원클릭 실행기의 .env 만들기만 본다. 서버 켜기 · bat은 수동 확인. 크롬 찾기는 test_chrome."""

from agent.launcher import ensure_env


def 기능_실행기_env가_있으면_건드리지_않는다(tmp_path):
    """목적: .env가 없을 때만 예시 파일로 만들고, 키가 든 기존 .env는 덮어쓰지 않는다."""
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
    first = ensure_env(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=넣은값\n", encoding="utf-8")
    second = ensure_env(tmp_path)
    assert (first, second) == (True, False)
    assert "넣은값" in (tmp_path / ".env").read_text(encoding="utf-8")
