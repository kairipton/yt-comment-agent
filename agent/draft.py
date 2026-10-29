"""작가 — 초안 재료를 받아 Claude로 「영상 속 고민 한 줄」을 먼저 정리하고, 시청자처럼 댓글을 쓴다.
명세: docs/systems/draft.md

흐름: 지시문(system, 고정) + 재료(user) → Claude 한 번 호출 → JSON {worry, comment}
     → 문지기 check_comment 검사 → 떨어지면 「이전 초안 + 떨어진 이유」를 재료에 붙여 다시(최대 max_rewrites번)
     → 끝내 떨어지면 마지막 초안과 이유를 그대로 돌려준다(조용히 버리지 않는다. 불합격 초안은 게시할 수 없다).
JSON 필드 순서가 worry → comment라, 모델은 고민을 먼저 정리한 뒤 댓글을 쓴다.
다시 쓸 때는 대화를 이어 붙이지 않고 새로 한 번 부른다 — 요청이 단순해지고 모델의 생각 블록을 되돌려 보낼 일이 없다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from agent import guard
from agent.models import PROMPT_FIELDS, CommentRules, Draft, Settings, Verdict, VideoContext

log = logging.getLogger(__name__)

MAX_TOKENS = 16000  # 생각(thinking) 토큰까지 들어가는 한도. 댓글은 짧지만 생각에 쓸 자리를 남긴다

# 응답 모양. 필드 순서가 곧 쓰는 순서다 — 고민을 먼저 정리하고 댓글을 쓴다.
_SCHEMA = {
    "type": "object",
    "properties": {
        "worry": {"type": "string", "description": "영상이 다루는 연애 고민을 한 문장으로 정리"},
        "comment": {"type": "string", "description": "그 영상을 본 시청자로서 남길 유튜브 댓글"},
    },
    "required": ["worry", "comment"],
    "additionalProperties": False,
}


class DraftUnavailable(Exception):
    """초안을 만들 수 없다(AI 키 없음 · 모델 거절 · API 오류 · 형식에 맞는 응답 없음). 이유를 담는다. 키 값은 담지 않는다."""


@dataclass(frozen=True)
class DraftResult:
    draft: Draft      # 마지막으로 받은 초안(issues에 불합격 이유)
    verdict: Verdict  # 그 초안의 문지기 판정
    tries: int        # Claude를 부른 횟수(첫 작성 1 + 다시 쓰기)


def source_text(ctx: VideoContext) -> str:
    """문지기 「재료와 겹침」에 넘길 재료: 자막(있으면) + 제목 + 설명. 인기 댓글은 넣지 않는다(「영상 속」 장면·말)."""
    return "\n".join(part for part in (ctx.transcript or "", ctx.video.title, ctx.video.description) if part)


class Drafter:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        """AI 키가 있으면 Claude 클라이언트를 만든다. 테스트는 정해진 답을 돌려주는 가짜 client를 넣는다."""
        self.settings = settings
        self.rules: CommentRules = settings.comment
        if client is None and settings.anthropic_api_key:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.client = client

    @property
    def available(self) -> bool:
        """AI 키가 있어 초안을 쓸 수 있나."""
        return self.client is not None

    def write(self, ctx: VideoContext, others: list[str], version: int) -> DraftResult:
        """초안을 쓰고 문지기로 검사한다. 떨어지면 이유를 붙여 다시 쓴다(최대 max_rewrites번)."""
        if not self.available:
            raise DraftUnavailable("AI API 키가 설정되지 않았어요.")

        system = build_system(self.settings.comment_prompt, self.rules)
        material = source_text(ctx)
        last: tuple[str, str] | None = None
        verdict = Verdict.from_reasons(["아직 쓰지 않음"])
        tries = 0
        for _ in range(1 + self.rules.max_rewrites):
            tries += 1
            answer = self._ask(system, build_user(ctx, others, previous=last, reasons=verdict.reasons if last else ()))
            if answer is None:
                # 형식이 틀린 응답은 불합격 한 번으로 센다. 이전 초안이 있으면 그것을 계속 고치게 둔다.
                if last is None:
                    verdict = Verdict.from_reasons(["응답 형식이 올바르지 않음"])
                continue
            last = answer
            verdict = guard.check_comment(answer[1], self.rules, material, others)
            if verdict.ok:
                break

        if last is None:
            raise DraftUnavailable("형식에 맞는 초안을 받지 못했어요.")
        worry, comment = last
        # AI가 읽은 양 — 화면 ②의 「AI가 읽은 것」. 자막이 없으면 0, YouTube 키가 없어 인기 댓글을 못 읽었으면 None.
        draft = Draft(video_id=ctx.video.id, worry=worry, comment=comment, version=version, issues=verdict.reasons,
                      transcript_chars=len(ctx.transcript or ""),
                      comments_read=len(ctx.top_comments) if self.settings.youtube_api_key else None)
        return DraftResult(draft=draft, verdict=verdict, tries=tries)

    def _ask(self, system: str, user: str) -> tuple[str, str] | None:
        """Claude를 한 번 부른다. (고민, 댓글) 또는 형식이 틀리면 None. 거절·API 오류는 DraftUnavailable."""
        try:
            response = self.client.messages.create(
                model=self.settings.llm_model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.settings.llm_effort,
                               "format": {"type": "json_schema", "schema": _SCHEMA}},
            )
        except anthropic.AuthenticationError:
            raise DraftUnavailable("AI API 키가 올바르지 않아요(인증 실패).") from None
        except anthropic.PermissionDeniedError:
            raise DraftUnavailable("AI API 키에 이 모델을 쓸 권한이 없어요.") from None
        except anthropic.NotFoundError:
            raise DraftUnavailable(f"모델을 찾지 못했어요: {self.settings.llm_model}") from None
        except anthropic.RateLimitError:
            raise DraftUnavailable("AI 요청 한도에 걸렸어요. 잠시 뒤 다시 시도하세요.") from None
        except anthropic.APIStatusError as e:
            raise DraftUnavailable(f"AI 서버 오류(HTTP {e.status_code})") from None
        except anthropic.APIConnectionError:
            raise DraftUnavailable("AI 서버에 연결하지 못했어요.") from None

        # 응답을 읽기 전에 멈춘 이유부터 본다 — 거절이면 내용이 없다.
        if response.stop_reason == "refusal":
            raise DraftUnavailable("모델이 이 요청을 거절했어요(안전 정책).")
        if response.stop_reason == "max_tokens":
            log.warning("초안 응답이 길이 한도에서 잘림")
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        worry, comment = data.get("worry"), data.get("comment")
        if not isinstance(worry, str) or not isinstance(comment, str) or not comment.strip():
            return None
        return worry.strip(), comment.strip()


# ── 지시문과 재료 ─────────────────────────────────────────────────────────────
def prompt_values(rules: CommentRules) -> dict[str, str]:
    """지시문 자리표시에 넣을 값. 전부 config의 comment 절에서 온다."""
    count = {"no_spaces": "공백을 뺀", "with_spaces": "공백을 포함한"}
    values = {"brand": rules.brand, "brand_desc": rules.brand_desc, "min_chars": str(rules.min_chars),
            "max_chars": str(rules.max_chars), "min_count": count[rules.min_count],
            "max_count": count[rules.max_count],
            "forbidden_examples": ", ".join(f"「{p}」" for p in rules.forbidden_phrases[:6])}
    assert set(values) == set(PROMPT_FIELDS)  # 자리표시 목록과 채우는 값이 어긋나지 않게
    return values


def build_system(template: str, rules: CommentRules) -> str:
    """지시문 = 지시문 파일(config llm.prompt_file)의 글에 config 값을 채운 것. 재료와 섞지 않는다."""
    return template.format_map(prompt_values(rules))


def build_user(ctx: VideoContext, others: list[str], previous: tuple[str, str] | None = None,
               reasons: tuple[str, ...] = ()) -> str:
    """재료. 자르지 않는다. 다시 쓸 때는 이전 초안과 떨어진 이유를 붙인다."""
    video = ctx.video
    transcript = ctx.transcript or "(자막 없음 — 제목과 설명만 보고 쓰세요)"
    comments = "\n".join(f"- {c}" for c in ctx.top_comments) or "(없음)"
    written = "\n".join(f"- {o}" for o in others) or "(없음)"
    parts = [
        f"<영상>\n제목: {video.title}\n채널: {video.channel_name}\n설명: {video.description or '(없음)'}\n</영상>",
        f"<자막>\n{transcript}\n</자막>",
        f"<인기_댓글 설명=\"분위기 참고용\">\n{comments}\n</인기_댓글>",
        f"<다른_영상에_쓴_댓글>\n{written}\n</다른_영상에_쓴_댓글>",
    ]
    if previous is not None:
        bullet = "\n".join(f"- {r}" for r in reasons)
        parts.append(f"<이전_초안>\n{previous[1]}\n</이전_초안>\n"
                     f"<통과하지_못한_이유>\n{bullet}\n</통과하지_못한_이유>\n위 이유를 고쳐 다시 쓰세요.")
    return "\n\n".join(parts)
