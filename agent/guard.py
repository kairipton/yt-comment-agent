"""문지기 — 게시해도 되는지, 댓글이 규칙을 지키는지 판정만 한다. 명세: docs/systems/guard.md

결정론 부품이다(D-19): 같은 입력이면 언제나 같은 판정. 그래서
  · 입출력(DB·네트워크·파일)을 하지 않고, 현재 시각을 스스로 읽지 않고, 난수를 쓰지 않는다.
  · 필요한 값(오늘 게시 수 · 현재 시각 · 설정)은 전부 부르는 쪽이 인자로 넘긴다.
  · tests/test_architecture.py가 이 규칙을 import·호출 모양으로 검사한다.
불합격이면 Verdict.reasons에 이유를 「전부」 담는다 — 작가가 그 이유를 붙여 다시 쓰고 화면이 그대로 보여 준다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from agent.models import CommentRules, Draft, PostStatus, Source, Verdict, VideoItem

# 한국 시간. 서머타임이 없어 고정 +9로 충분하고, 시간대 데이터베이스가 없는 PC에서도 돈다.
KST = timezone(timedelta(hours=9), "KST")

# 링크 꼴: 주소 머리 · youtu.be · 「낱말.도메인」 꼴. 한글 문장의 마침표(「1인. 결국」)는 걸리지 않는다.
_LINK = re.compile(r"https?://|www\.|youtu\.be|[\w-]+\.(?:com|net|kr|co|io|me|ly)\b", re.IGNORECASE)
# 해시태그: 반각 # 또는 전각 ＃ 바로 뒤에 공백이 아닌 글자.
_HASHTAG = re.compile(r"[#＃]\S")

# 지어낸 후기(금지 조항) ② — 브랜드 바로 곁의 「직접 겪은」 표현. 명세 guard.md 「지어낸 후기」.
# 표현 목록(forbidden_phrases)은 정확히 같은 말만 잡아 「운명원 가봤는데 딱 맞더라고요」가 통과했다. 그래서 브랜드가 나온
# 자리마다 뒤 _AFTER글자 · 앞 _BEFORE글자만 본다 — 브랜드와 먼 곳의 내 경험(「소개팅에서 상담 받았는데」)은 건드리지 않는다.
_EXPERIENCE_AFTER = re.compile(
    r"가\s?봤|갔었|갔다\s?왔|갔더니|갔는데|갔을|갔던|가서|다녀|들렀|방문|예약했|예약해서|예약하고|상담\s?받|상담했"
    r"|봐\s?줬|봐\s?주셨|잡아\s?준|해\s?준(?!다)|해\s?줘서|해\s?줬|해\s?주신|해\s?주셨|말씀하신|말씀하셨|하셨는데"
    # 「검색해 봤는데」 · 「찾아 봤는데」 · 「추천 받았」은 운명원을 이용한 것이 아니라 뺀다.
    r"|(?<!검색해)(?<!검색해 )(?<!찾아)(?<!찾아 )(?:봤는데|봤더니)|봤을\s?때|봤던"
    r"|(?<!추천)(?<!추천 )받았|(?<!추천)(?<!추천 )받고|받아\s?보니|(?<!검색)(?<!검색 )해\s?봤"
    r"|맞더라|맞았|맞혔|맞췄|소름|단골|덕분에|에서\s?들은|에서\s?들었")
_EXPERIENCE_BEFORE = re.compile(r"다녀온|갔던|가\s?본|단골|예약한|상담\s?받은")
# 남이 겪은 일을 전해 들은 말(_heard_from_others) — 앞에 남을 가리키는 주어가 있고 뒤가 전해 들은 끝맺음이거나,
# 경험 표현이 남을 꾸미는 꼴(「운명원 다녀온 지인이」)일 때. 「사람들 후기가」처럼 조사 없이 오는 주어도 본다.
_PEOPLE = r"(?:친구|언니|오빠|누나|형|동생|지인|동료|후배|선배|엄마|이모|사촌|사람들|다들|사람)"
_THIRD_PARTY = re.compile(_PEOPLE + r"(?:이|가|는|도|\s)")
_HEARSAY_END = re.compile(r"대요|래요|다던|라던|다는데|라는데|다더라|라더라|다고\s?하더|다고\s?했|다고\s?들었|댔어|랬어")
_SOMEONE_ELSES = re.compile(r"(?:다녀온|갔던|가\s?본|갔다\s?온|받은|본)\s?" + _PEOPLE)
# 볼 범위(글자 수): 경험 표현은 브랜드 뒤 _AFTER · 앞 _BEFORE, 남을 가리키는 주어는 앞 _SUBJECT_BEFORE(앞 문장으로는 안 넘어감)
_AFTER, _BEFORE, _SUBJECT_BEFORE = 30, 12, 25
_COUNT_MODES = ("no_spaces", "with_spaces")
# 영상 id: 영문·숫자·-·_ 11자. 링크에서는 youtu.be/<id> · watch?v=<id> · /shorts|live|embed/<id> 자리에 온다.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LINK_ID = re.compile(
    r"^(?:https?://)?(?:(?:www|m|music)\.)?"
    r"(?:youtu\.be/(?P<short>[A-Za-z0-9_-]{11})"
    r"|youtube\.com/(?:watch\?(?:[^#]*&)?v=(?P<watch>[A-Za-z0-9_-]{11})|(?:shorts|live|embed)/(?P<path>[A-Za-z0-9_-]{11})))"
    r"(?:[?&#/].*)?$",
    re.IGNORECASE,
)


def _heard_from_others(text: str, start: int, end: int) -> bool:
    """text[start:end](브랜드나 금지 표현)가 남의 경험을 전해 들은 말 속에 있나.

    「친구가 운명원 다녀왔는데 잘 본대요」 — 앞에 남을 가리키는 주어 + 뒤에 전해 들은 끝맺음.
    「운명원 다녀온 지인이 …」 — 바로 뒤가 남을 꾸미는 꼴.
    브랜드 곁 검사(_experience_near_brand)와 설정의 표현 목록 검사(check_text)가 같이 쓴다 — 판단 기준은 이 한 곳이다.
    """
    before = re.split(r"[.?!\n]", text[max(0, start - _SUBJECT_BEFORE):start])[-1]  # 앞 문장으로는 넘어가지 않는다
    after = text[end:end + _AFTER]
    if _THIRD_PARTY.search(before) and _HEARSAY_END.search(after):
        return True
    return bool(_SOMEONE_ELSES.match(after.lstrip()))


def _experience_near_brand(text: str, brand: str) -> list[str]:
    """브랜드가 나온 자리마다 그 곁에서 직접 겪은 표현을 찾아, 걸린 부분(「운명원 가봤는데」 같은)을 돌려준다."""
    hits = []
    for m in re.finditer(re.escape(brand), text):
        if _heard_from_others(text, m.start(), m.end()):
            continue
        before = text[max(0, m.start() - _BEFORE):m.start()]
        after = text[m.end():m.end() + _AFTER]
        found = _EXPERIENCE_AFTER.search(after)
        if found:
            hits.append(f"{brand}{after[:found.end()]}")
            continue
        found = _EXPERIENCE_BEFORE.search(before)
        if found:
            hits.append(f"{before[found.start():]}{brand}")
    return hits


def _listed_phrases(text: str, phrases: tuple[str, ...]) -> list[str]:
    """설정의 금지 표현 중 댓글에 든 것. 남의 경험을 전해 들은 말 속에만 있으면 빼 준다(모든 자리가 그럴 때)."""
    return [p for p in phrases
            if any(not _heard_from_others(text, m.start(), m.end()) for m in re.finditer(re.escape(p), text))]


# ── 링크 ──────────────────────────────────────────────────────────────────────
def parse_video_id(text: str) -> str | None:
    """붙여 넣은 유튜브 영상 링크(또는 id 11자)에서 영상 id를 뽑는다. 영상 링크가 아니면 None.

    허용 범위는 여기서 보지 않는다 — 영상을 가져온 뒤 check_target이 채널로 본다.
    """
    text = text.strip()
    if _VIDEO_ID.match(text):
        return text
    m = _LINK_ID.match(text)
    if m is None:
        return None
    return m.group("short") or m.group("watch") or m.group("path")


# ── 시각 ──────────────────────────────────────────────────────────────────────
def kst_day_range(now: datetime) -> tuple[datetime, datetime]:
    """now가 속한 한국 시간 하루 [00:00, 다음날 00:00). UTC로 받아도 한국 날짜로 자른다."""
    if now.tzinfo is None:
        raise ValueError("시간대가 없는 시각은 받지 않아요(예: datetime.now(UTC)).")
    start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def quota_window(now: datetime, reset_at: datetime | None) -> tuple[datetime, datetime]:
    """하루 상한을 세는 구간 — 서버 · 게시 로봇 · 자동 모드가 모두 이것으로 센다.

    보통은 한국 시간 오늘 [00:00, 다음날 00:00). 오늘 안에 초기화(개발 모드의 [초기화])가 있었으면 그 시각부터 센다.
    어제 이전의 초기화는 무시한다 — 자정이 지나면 늘 0부터다.
    """
    start, end = kst_day_range(now)
    if reset_at is not None and reset_at > start:
        start = reset_at
    return start, end


# ── 게시해도 되나 ──────────────────────────────────────────────────────────────
def check_target(video: VideoItem, allowed_channels: set[str], test_ids: set[str],
                 channel_posting: bool) -> Verdict:
    """허용 범위(R16·D-22·D-34). 순서: 뜨기 직전 거부 → 테스트 영상 통과 → 지정 채널(스위치가 켜졌을 때만) → 나머지 거부.

    channel_posting이 꺼져 있으면 지정 채널 영상도 막혀 테스트 영상만 게시된다 — dev_mode와 따로 돈다.
    """
    if video.source is Source.RISING:
        return Verdict.from_reasons(["뜨기 직전 영상은 초안까지만 만들 수 있어요(게시 불가)."])
    if video.id in test_ids:
        return Verdict.passed()
    if video.channel_id in allowed_channels:
        if channel_posting:
            return Verdict.passed()
        return Verdict.from_reasons(["지정 채널 게시가 꺼져 있어요 — 지금은 테스트 영상에만 달아요."])
    return Verdict.from_reasons(["허용된 채널의 영상이 아니에요."])


def is_exempt(video: VideoItem, test_ids: set[str], dev_mode: bool) -> bool:
    """영상당 1개의 예외인가: dev_mode가 켜져 있고 테스트 영상일 때만(D-17·D-23)."""
    return dev_mode and video.id in test_ids


def check_exempt(claimed: bool, allowed: bool) -> Verdict:
    """시도 줄에 적힌 예외 표시(claimed)가 지금도 허락되는가(allowed = is_exempt의 지금 값).

    dev_mode를 켜고 만든 시도를 dev_mode를 끈 뒤 띄우면 예외가 아니어야 한다 — 실제로 쓰는 중에 테스트 영상에 둘 달리지 않게.
    예외가 아닌 줄을 예외로 다루는 쪽(반대)은 더 엄격할 뿐이라 막지 않는다.
    """
    if claimed and not allowed:
        return Verdict.from_reasons(["개발 모드가 꺼져 「영상당 1개」 예외를 쓸 수 없어요. 다시 눌러 주세요."])
    return Verdict.passed()


def check_quota(already_posted: bool, exempt: bool, submitted_today: int, daily_max: int) -> Verdict:
    """하루 상한(예외 영상에도 적용)과 영상당 1개를 둘 다 보고 이유를 모은다."""
    reasons = []
    if submitted_today >= daily_max:
        reasons.append(f"오늘 게시 상한 {daily_max}개에 도달했어요.")
    if already_posted and not exempt:
        reasons.append("이미 댓글을 단 영상이에요(영상당 1개).")
    return Verdict.from_reasons(reasons)


def check_draft(draft: Draft | None, text: str) -> Verdict:
    """게시할 글이 저장된 초안 그대로인가 — 모드와 무관하게 본다(poster 명세).

    초안이 없거나 · 문지기 불합격 이유가 있거나 · 글이 초안과 다르면 거부. 앞뒤 공백 차이는 무시한다.
    불합격 초안이나 초안과 다른 글이 [댓글 달기]·자동 모드·로봇 직접 실행 어디로도 새지 않게 한다.
    """
    if draft is None:
        return Verdict.from_reasons(["이 영상의 초안이 없어요."])
    reasons = []
    if draft.issues:
        reasons.append("규칙을 통과하지 못한 초안이에요.")
    if draft.comment.strip() != text.strip():
        reasons.append("게시할 글이 저장된 초안과 달라요.")
    return Verdict.from_reasons(reasons)


def can_retry(status: PostStatus, retry_no: int, max_retries: int, *, cancelled: bool = False) -> bool:
    """자동 재시도 가능? 게시 버튼 전 실패이고 횟수가 남았고 취소가 아닐 때만(D-12).

    retry_no는 ③을 한 번 누를 때마다 0부터 다시 센다(첫 시도 0, 재시도 1·2 → 최대 3번 시도).
    게시 버튼 뒤 실패는 재시도하지 않는다 — 실제로 달렸을 수 있어 같은 영상에 둘 달릴 수 있다.
    cancelled: 사용자가 크롬 창을 닫았다. 취소로 보고 창을 다시 띄우지 않는다.
    """
    return status is PostStatus.FAILED_BEFORE_SUBMIT and retry_no < max_retries and not cancelled


# ── 댓글 규칙 ──────────────────────────────────────────────────────────────────
def char_count(text: str, mode: str) -> int:
    """no_spaces: 공백 문자(스페이스·줄바꿈·탭)를 뺀 글자 수 / with_spaces: 전체 글자 수(D-21)."""
    if mode == "no_spaces":
        return len(re.sub(r"\s", "", text))
    if mode == "with_spaces":
        return len(text)
    raise ValueError(f"글자 수 세는 방식은 {'·'.join(_COUNT_MODES)} 중 하나여야 해요: {mode!r}")


def check_comment(text: str, rules: CommentRules, source_text: str, others: list[str]) -> Verdict:
    """댓글 규칙을 모두 검사하고 걸린 것을 전부 이유로 담는다 = check_text + 「재료와 겹침」.

    source_text: 자막·제목·설명(인기 댓글은 넣지 않는다 — 「영상 속」 장면·말을 보려는 검사다).
    ⚠ 「재료와 겹침」은 장면 언급을 보증하지 않는다 — 재료와 동떨어진 초안을 거르는 최소선이다.
    """
    reasons = list(check_text(text, rules, others).reasons)
    overlap = len(_own_trigrams(text, rules) & _trigrams(source_text))
    if overlap < rules.grounding_min_overlap:
        reasons.append(f"영상 내용과 겹치는 말이 부족해요(겹친 조각 {overlap}개, {rules.grounding_min_overlap}개 이상).")
    return Verdict.from_reasons(reasons)


def check_text(text: str, rules: CommentRules, others: list[str]) -> Verdict:
    """재료(자막) 없이 볼 수 있는 댓글 규칙 전부: 길이 · 링크 · 해시태그 · 브랜드 · 지어낸 후기 · 다른 글과 유사도.

    게시 로봇은 재료를 다시 받지 않으므로 이것으로 다시 본다(poster 명세).
    others: 다른 영상의 게시 댓글·초안(store.comparable_texts).
    ⚠ 「지어낸 후기」는 forbidden_phrases 목록 + 브랜드 곁의 경험 표현(_experience_near_brand)만 잡는다. 그 밖은 작가의 지시문 몫이다.
    """
    reasons = []

    low = char_count(text, rules.min_count)
    high = char_count(text, rules.max_count)
    if low < rules.min_chars:
        reasons.append(f"너무 짧아요({_mode_name(rules.min_count)} {low}자, {rules.min_chars}자 이상).")
    if high > rules.max_chars:
        reasons.append(f"너무 길어요({_mode_name(rules.max_count)} {high}자, {rules.max_chars}자 이하).")

    if _LINK.search(text):
        reasons.append("링크가 들어 있어요.")
    if _HASHTAG.search(text):
        reasons.append("해시태그가 들어 있어요.")
    if rules.brand not in text:
        reasons.append(f"「{rules.brand}」 언급이 없어요.")
    # 지어낸 후기: ① 설정의 표현 목록 ② 브랜드 바로 곁의 직접 겪은 표현(코드 규칙). 둘 다 남의 경험을 전해 들은 말이면 뺀다.
    found = _listed_phrases(text, rules.forbidden_phrases) + [f"「{h}」" for h in _experience_near_brand(text, rules.brand)]
    if found:
        reasons.append(f"직접 이용해 본 것 같은 표현이 있어요: {', '.join(found)}")

    mine = _own_trigrams(text, rules)
    worst = max((_jaccard(mine, _own_trigrams(o, rules)) for o in others), default=0.0)
    if worst > rules.max_similarity:
        reasons.append(f"다른 영상 댓글과 너무 비슷해요(유사도 {worst:.2f}, {rules.max_similarity:.2f} 이하).")

    return Verdict.from_reasons(reasons)


def _own_trigrams(text: str, rules: CommentRules) -> set[str]:
    """브랜드·브랜드 소개에서 나오는 조각을 뺀 세 글자 조각.

    그 조각은 모든 댓글에 공통이라 겹침·유사도를 부풀린다 — 빼고 센다.
    """
    brand_grams = _trigrams(f"{rules.brand} {rules.brand_desc}") | _trigrams(
        (rules.brand + rules.brand_desc).replace(" ", ""))
    return _trigrams(text) - brand_grams


def _mode_name(mode: str) -> str:
    return "공백 제외" if mode == "no_spaces" else "공백 포함"


def _trigrams(text: str) -> set[str]:
    """세 글자 조각: 띄어쓰기로 나눈 낱말마다 한글만 남겨 붙은 세 글자씩 자른다(「바빠서라니」→ 바빠서·빠서라·서라니).

    두 글자 조각은 「연애」「진짜」처럼 이 채널들의 재료에 늘 있는 말로 쉽게 채워져 쓰지 않는다.
    """
    grams = set()
    for word in text.split():
        hangul = "".join(ch for ch in word if "가" <= ch <= "힣")
        grams.update(hangul[i:i + 3] for i in range(len(hangul) - 2))
    return grams


def _jaccard(a: set[str], b: set[str]) -> float:
    """겹친 조각 수 ÷ 합친 조각 수. 둘 다 비면 0."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0
