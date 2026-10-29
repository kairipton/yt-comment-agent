"""draft 테스트 — Claude를 부르지 않는다. 정해진 답을 돌려주는 가짜 클라이언트로 흐름만 본다."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from agent.config import load_settings
from agent.draft import Drafter, DraftUnavailable, build_system, build_user, source_text
from agent.models import Source, VideoContext, VideoItem
from tests.conftest import CHANNEL_A, GOOD_COMMENT, SOURCE_TEXT

ROOT = Path(__file__).resolve().parents[1]
BAD_COMMENT = "#연애 너무 공감돼요 https://a.com"  # 링크·해시태그·운명원 없음 — 문지기에 떨어진다


def 응답(comment: str | None = None, stop_reason: str = "end_turn", raw: str | None = None):
    """Claude 응답 꼴의 가짜. raw를 주면 그 문자열을 그대로 본문으로 쓴다(형식 오류 흉내)."""
    text = raw if raw is not None else json.dumps({"worry": "답장이 늦어 서운하다", "comment": comment}, ensure_ascii=False)
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


class 가짜클라이언트:
    """create가 불릴 때마다 준비해 둔 응답(또는 예외)을 차례로 돌려주고, 받은 요청을 모아 둔다."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return load_settings(ROOT / "config.yaml", ROOT / "없는.env")


@pytest.fixture
def ctx() -> VideoContext:
    v = VideoItem(id="vid00000001", title="썸 타는 사람 답장이 하루씩 늦어요", channel_id=CHANNEL_A, channel_name="외롭여",
                  published_at=None, thumbnail_url="t.jpg", source=Source.CHANNEL, description="고민 상담")
    return VideoContext(video=v, transcript=SOURCE_TEXT, top_comments=("인기댓글표본",))


def 작가(settings, client) -> Drafter:
    return Drafter(settings, client=client)


def 기능_작가_통과한_초안을_돌려준다(settings, ctx):
    """목적: 문지기를 통과한 초안은 한 번에 끝나고 불합격 이유가 없다."""
    result = 작가(settings, 가짜클라이언트(응답(GOOD_COMMENT))).write(ctx, [], version=1)
    assert (result.verdict.ok, result.tries, result.draft.issues) == (True, 1, ())


def 기능_작가_읽은_자막글자수와_인기댓글수를_초안에_담는다(settings, ctx):
    """목적: 초안에 「AI가 읽은 양」(자막 글자 수 · 인기 댓글 수)을 담는다 — 화면 ②가 그대로 보여 준다."""
    keyed = replace(settings, youtube_api_key="yt-키")  # 키가 있어 인기 댓글을 읽은 경우
    draft = 작가(keyed, 가짜클라이언트(응답(GOOD_COMMENT))).write(ctx, [], version=1).draft
    assert (draft.transcript_chars, draft.comments_read) == (len(SOURCE_TEXT), 1)


def 경계_작가_자막이_없으면_자막글자수는_0이다(settings, ctx):
    """목적: 자막이 없으면 0 — 「기록 없음(None, 옛 초안)」과 갈라 「자막 없음」으로 보이게."""
    no_sub = replace(ctx, transcript=None)
    # 자막이 빠지면 좋은 댓글도 「재료와 겹침」에 떨어져 다시 쓴다 — 다시 쓰기 횟수만큼 답을 준비한다.
    answers = [응답(GOOD_COMMENT) for _ in range(1 + settings.comment.max_rewrites)]
    draft = 작가(settings, 가짜클라이언트(*answers)).write(no_sub, [], version=1).draft
    assert draft.transcript_chars == 0


def 경계_작가_YouTube키가_없으면_인기댓글수는_None이다(settings, ctx):
    """목적: YouTube 키가 없어 인기 댓글을 못 읽었으면 None — 「댓글 0개」와 「키 없음」을 가른다(D-31)."""
    no_key = replace(settings, youtube_api_key=None)
    draft = 작가(no_key, 가짜클라이언트(응답(GOOD_COMMENT))).write(replace(ctx, top_comments=()), [], version=1).draft
    assert draft.comments_read is None


def 기능_작가_고민을_먼저_정리하게_요청한다(settings, ctx):
    """목적: 응답 스키마의 필드 순서가 worry → comment라 고민을 먼저 정리하게 한다."""
    client = 가짜클라이언트(응답(GOOD_COMMENT))
    작가(settings, client).write(ctx, [], version=1)
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert list(schema["properties"]) == ["worry", "comment"]


def 기능_작가_불합격이면_이유를_붙여_다시_쓴다(settings, ctx):
    """목적: 문지기에 떨어지면 이전 초안과 떨어진 이유를 재료에 붙여 다시 부른다."""
    client = 가짜클라이언트(응답(BAD_COMMENT), 응답(GOOD_COMMENT))
    result = 작가(settings, client).write(ctx, [], version=1)
    second = client.calls[1]["messages"][0]["content"]
    assert result.verdict.ok and "링크가 들어 있어요." in second and BAD_COMMENT in second


def 경계_작가_다시쓰기는_최대횟수까지만_부른다(settings, ctx):
    """목적: 끝내 떨어지면 첫 작성 1번 + 다시 쓰기 max_rewrites번까지만 부르고, 마지막 초안을 이유와 함께 돌려준다."""
    n = settings.comment.max_rewrites
    client = 가짜클라이언트(*[응답(BAD_COMMENT)] * (n + 1))
    result = 작가(settings, client).write(ctx, [], version=1)
    assert (len(client.calls), result.verdict.ok, bool(result.draft.issues)) == (n + 1, False, True)


def 기능_작가_형식이_틀린_응답은_불합격으로_센다(settings, ctx):
    """목적: JSON이 아닌 응답은 버리지 않고 불합격 한 번으로 센 뒤 다시 부른다."""
    result = 작가(settings, 가짜클라이언트(응답(raw="그냥 문장"), 응답(GOOD_COMMENT))).write(ctx, [], version=1)
    assert (result.verdict.ok, result.tries) == (True, 2)


def 기능_작가_거절이면_초안없음으로_알린다(settings, ctx):
    """목적: 모델이 거절하면 빈 초안을 만들지 않고 이유와 함께 알린다(Haiku에는 대체 모델이 없다)."""
    with pytest.raises(DraftUnavailable):
        작가(settings, 가짜클라이언트(응답(stop_reason="refusal", raw=""))).write(ctx, [], version=1)


def 기능_작가_AI키가_없으면_초안없음으로_알린다(settings, ctx):
    """목적: AI 키가 없으면 저장된 초안을 쓰지 않고 「키 없음」으로 알린다(D-28 · 사용자 결정)."""
    drafter = Drafter(settings)
    with pytest.raises(DraftUnavailable, match="키가 설정되지 않았"):
        drafter.write(ctx, [], version=1)


def 기능_작가_API_연결오류는_이유를_담아_올린다(settings, ctx):
    """목적: API 오류를 삼키지 않고 무슨 일인지 담아 올린다."""
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    with pytest.raises(DraftUnavailable, match="연결하지 못했"):
        작가(settings, 가짜클라이언트(error)).write(ctx, [], version=1)


def 기능_작가_설정의_모델과_effort로_부른다(settings, ctx):
    """목적: 모델·생각 깊이는 코드가 아니라 config.yaml 값을 쓴다(D-20)."""
    client = 가짜클라이언트(응답(GOOD_COMMENT))
    작가(replace(settings, llm_model="모델X", llm_effort="low"), client).write(ctx, [], version=1)
    call = client.calls[0]
    assert (call["model"], call["output_config"]["effort"]) == ("모델X", "low")


def 기능_재료_인기댓글은_문지기_재료에서_빠진다(ctx):
    """목적: 「재료와 겹침」 검사 재료에는 인기 댓글을 넣지 않는다(「영상 속」 장면·말)."""
    assert "인기댓글표본" not in source_text(ctx)


def 기능_재료_인기댓글은_작가에게는_보인다(ctx):
    """목적: 작가는 인기 댓글을 읽는다(분위기 참고)."""
    assert "인기댓글표본" in build_user(ctx, [])


def 기능_재료_자막이_없으면_그렇다고_밝힌다(ctx):
    """목적: 자막이 없으면 제목·설명만 보고 쓰라고 알린다(대체 경로)."""
    assert "자막 없음" in build_user(replace(ctx, transcript=None), [])


def 기능_작가_지시문은_파일의_글에_설정값을_채운다(rules):
    """목적: 지시문은 파일(config llm.prompt_file)의 글 그대로이고, {brand} 같은 자리표시만 config 값으로 채워진다."""
    text = build_system("{brand}은 {brand_desc}. {min_count} {min_chars}~{max_chars}자. 금지: {forbidden_examples}", rules)
    assert text == "운명원은 연애 사주 잘 봐 주는 곳으로 유명한 곳. 공백을 뺀 40~120자. 금지: 「받아봤」, 「봤는데 맞」, 「다녀왔」, 「후기」"
