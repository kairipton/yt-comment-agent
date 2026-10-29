"""models 테스트 — 「게시 버튼을 눌렀나」 분류와 비밀 값 가림."""

from pathlib import Path

from agent.config import load_settings
from agent.models import PostStatus

ROOT = Path(__file__).resolve().parents[1]


def 기능_게시상태_버튼을_누른_상태만_참이다():
    """목적: 하루 상한·영상당 1개가 세는 기준(pressed_submit)이 명세의 네 상태와 정확히 같은지 확인한다."""
    pressed = {s for s in PostStatus if s.pressed_submit}
    assert pressed == {PostStatus.SUBMITTED, PostStatus.CONFIRMING, PostStatus.CONFIRMED,
                       PostStatus.FAILED_AFTER_SUBMIT}


def 기능_설정_출력에_API키가_보이지_않는다(monkeypatch):
    """목적: Settings를 출력(repr)해도 비밀 값이 찍히지 않는지 확인한다."""
    # 환경 변수로 가짜 키를 넣고 저장소의 config.yaml을 읽는다.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-비밀값")
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-비밀값")
    settings = load_settings(ROOT / "config.yaml", ROOT / "없는.env")
    assert settings.anthropic_api_key == "sk-ant-비밀값"
    assert "비밀값" not in repr(settings)


def 기능_게시상태_끝난_상태는_넷이다():
    """목적: 「끝난 상태」(상태를 다시 바꾸지 않는 상태)가 완료·실패 둘·거부뿐인지 — 진행 중 상태가 섞이면 멈춘 줄을 못 정리한다."""
    finished = {s for s in PostStatus if s.finished}
    assert finished == {PostStatus.CONFIRMED, PostStatus.FAILED_BEFORE_SUBMIT, PostStatus.FAILED_AFTER_SUBMIT,
                        PostStatus.REJECTED}


def 기능_상태_갈래는_완료_손봐야_막힘_진행이다():
    """목적: 화면 ③·기록 표가 색을 고르는 갈래(tone)가 명세 표와 같은지 — 실패 둘은 「손봐야」, 거부만 「막힘」이다."""
    tones = {s: s.tone for s in PostStatus}
    assert tones == {
        PostStatus.QUEUED: "progress", PostStatus.OPENING: "progress", PostStatus.TYPING: "progress",
        PostStatus.SUBMITTED: "progress", PostStatus.CONFIRMING: "progress", PostStatus.CONFIRMED: "done",
        PostStatus.FAILED_BEFORE_SUBMIT: "fix", PostStatus.FAILED_AFTER_SUBMIT: "fix", PostStatus.REJECTED: "blocked",
    }
