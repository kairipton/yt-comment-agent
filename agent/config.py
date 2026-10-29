"""설정 읽기 — config.yaml(튜닝 값·토글)과 .env(비밀 값)를 읽어 검증한 Settings를 돌려준다. 명세: docs/systems/config.md

흐름: YAML을 읽는다 → _Reader가 키를 하나씩 꺼내며 빠진 키·잘못된 값을 「이유 목록」에 모은다
     → 규칙 사이의 관계(하한 < 상한 등)를 검사한다 → 이유가 하나라도 있으면 ConfigError로 한 번에 알린다.
코드에는 기본값을 두지 않는다 — 값의 주인은 config.yaml 하나다(D-20).
"""

from __future__ import annotations

import os
import re
import string
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from agent.models import PROMPT_FIELDS, Channel, CommentRules, Profile, RisingRules, Settings

COUNT_MODES = ("no_spaces", "with_spaces")
POST_MODES = ("manual", "auto")
ASSIGN_MODES = ("manual", "rotate")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")
SECRET_KEYS = ("YOUTUBE_API_KEY", "ANTHROPIC_API_KEY")


class ConfigError(Exception):
    """잘못된 설정. 이유를 전부 담는다(오류 문구에는 비밀 값이 들어가지 않는다).

    이유 문구의 꼴: 설정 하나에 걸린 이유는 「경로: 설명」(예: "comment.min_chars: comment.max_chars보다 작아야 해요").
    옵션 페이지(web/options.js)가 앞의 경로로 칸을 찾아 칸 이름으로, 설명 안의 경로는 「칸 이름」으로 바꿔 보여 준다.
    그래서 설명 안의 경로 뒤에는 받침에 따라 바뀌는 조사(은/는 · 이/가 …)를 붙이지 않는다.
    """

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("설정 오류:\n- " + "\n- ".join(self.reasons))


class _Reader:
    """YAML 사전에서 「a.b.c」 경로로 값을 꺼내며 타입을 확인한다. 문제는 예외 대신 reasons에 쌓는다."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.reasons: list[str] = []

    def get(self, path: str, kind: type | tuple[type, ...]) -> Any:
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                self.reasons.append(f"{path}: 값이 없어요(빠진 키)")
                return None
            node = node[part]
        # 옵션 페이지에서 숫자 칸을 비우고 저장하면 None(YAML null)이 온다.
        if node is None:
            self.reasons.append(f"{path}: 비어 있어요")
            return None
        # bool은 int의 하위 타입이라, int를 기대한 자리에 true/false가 들어오는 것을 따로 막는다.
        if not isinstance(node, kind) or (kind is int and isinstance(node, bool)):
            self.reasons.append(f"{path}: 값이 올바르지 않아요({node!r})")
            return None
        return node


def load_settings(config_path: Path = Path("config.yaml"), env_path: Path = Path(".env")) -> Settings:
    """두 파일을 읽어 Settings를 만든다. 문제가 있으면 이유를 전부 모아 ConfigError."""
    if not config_path.exists():
        raise ConfigError([f"설정 파일이 없음: {config_path}"])
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError([f"설정 파일의 최상위가 사전 꼴이 아님: {config_path}"])

    r = _Reader(raw)
    base = config_path.parent

    post_mode = r.get("post_mode", str)
    dev_mode = r.get("dev_mode", bool)
    channel_posting = r.get("channel_posting", bool)
    profile_assign = r.get("profile_assign", str)
    profiles = _profiles(r, base)
    channels = _channels(r)
    test_ids = r.get("test_video_ids", list)
    per_channel = r.get("fetch.per_channel", int)
    comment = _comment_rules(r)
    daily_max = r.get("limits.daily_max_comments", int)
    max_retries = r.get("limits.max_retries_per_post", int)
    auto_gap = r.get("auto.gap_seconds", int)
    rising = _rising_rules(r)
    llm_model = r.get("llm.model", str)
    llm_effort = r.get("llm.effort", str)
    prompt_file = r.get("llm.prompt_file", str)
    draft_on_select = r.get("llm.draft_on_select", bool)
    comment_prompt = _prompt(r, base, prompt_file)
    fetch_timeout = r.get("fetch.timeout_seconds", (int, float))
    db_path = r.get("storage.db_path", str)
    shot_dir = r.get("storage.screenshot_dir", str)

    # 값 하나하나가 아니라 값의 범위·관계를 본다. 앞에서 빠진 값(None)은 이미 이유가 있으니 건너뛴다.
    if profile_assign is not None and profile_assign not in ASSIGN_MODES:
        r.reasons.append(f"profile_assign: {'·'.join(ASSIGN_MODES)} 중 하나여야 해요({profile_assign!r})")
    if post_mode is not None and post_mode not in POST_MODES:
        r.reasons.append(f"post_mode: {'·'.join(POST_MODES)} 중 하나여야 해요({post_mode!r})")
    if llm_effort is not None and llm_effort not in EFFORTS:
        r.reasons.append(f"llm.effort: {'·'.join(EFFORTS)} 중 하나여야 해요({llm_effort!r})")
    if fetch_timeout is not None and fetch_timeout <= 0:
        r.reasons.append("fetch.timeout_seconds: 0보다 커야 해요")
    if per_channel is not None and per_channel < 1:
        r.reasons.append("fetch.per_channel: 1 이상이어야 해요")
    if daily_max is not None and daily_max < 1:
        r.reasons.append("limits.daily_max_comments: 1 이상이어야 해요")
    if max_retries is not None and max_retries < 0:
        r.reasons.append("limits.max_retries_per_post: 0 이상이어야 해요")

    if r.reasons:
        raise ConfigError(r.reasons)

    secrets = _secrets(env_path)
    return Settings(
        post_mode=post_mode, dev_mode=dev_mode, channel_posting=channel_posting, profile_assign=profile_assign, profiles=profiles, channels=channels,
        test_video_ids=frozenset(str(v) for v in test_ids), per_channel=per_channel, comment=comment,
        daily_max=daily_max, max_retries=max_retries, auto_gap_seconds=auto_gap, rising=rising,
        llm_model=llm_model, llm_effort=llm_effort, comment_prompt=comment_prompt, draft_on_select=draft_on_select, fetch_timeout=float(fetch_timeout), db_path=base / db_path, screenshot_dir=base / shot_dir,
        youtube_api_key=secrets["YOUTUBE_API_KEY"], anthropic_api_key=secrets["ANTHROPIC_API_KEY"],
    )


# ── 옵션 페이지가 쓰는 읽기 · 쓰기 ──────────────────────────────────────────────
# config.yaml에는 주석을 두지 않는다 — 옵션 설명은 옵션 페이지(web/options.js)가 진다. 그래서 일반 YAML로 다시 쓴다.
def read_options(config_path: Path) -> dict:
    """config.yaml 값을 사전 그대로 돌려준다(옵션 페이지가 칸을 채운다)."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError([f"설정 파일의 최상위가 사전 꼴이 아님: {config_path}"])
    return raw


# 옵션 페이지에서 바꾸지 못하는 값(경로) — 저장할 때 지금 파일의 값을 그대로 둔다. 바꾸려면 config.yaml을 직접 고친다.
# 개발 모드(dev_mode)는 옵션 페이지에 칸도 없다 — 설정 파일로만 켠다.
LOCKED_OPTIONS = (("llm", "model"), ("llm", "effort"), ("dev_mode",))


def save_options(config_path: Path, values: dict) -> None:
    """옵션 값을 저장한다. 같은 폴더의 임시 파일에 먼저 써서 load_settings로 검사하고, 통과해야 원래 파일과 바꿔 끼운다.

    LOCKED_OPTIONS(AI 모델 · 생각 깊이)는 보내온 값을 무시하고 지금 파일의 값을 둔다.

    검사에 걸리면 임시 파일만 지우고 이유 목록으로 ConfigError — 원래 config.yaml은 한 글자도 바뀌지 않는다.
    임시 파일을 같은 폴더에 두는 이유: 지침 파일 같은 상대 경로가 같은 기준으로 풀리게.
    """
    if not isinstance(values, dict):
        raise ConfigError(["옵션 값이 사전 꼴이 아님"])
    current = read_options(config_path)
    for path in LOCKED_OPTIONS:  # 화면이 보낸 값과 상관없이 지금 값 그대로
        src, dst = current, values
        for key in path[:-1]:
            src, dst = src.get(key, {}), dst.setdefault(key, {})
        if path[-1] in src:
            dst[path[-1]] = src[path[-1]]
    text = yaml.safe_dump(values, allow_unicode=True, sort_keys=False, default_flow_style=False)
    temp = config_path.with_name(config_path.name + ".saving")
    temp.write_text(text, encoding="utf-8")
    try:
        load_settings(temp, Path(os.devnull))  # 검사만 — 비밀 값은 필요 없다
    except ConfigError:
        temp.unlink(missing_ok=True)
        raise
    temp.replace(config_path)


def _prompt_path(config_path: Path) -> Path:
    prompt_file = read_options(config_path).get("llm", {}).get("prompt_file")
    if not isinstance(prompt_file, str) or not prompt_file:
        raise ConfigError(["llm.prompt_file: 값이 없어요"])
    return config_path.parent / prompt_file


def read_prompt(config_path: Path) -> str:
    """댓글 작성 지침(llm.prompt_file)의 글."""
    return _prompt_path(config_path).read_text(encoding="utf-8")


def save_prompt(config_path: Path, text: str) -> None:
    """댓글 작성 지침을 저장한다. 빈 글 · 중괄호 · 모르는 자리표시가 있으면 쓰지 않고 ConfigError."""
    problems = prompt_problems(text)
    if problems:
        raise ConfigError(problems)
    _prompt_path(config_path).write_text(text, encoding="utf-8")


def prompt_problems(text: str) -> list[str]:
    """지침 글의 문제 목록 — 읽을 때(_prompt)와 저장할 때(save_prompt)가 같은 검사를 쓴다."""
    if not text.strip():
        return ["댓글 작성 지침이 비어 있어요"]
    try:
        names = {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
    except ValueError:  # 짝이 안 맞는 중괄호(파이썬의 영어 오류 문구는 화면에 내지 않는다)
        return ["지침의 중괄호 짝이 맞지 않아요 — 중괄호를 글자 그대로 쓰려면 {{ · }}처럼 두 번 쓰세요"]
    unknown = sorted(names - set(PROMPT_FIELDS))
    if unknown:
        return [f"지침에 모르는 자리표시: {', '.join(unknown)} — 쓸 수 있는 것: {', '.join(PROMPT_FIELDS)}"]
    return []


def _prompt(r: _Reader, base: Path, prompt_file: str | None) -> str | None:
    """댓글 지시문 파일을 읽는다. 없거나 비었거나 모르는 자리표시({이름})가 있으면 이유를 남긴다."""
    if prompt_file is None:
        return None
    path = base / prompt_file
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        r.reasons.append(f"llm.prompt_file: 파일을 읽지 못했어요({path})")
        return None
    problems = prompt_problems(text)
    if problems:
        r.reasons += [f"llm.prompt_file: {p}({path.name})" for p in problems]
        return None
    return text


def _profiles(r: _Reader, base: Path) -> tuple[Profile, ...]:
    items = r.get("profiles", list) or []
    result = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("name") or not item.get("user_data_dir"):
            r.reasons.append(f"profiles[{i}]: 비어 있는 칸이 있어요(name · user_data_dir)")
            continue
        # 로그인 여부는 설정에 두지 않는다 — chrome.login_state가 그때그때 확인한다(옛 logged_in 키는 읽지 않음).
        result.append(Profile(name=str(item["name"]), user_data_dir=base / str(item["user_data_dir"])))
    if not result and "profiles" in r.raw:
        r.reasons.append("profiles: 하나 이상 있어야 해요")
    names = [p.name for p in result]
    if len(names) != len(set(names)):
        r.reasons.append("profiles: 같은 이름이 여러 번 있어요")
    return tuple(result)


def _channels(r: _Reader) -> tuple[Channel, ...]:
    items = r.get("channels", list) or []
    result = []
    for i, item in enumerate(items):
        cid = str(item.get("id", "")) if isinstance(item, dict) else ""
        if not CHANNEL_ID.match(cid):
            r.reasons.append(f"channels[{i}]: 채널 ID는 UC로 시작하는 24글자여야 해요({cid!r})")
            continue
        result.append(Channel(id=cid, name=str(item.get("name", cid))))
    if not result and "channels" in r.raw:
        r.reasons.append("channels: 하나 이상 있어야 해요")
    return tuple(result)


def _comment_rules(r: _Reader) -> CommentRules | None:
    brand = r.get("comment.brand", str)
    brand_desc = r.get("comment.brand_desc", str)
    min_chars = r.get("comment.min_chars", int)
    max_chars = r.get("comment.max_chars", int)
    min_count = r.get("comment.min_count", str)
    max_count = r.get("comment.max_count", str)
    max_similarity = r.get("comment.max_similarity", (int, float))
    max_rewrites = r.get("comment.max_rewrites", int)
    overlap = r.get("comment.grounding_min_overlap", int)
    phrases = r.get("comment.forbidden_phrases", list)

    if brand is not None and not brand.strip():
        r.reasons.append("comment.brand: 비어 있어요")
    if min_chars is not None and max_chars is not None and min_chars >= max_chars:
        r.reasons.append("comment.min_chars: comment.max_chars보다 작아야 해요")
    for key, mode in (("min_count", min_count), ("max_count", max_count)):
        if mode is not None and mode not in COUNT_MODES:
            r.reasons.append(f"comment.{key}: {'·'.join(COUNT_MODES)} 중 하나여야 해요({mode!r})")
    if max_similarity is not None and not 0 <= max_similarity <= 1:
        r.reasons.append("comment.max_similarity: 0~1 사이여야 해요")
    if max_rewrites is not None and max_rewrites < 0:
        r.reasons.append("comment.max_rewrites: 0 이상이어야 해요")
    # 아래 둘이 꺼지면 금지 조항·맥락 검사가 조용히 사라진다 — 오류로 막는다.
    if overlap is not None and overlap < 1:
        r.reasons.append("comment.grounding_min_overlap: 1 이상이어야 해요(0이면 영상 내용 검사가 꺼져요)")
    if phrases is not None and not [p for p in phrases if str(p).strip()]:
        r.reasons.append("comment.forbidden_phrases: 하나 이상 있어야 해요(비면 지어낸 후기 검사가 꺼져요)")

    if None in (brand, brand_desc, min_chars, max_chars, min_count, max_count, max_similarity,
                max_rewrites, overlap, phrases):
        return None
    return CommentRules(
        brand=brand, brand_desc=brand_desc, min_chars=min_chars, max_chars=max_chars,
        min_count=min_count, max_count=max_count, max_similarity=float(max_similarity),
        max_rewrites=max_rewrites, grounding_min_overlap=overlap,
        forbidden_phrases=tuple(str(p) for p in phrases if str(p).strip()),
    )


def _rising_rules(r: _Reader) -> RisingRules | None:
    values = {
        "keyword": r.get("rising.keyword", str),
        "published_within_hours": r.get("rising.published_within_hours", int),
        "min_views": r.get("rising.min_views", int),
        "max_views": r.get("rising.max_views", int),
        "min_views_per_hour": r.get("rising.min_views_per_hour", (int, float)),
        "remeasure": r.get("rising.remeasure", bool),
        "remeasure_min_gap_minutes": r.get("rising.remeasure_min_gap_minutes", int),
    }
    if None in values.values():
        return None
    values["min_views_per_hour"] = float(values["min_views_per_hour"])
    return RisingRules(**values)


def _secrets(env_path: Path) -> dict[str, str | None]:
    """비밀 값: 이미 설정된 환경 변수가 먼저, 없으면 .env 파일. 둘 다 없으면 None(오류 아님, D-28)."""
    file_values = dotenv_values(env_path) if env_path.exists() else {}
    result = {}
    for key in SECRET_KEYS:
        value = os.environ.get(key) or file_values.get(key)
        result[key] = value.strip() if value and value.strip() else None
    return result
