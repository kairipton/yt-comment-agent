"""config 테스트 — 저장소 설정이 읽히는지, 잘못된 설정이 조용히 넘어가지 않는지."""

from pathlib import Path

import pytest
import yaml

from agent.config import ConfigError, load_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def 키_없는_환경(monkeypatch):
    """사용자 PC의 환경 변수가 테스트에 섞이지 않게 비밀 값 두 개를 지운다."""
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def 고친_설정(tmp_path: Path, change) -> Path:
    """저장소 config.yaml을 읽어 change(사전)로 고친 뒤 임시 폴더에 써서 경로를 돌려준다."""
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    change(raw)
    # 지시문 파일도 config.yaml 기준 경로라 함께 옮긴다.
    prompt = tmp_path / "prompts" / "comment_system.md"
    prompt.parent.mkdir(exist_ok=True)
    if not prompt.exists():
        prompt.write_text((ROOT / "prompts" / "comment_system.md").read_text(encoding="utf-8"), encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def 기능_설정읽기_저장소의_config가_읽힌다():
    """목적: 저장소의 config.yaml이 늘 유효한지 지킨다 — 손으로 고치다 깨지면 여기서 잡힌다."""
    settings = load_settings(ROOT / "config.yaml", ROOT / "없는.env")
    assert settings.comment.brand == "운명원"


def 기능_설정읽기_API키가_없어도_성공한다():
    """목적: 키가 없어도 설정 읽기는 성공하고 키 자리는 None이다(D-28)."""
    settings = load_settings(ROOT / "config.yaml", ROOT / "없는.env")
    assert settings.anthropic_api_key is None and settings.youtube_api_key is None


def 기능_설정읽기_env파일의_키를_읽는다(tmp_path):
    """목적: .env 파일에 적힌 키를 읽는다."""
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=abc123\n", encoding="utf-8")
    assert load_settings(ROOT / "config.yaml", env).anthropic_api_key == "abc123"


def 기능_설정읽기_경로는_설정파일_폴더_기준이다(tmp_path):
    """목적: db_path 같은 상대 경로가 실행 위치가 아니라 config.yaml이 있는 폴더 기준으로 풀린다."""
    path = 고친_설정(tmp_path, lambda raw: None)
    assert load_settings(path, tmp_path / ".env").db_path == tmp_path / "data" / "agent.db"


def 예외_설정읽기_빠진_키는_이유와_함께_거부된다(tmp_path):
    """목적: 코드에 기본값이 없으므로, 키가 빠지면 그 키 이름을 담아 거부한다."""
    path = 고친_설정(tmp_path, lambda raw: raw["comment"].pop("min_chars"))
    with pytest.raises(ConfigError) as e:
        load_settings(path, tmp_path / ".env")
    assert "comment.min_chars: 값이 없어요(빠진 키)" in e.value.reasons


def 기능_설정읽기_이유는_설정경로로_시작한다(tmp_path):
    """목적: 설정 하나에 걸린 이유는 「경로: 설명」 꼴이다 — 옵션 페이지가 앞의 경로로 칸을 찾아 칸 이름으로 바꾼다."""
    def change(raw):
        raw["comment"]["min_chars"] = 500      # 범위 관계 위반
        raw["fetch"]["per_channel"] = None     # 옵션 페이지에서 숫자 칸을 비우고 저장한 경우
        raw["profiles"][0]["user_data_dir"] = ""  # 목록의 한 줄이 덜 채워진 경우
    with pytest.raises(ConfigError) as e:
        load_settings(고친_설정(tmp_path, change), tmp_path / ".env")
    # 이유마다 그 설정 경로 + 「: 」로 시작해야 한다(순서는 상관없다).
    assert sorted(r.split(": ", 1)[0] for r in e.value.reasons) == ["comment.min_chars", "fetch.per_channel", "profiles[0]"]


def 예외_설정읽기_잘못된_값을_한번에_모두_알린다(tmp_path):
    """목적: 문제가 여럿이면 첫 번째에서 멈추지 않고 전부 모아 알린다."""
    def change(raw):
        raw["post_mode"] = "반자동"
        raw["limits"]["daily_max_comments"] = 0
    with pytest.raises(ConfigError) as e:
        load_settings(고친_설정(tmp_path, change), tmp_path / ".env")
    assert len(e.value.reasons) == 2


def 예외_설정읽기_금지표현이_비면_거부된다(tmp_path):
    """목적: 지어낸 후기 목록을 비워 금지 조항 검사가 조용히 꺼지는 것을 막는다."""
    path = 고친_설정(tmp_path, lambda raw: raw["comment"].update(forbidden_phrases=[]))
    with pytest.raises(ConfigError):
        load_settings(path, tmp_path / ".env")


def 예외_설정읽기_겹침기준이_0이면_거부된다(tmp_path):
    """목적: 재료 겹침 기준을 0으로 두어 맥락 검사가 조용히 꺼지는 것을 막는다."""
    path = 고친_설정(tmp_path, lambda raw: raw["comment"].update(grounding_min_overlap=0))
    with pytest.raises(ConfigError):
        load_settings(path, tmp_path / ".env")


def 예외_설정읽기_채널id_꼴이_틀리면_거부된다(tmp_path):
    """목적: 오타 난 채널 id(UC로 시작하는 24자가 아님)를 받지 않는다."""
    path = 고친_설정(tmp_path, lambda raw: raw["channels"][0].update(id="UC짧음"))
    with pytest.raises(ConfigError):
        load_settings(path, tmp_path / ".env")


def 예외_설정읽기_하한이_상한보다_크면_거부된다(tmp_path):
    """목적: 글자 수 하한·상한이 뒤집힌 설정을 받지 않는다."""
    path = 고친_설정(tmp_path, lambda raw: raw["comment"].update(min_chars=130))
    with pytest.raises(ConfigError):
        load_settings(path, tmp_path / ".env")


def 예외_설정읽기_모르는_effort는_거부된다(tmp_path):
    """목적: 생각 깊이 값 오타(예: hi)를 받지 않는다 — API가 400으로 거부하기 전에 설정에서 막는다."""
    path = 고친_설정(tmp_path, lambda raw: raw["llm"].update(effort="hi"))
    with pytest.raises(ConfigError):
        load_settings(path, tmp_path / ".env")


def 예외_설정읽기_지시문파일이_없으면_거부된다(tmp_path):
    """목적: 댓글 지시문 파일 경로가 틀리면 조용히 넘어가지 않고 경로와 함께 거부한다."""
    path = 고친_설정(tmp_path, lambda raw: raw["llm"].update(prompt_file="없는/지시문.md"))
    with pytest.raises(ConfigError) as e:
        load_settings(path, tmp_path / ".env")
    assert any(r.startswith("llm.prompt_file: 파일을 읽지 못했어요") for r in e.value.reasons)


def 예외_설정읽기_지시문의_모르는_자리표시는_거부된다(tmp_path):
    """목적: 지시문에 오타 난 자리표시({brnad} 같은)가 있으면 그 이름과 쓸 수 있는 목록을 알려 주며 거부한다."""
    path = 고친_설정(tmp_path, lambda raw: None)
    (tmp_path / "prompts" / "comment_system.md").write_text("{brnad}을 언급하세요.", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_settings(path, tmp_path / ".env")
    assert any("모르는 자리표시: brnad" in r for r in e.value.reasons)


# ── 옵션 페이지의 저장 ─────────────────────────────────────────────────────────
def 원본_복사(tmp_path: Path) -> Path:
    """저장소 config.yaml과 지침 파일을 임시 폴더에 복사해 그 config 경로를 돌려준다."""
    prompt = tmp_path / "prompts" / "comment_system.md"
    prompt.parent.mkdir(exist_ok=True)
    prompt.write_text((ROOT / "prompts" / "comment_system.md").read_text(encoding="utf-8"), encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_bytes((ROOT / "config.yaml").read_bytes())
    return path


def 기능_옵션저장_바꾼_값이_다시_읽힌다(tmp_path):
    """목적: 옵션 페이지가 보낸 값을 저장하면 다시 읽었을 때 그 값이고, 나머지 값은 그대로다."""
    from agent.config import read_options, save_options
    path = 원본_복사(tmp_path)
    values = read_options(path)
    values["limits"]["daily_max_comments"] = 7
    save_options(path, values)
    settings = load_settings(path, tmp_path / ".env")
    assert (settings.daily_max, settings.comment.brand) == (7, "운명원")


def 예외_옵션저장_잘못된_값이면_파일이_그대로다(tmp_path):
    """목적: 검사에 걸리는 값(하루 상한 0)이면 이유와 함께 거부하고, 설정 파일은 한 글자도 바뀌지 않는다."""
    from agent.config import read_options, save_options
    path = 원본_복사(tmp_path)
    original = path.read_bytes()
    values = read_options(path)
    values["limits"]["daily_max_comments"] = 0
    with pytest.raises(ConfigError) as e:
        save_options(path, values)
    assert path.read_bytes() == original
    assert any("daily_max_comments" in r for r in e.value.reasons)


def 기능_지침저장_올바른_글은_저장된다(tmp_path):
    """목적: 쓸 수 있는 자리표시만 쓴 지침은 저장되고, 다음에 읽으면 그 글이다."""
    from agent.config import read_prompt, save_prompt
    path = 원본_복사(tmp_path)
    save_prompt(path, "{brand}을 자연스럽게 언급하는 댓글을 쓰세요.")
    assert read_prompt(path) == "{brand}을 자연스럽게 언급하는 댓글을 쓰세요."


def 예외_지침저장_모르는_자리표시는_거부된다(tmp_path):
    """목적: 오타 자리표시({brnad})가 든 지침은 저장하지 않고 이유와 함께 거부한다(파일 그대로)."""
    from agent.config import read_prompt, save_prompt
    path = 원본_복사(tmp_path)
    before = read_prompt(path)
    with pytest.raises(ConfigError) as e:
        save_prompt(path, "{brnad}을 언급하세요.")
    assert read_prompt(path) == before
    assert any("brnad" in r for r in e.value.reasons)


def 기능_옵션저장_모델과_생각깊이는_바뀌지_않는다(tmp_path):
    """목적: 옵션 페이지가 AI 모델·생각 깊이를 바꿔 보내도 저장하지 않는다 — 이 둘은 설정 파일을 직접 고쳐야 바뀐다."""
    from agent.config import read_options, save_options
    path = 원본_복사(tmp_path)
    values = read_options(path)
    before = (values["llm"]["model"], values["llm"]["effort"])
    values["llm"]["model"], values["llm"]["effort"] = "다른-모델", "low"
    save_options(path, values)
    saved = read_options(path)["llm"]
    assert (saved["model"], saved["effort"]) == before


def 기능_옵션저장_개발모드는_바뀌지_않는다(tmp_path):
    """목적: 개발 모드는 설정 파일로만 켠다 — 옵션 페이지가 값을 바꿔 보내도 저장하지 않는다."""
    from agent.config import read_options, save_options
    path = 원본_복사(tmp_path)
    values = read_options(path)
    before = values["dev_mode"]
    values["dev_mode"] = not before
    save_options(path, values)
    assert read_options(path)["dev_mode"] == before


@pytest.mark.parametrize(("path", "change"), [
    ("limits.max_retries_per_post", lambda raw: raw["limits"].update(max_retries_per_post=-1)),
    ("limits.daily_max_comments", lambda raw: raw["limits"].update(daily_max_comments=True)),  # bool은 숫자가 아니다
    ("profiles", lambda raw: raw.update(profiles=[])),
    ("profiles", lambda raw: raw["profiles"].append(dict(raw["profiles"][0]))),  # 같은 이름 두 번
    ("channels", lambda raw: raw.update(channels=[])),
], ids=["재시도_음수", "하루상한_참거짓", "프로필_없음", "프로필_이름중복", "채널_없음"])
def 예외_설정읽기_운영규칙_값이_잘못되면_거부된다(tmp_path, path, change):
    """목적: 운영 규칙에 들어가는 값이 잘못되면 조용히 쓰이지 않고, 그 설정 경로를 단 이유로 거부된다."""
    with pytest.raises(ConfigError) as e:
        load_settings(고친_설정(tmp_path, change), tmp_path / ".env")
    # 이유는 「경로: 설명」 꼴이다 — 그 경로로 시작하는 이유가 있어야 한다.
    assert any(r.split(": ", 1)[0] == path for r in e.value.reasons), e.value.reasons
