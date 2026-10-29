"""guard 테스트 — 허용 범위, 영상당 1개·하루 상한, 글자 수, 댓글 규칙, 초안 일치, 재시도."""

from dataclasses import replace
from datetime import datetime

import pytest

from agent import guard
from agent.models import PostStatus, Source
from tests.conftest import CHANNEL_A, CHANNEL_X, GOOD_COMMENT, SOURCE_TEXT, TEST_ID, at, video

ALLOWED = {CHANNEL_A}
TESTS = {TEST_ID}


# ── 시각 ──────────────────────────────────────────────────────────────────────
def 경계_하루구간_한국시간_자정에서_나뉜다():
    """목적: UTC 14:59(한국 23:59)와 15:00(한국 다음날 00:00)이 서로 다른 하루에 든다."""
    _, end = guard.kst_day_range(at(14, 59))
    start, _ = guard.kst_day_range(at(15, 0))
    assert end == start == at(15, 0)


def 예외_하루구간_시간대가_없으면_거부된다():
    """목적: 시간대 없는 시각으로 하루를 자르지 않는다."""
    with pytest.raises(ValueError):
        guard.kst_day_range(datetime(2026, 10, 20, 3))


def 기능_세는구간_오늘_초기화부터_센다():
    """목적: 오늘 안에 초기화가 있었으면 하루 상한은 그 시각부터 센다(개발 모드의 초기화)."""
    assert guard.quota_window(at(5), at(3)) == (at(3), at(15))


def 경계_세는구간_어제_초기화는_무시한다():
    """목적: 오늘 00:00(한국) 직전의 초기화는 무시하고 오늘 00:00부터 센다 — 자정이 지나면 늘 0부터."""
    # at(5)의 한국 하루는 UTC 19일 15:00에 시작한다. 그 1분 전 초기화는 어제 것이다.
    assert guard.quota_window(at(5), at(14, 59, day=19)) == (at(15, day=19), at(15))


# ── 허용 범위 ──────────────────────────────────────────────────────────────────
def 기능_허용범위_지정채널_영상은_통과한다():
    """목적: 지정 채널 영상은 게시할 수 있다."""
    assert guard.check_target(video(), ALLOWED, TESTS, channel_posting=True).ok


def 기능_허용범위_다른채널_영상은_거부된다():
    """목적: 지정되지 않은 채널의 영상은 막는다."""
    assert not guard.check_target(video(channel_id=CHANNEL_X, source=Source.LINK), ALLOWED, TESTS, channel_posting=True).ok


def 기능_허용범위_테스트영상은_통과한다():
    """목적: 본인 테스트 영상은 채널이 지정 목록에 없어도 id로 통과한다."""
    assert guard.check_target(video(TEST_ID, channel_id=CHANNEL_X, source=Source.TEST), ALLOWED, TESTS, channel_posting=True).ok


def 기능_허용범위_뜨기직전_영상은_지정채널이어도_거부된다():
    """목적: 뜨기 직전 영상은 초안까지만 — 지정 채널 영상이어도 게시를 막는다(D-22)."""
    assert not guard.check_target(video(source=Source.RISING), ALLOWED, TESTS, channel_posting=True).ok


# ── 영상당 1개 · 하루 상한 ─────────────────────────────────────────────────────
def 기능_예외판정_dev모드의_테스트영상만_예외다():
    """목적: 예외는 dev_mode가 켜져 있고 테스트 영상일 때뿐이다(D-17·D-23)."""
    test_video = video(TEST_ID, source=Source.TEST)
    cases = [guard.is_exempt(test_video, TESTS, True), guard.is_exempt(test_video, TESTS, False),
             guard.is_exempt(video(), TESTS, True)]
    assert cases == [True, False, False]


def 경계_하루상한_10번째는_허용되고_11번째는_거부된다():
    """목적: 오늘 9개를 눌렀으면 10번째는 허용, 10개를 눌렀으면 11번째는 거부."""
    allowed = guard.check_quota(False, False, submitted_today=9, daily_max=10).ok
    blocked = guard.check_quota(False, False, submitted_today=10, daily_max=10).ok
    assert (allowed, blocked) == (True, False)


def 기능_하루상한_예외영상도_막힌다():
    """목적: 하루 상한은 테스트 영상(예외)에도 적용된다(D-17)."""
    assert not guard.check_quota(False, True, submitted_today=10, daily_max=10).ok


def 기능_영상당1개_이미_게시한_영상은_거부된다():
    """목적: 예외가 아닌 영상은 이미 게시했으면 막는다."""
    assert not guard.check_quota(True, False, submitted_today=0, daily_max=10).ok


def 기능_영상당1개_예외영상은_이미_게시해도_통과한다():
    """목적: dev_mode의 테스트 영상은 이미 게시했어도 통과한다(D-17)."""
    assert guard.check_quota(True, True, submitted_today=0, daily_max=10).ok


def 기능_상한판정_이유를_모두_모은다():
    """목적: 상한과 영상당 1개에 동시에 걸리면 이유 두 개를 모두 돌려준다."""
    assert len(guard.check_quota(True, False, submitted_today=10, daily_max=10).reasons) == 2


# ── 글자 수 ───────────────────────────────────────────────────────────────────
def 경계_글자수_공백제외와_공백포함을_따로_센다():
    """목적: 공백 제외는 스페이스·줄바꿈을 빼고, 공백 포함은 전부 센다(D-21)."""
    text = "가 나\n다"
    assert (guard.char_count(text, "no_spaces"), guard.char_count(text, "with_spaces")) == (3, 5)


def 예외_글자수_모르는_세기방식은_거부된다():
    """목적: 설정 오타로 세기 방식이 이상하면 조용히 넘어가지 않는다."""
    with pytest.raises(ValueError):
        guard.char_count("가나다", "spaces")


# ── 댓글 규칙 ──────────────────────────────────────────────────────────────────
def 기능_댓글검사_좋은_댓글은_통과한다(rules):
    """목적: 재료의 장면을 짚고 운명원을 전해 들은 말투로 언급한 댓글은 모든 검사를 통과한다."""
    verdict = guard.check_comment(GOOD_COMMENT, rules, SOURCE_TEXT, others=[])
    assert verdict.ok, verdict.reasons


def 경계_댓글길이_하한과_상한에서_통과하고_벗어나면_거부된다(rules):
    """목적: 공백 제외 40자는 통과·39자는 거부, 공백 포함 120자는 통과·121자는 거부.

    길이만 보려고 다른 검사(브랜드·겹침)는 규칙에서 느슨하게 한다.
    """
    loose = replace(rules, brand="가", brand_desc="가", grounding_min_overlap=1)
    source = "가가가"

    def ok(text):
        return guard.check_comment(text, loose, source, []).ok

    assert [ok("가" * 40), ok("가" * 39), ok("가" * 120), ok("가" * 121)] == [True, False, True, False]


@pytest.mark.parametrize("link", ["https://a.com", "www.naver.com", "youtu.be/abc", "unmyeong.kr", "abc.co 가봐"])
def 기능_댓글검사_링크가_있으면_거부된다(rules, link):
    """목적: 주소 머리 없는 도메인 꼴까지 링크를 막는다(금지 조항)."""
    assert "링크가 들어 있어요." in guard.check_comment(GOOD_COMMENT + " " + link, rules, SOURCE_TEXT, []).reasons


@pytest.mark.parametrize("tag", ["#연애", "＃운명원"])
def 기능_댓글검사_해시태그가_있으면_거부된다(rules, tag):
    """목적: 반각·전각 해시태그를 모두 막는다(금지 조항)."""
    assert "해시태그가 들어 있어요." in guard.check_comment(GOOD_COMMENT + " " + tag, rules, SOURCE_TEXT, []).reasons


def 기능_댓글검사_운명원이_없으면_거부된다(rules):
    """목적: 유도 대상인 운명원이 빠진 댓글은 막는다."""
    text = GOOD_COMMENT.replace("운명원", "그 집")
    assert not guard.check_comment(text, rules, SOURCE_TEXT, []).ok


def 기능_댓글검사_지어낸_후기_표현은_거부된다(rules):
    """목적: 운명원을 직접 이용해 본 것처럼 쓴 표현을 막는다(금지 조항)."""
    text = GOOD_COMMENT.replace("잘 본다던데", "다녀왔는데 소름")
    assert not guard.check_comment(text, rules, SOURCE_TEXT, []).ok


def 기능_댓글검사_전해들은_말투는_통과한다(rules):
    """목적: 「잘 본다던데」 같은 들은 말투는 지어낸 후기가 아니다."""
    reasons = guard.check_comment(GOOD_COMMENT, rules, SOURCE_TEXT, []).reasons
    assert not any("직접 이용" in r for r in reasons)



def _지어낸후기로_걸리나(text, rules) -> bool:
    """check_text의 이유 중 「직접 이용해 본 것 같은」(지어낸 후기)이 있나 — 길이 같은 다른 규칙과 섞이지 않게 이것만 본다."""
    return any("직접 이용해 본 것 같은" in r for r in guard.check_text(text, rules, []).reasons)


@pytest.mark.parametrize("text", [
    "저번에 운명원 가봤는데 진짜 딱 맞더라고요 답장 늦는 썸남 얘기가 제 얘기 같아서 웃펐어요",
    "작년에 운명원 갔었는데 그때 들은 말이 이 영상이랑 똑같아서 놀랐어요 기다리지 말라는 말이요",
    "친구랑 운명원 예약해서 연애 사주 봤는데 제 밀당 패턴을 정확히 짚어 줘서 소름이었어요",
    "제가 다녀온 운명원은 재회운도 본다던데 영상 속 환승이별 얘기에 자꾸 그 말이 떠올라요",
    "운명원 덕분에 마음 정리했어요 영상 속 연락 없는 남자친구 얘기가 딱 작년의 저라서 찔렸네요",
    "재회 고민할 때 운명원 선생님이 말씀하신 게 딱 이거였어요 억지로 붙잡으면 더 멀어진다고",
    "소개팅 전에 긴장돼서 혼자 운명원 들렀는데 웃는 얼굴이 중요하다고 해서 진짜 도움 됐어요",
], ids=["가봤는데", "갔었는데", "예약해서_봤는데", "앞에_다녀온", "덕분에", "말씀하신", "들렀는데"])
def 예외_댓글검사_운명원_곁의_경험표현은_지어낸후기다(text, rules):
    """목적: 운명원을 직접 이용해 본 것처럼 쓴 말이 운명원 바로 곁에 있으면 표현 목록에 없어도 지어낸 후기로 막는다(금지 조항)."""
    assert _지어낸후기로_걸리나(text, rules)


@pytest.mark.parametrize("text", [
    "친구가 운명원 가봤는데 썸 타이밍을 잘 본대요 저도 답장 늦는 이 사람 마음이 궁금하네요",
    "회사 동료가 운명원 얘기를 하던데 이 사람이랑 인연인지 저도 한번 물어보고 싶어요ㅠ",
    "사주 잘 본다는 운명원 얘기 들은 뒤로 제 연애 패턴도 다시 보게 되네요 다음엔 달라지고 싶어요",
    "운명원 가 볼까 고민 중인데 영상 속 3시간 답장 없는 장면 보니까 더 궁금해지네요",
    "친구한테 운명원 추천받았는데 이 사람이 내 인연인지부터 물어보고 싶어요 영상 보고 더 그러네요",
    "운명원 다녀온 지인이 연락 문제는 결국 타이밍이라는 말을 들었대요 영상이랑 똑같네요",
    "답장 늦는 사람 기다리다 지쳐서 운명원 검색해 봤는데 평이 진짜 좋더라고요 조만간 물어볼까 봐요",
], ids=["친구가_가봤는데_본대요", "동료가_하던데", "들은_뒤로", "가볼까_고민", "추천받았는데", "다녀온_지인이", "검색해_봤는데"])
def 기능_댓글검사_남의_경험을_전해들은_말은_통과한다(text, rules):
    """목적: 남이 겪은 일을 전해 들은 말 · 앞으로 해 볼 마음 · 추천받은 일은 지어낸 후기가 아니다 — 막지 않는다."""
    assert not _지어낸후기로_걸리나(text, rules)



@pytest.mark.parametrize("text", [
    "친구가 운명원 다녀왔는데 썸 타이밍을 잘 본대요 저도 답장 늦는 이 사람 마음이 궁금하네요",
    "재회 고민할 때 운명원 가 본 사람들 후기가 많던데 다들 기다리라는 말을 들었다더라고요",
], ids=["친구가_다녀왔는데_본대요", "사람들_후기_들었다더라"])
def 기능_댓글검사_목록표현도_전해들은_말이면_통과한다(text, rules):
    """목적: 설정의 금지 표현(다녀왔 · 후기)도 남의 경험을 전해 들은 말 속에 있으면 막지 않는다 — 막으면 쓸데없이 다시 쓰게 된다."""
    assert not _지어낸후기로_걸리나(text, rules)


@pytest.mark.parametrize("text", [
    "친구랑 같이 운명원 다녀왔는데 소름 돋게 맞았어요 영상 속 썸 얘기도 거기서 들은 대로였네요",
    "저번에 엄마랑 운명원 다녀왔는데 재회운 없다고 해서 단념했어요 영상 보니 또 생각나네요",
    "언니는 별로라던데 저는 운명원 다녀왔는데 진짜 잘 봐 줘서 신기했어요 영상 속 얘기 같더라고요",
], ids=["친구랑_같이", "엄마랑", "언니는_별로라던데_저는"])
def 예외_댓글검사_남과_함께한_내_경험은_지어낸후기다(text, rules):
    """목적: 남이 문장에 나와도 겪은 사람이 나면 지어낸 후기다 — 「전해 들은 말」 예외가 너무 느슨해지지 않게 못박는다."""
    assert _지어낸후기로_걸리나(text, rules)

def 경계_댓글검사_운명원에서_먼_경험표현은_보지_않는다(rules):
    """목적: 운명원과 상관없는 내 경험(소개팅에서 상담 받았던 일 등)이 멀리 있으면 지어낸 후기로 보지 않는다."""
    text = "저도 회사에서 연애 상담 받았는데 영상 속 3시간 답장 없는 장면이 딱 그 얘기더라고요 운명원 연애 사주 궁금해요"
    assert not _지어낸후기로_걸리나(text, rules)


def 기능_댓글검사_재료와_겹치는_조각이_모자라면_거부된다(rules):
    """목적: 영상 내용과 동떨어진 뻔한 댓글은 막는다(보조 검사)."""
    generic = "연애 진짜 어렵네요 매번 혼자 애태우다 끝나는 느낌이에요 운명원 연애사주 잘 본다던데 저도 보고 싶다"
    reasons = guard.check_comment(generic, rules, SOURCE_TEXT, []).reasons
    assert any("겹치는 말" in r for r in reasons)


def 기능_댓글검사_브랜드_조각은_겹침에_세지_않는다(rules):
    """목적: 재료에 「운명원 연애사주」가 있어도 그 조각으로 겹침을 채우지 못한다.

    댓글과 재료가 겹치는 조각은 운명원·연애사·애사주 셋뿐이다 — 브랜드를 빼지 않으면 3개로 통과해 버린다.
    """
    source = "운명원 연애사주"
    text = "운명원 연애사주 정말 궁금해서 저도 언젠가 꼭 한번 보고 싶네요 요즘 너무 답답하거든요"
    reasons = guard.check_comment(text, rules, source, []).reasons
    assert any("겹치는 말" in r for r in reasons)


def 기능_댓글검사_다른_글과_너무_비슷하면_거부된다(rules):
    """목적: 다른 영상에 쓴 글과 거의 같은 댓글은 막는다(영상마다 다르게)."""
    reasons = guard.check_comment(GOOD_COMMENT, rules, SOURCE_TEXT, others=[GOOD_COMMENT]).reasons
    assert any("비슷해요" in r for r in reasons)


def 기능_댓글검사_이유를_모두_모은다(rules):
    """목적: 여러 규칙에 걸리면 첫 번째에서 멈추지 않고 전부 돌려준다 — 작가가 한 번에 고치게."""
    text = GOOD_COMMENT.replace("운명원", "그 집") + " #연애 https://a.com"
    assert len(guard.check_comment(text, rules, SOURCE_TEXT, []).reasons) >= 3


# ── 재시도 ─────────────────────────────────────────────────────────────────────
def 기능_재시도_게시버튼_전_실패는_허용된다():
    """목적: 게시 버튼 전 실패는 자동 재시도한다(D-12)."""
    assert guard.can_retry(PostStatus.FAILED_BEFORE_SUBMIT, retry_no=0, max_retries=2)


def 기능_재시도_게시버튼_후_실패는_막힌다():
    """목적: 게시 버튼 뒤 실패는 실제로 달렸을 수 있어 재시도하지 않는다(D-12)."""
    assert not guard.can_retry(PostStatus.FAILED_AFTER_SUBMIT, retry_no=0, max_retries=2)


def 경계_재시도_2회를_쓰면_더는_안된다():
    """목적: 한 번 누를 때 재시도는 2회까지 — 재시도 1·2는 허용, 2를 쓴 뒤엔 멈춘다(총 3번 시도)."""
    allowed = [guard.can_retry(PostStatus.FAILED_BEFORE_SUBMIT, n, 2) for n in (0, 1, 2)]
    assert allowed == [True, True, False]


def 기능_재시도_취소는_막힌다():
    """목적: 사용자가 크롬 창을 닫아 취소한 시도는 버튼 전 실패여도 다시 띄우지 않는다 [사용자]."""
    assert not guard.can_retry(PostStatus.FAILED_BEFORE_SUBMIT, retry_no=0, max_retries=2, cancelled=True)


# ── 채널 게시 스위치 · 재료 없는 검사 (게시 로봇용) ──────────────────────────────
def 기능_허용범위_채널게시가_꺼지면_테스트영상만_통과한다():
    """목적: channel_posting이 꺼져 있으면 지정 채널 영상은 막히고 테스트 영상만 통과한다(D-34)."""
    # 같은 스위치 값으로 지정 채널 영상과 테스트 영상을 함께 본다.
    channel = guard.check_target(video(), ALLOWED, TESTS, channel_posting=False)
    test = guard.check_target(video(TEST_ID, channel_id=CHANNEL_X, source=Source.TEST), ALLOWED, TESTS,
                              channel_posting=False)
    assert (channel.ok, test.ok) == (False, True)


def 기능_댓글검사_재료없이_링크와_지어낸후기를_잡는다(rules):
    """목적: 게시 로봇의 재검사(check_text)도 재료 없이 링크·지어낸 후기를 잡는다."""
    text = GOOD_COMMENT.replace("잘 본다던데", "받아봤는데") + " youtu.be/abc"
    reasons = " ".join(guard.check_text(text, rules, []).reasons)
    assert "링크" in reasons and "받아봤" in reasons


def 기능_댓글검사_재료없는_검사는_겹침을_보지_않는다(rules):
    """목적: check_text는 재료 겹침을 보지 않는다 — 재료가 없는 게시 로봇이 좋은 댓글을 막지 않게."""
    assert guard.check_text(GOOD_COMMENT, rules, []).ok


def 예외_초안일치_불합격이거나_글이_다르면_거부된다():
    """목적: 게시할 글이 저장된 합격 초안 그대로가 아니면 막는다 — 불합격 초안과 바뀐 글 둘 다."""
    from agent.models import Draft
    failed = Draft("vid00000001", "고민", GOOD_COMMENT, issues=("해시태그가 들어 있어요.",))
    passed = Draft("vid00000001", "고민", GOOD_COMMENT)
    results = (guard.check_draft(failed, GOOD_COMMENT).ok, guard.check_draft(passed, GOOD_COMMENT + "!").ok,
               guard.check_draft(None, GOOD_COMMENT).ok, guard.check_draft(passed, f" {GOOD_COMMENT}\n").ok)
    assert results == (False, False, False, True)


# ── 링크에서 영상 id ────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "https://youtu.be/z_K3rHRbqck", "https://youtu.be/z_K3rHRbqck?si=abc", "youtu.be/z_K3rHRbqck",
    "https://www.youtube.com/watch?v=z_K3rHRbqck", "https://m.youtube.com/watch?feature=share&v=z_K3rHRbqck&t=30",
    "https://www.youtube.com/shorts/z_K3rHRbqck", "https://youtube.com/live/z_K3rHRbqck?feature=x",
    "https://www.youtube.com/embed/z_K3rHRbqck", "  z_K3rHRbqck  ",
])
def 기능_링크_여러_꼴에서_영상id를_뽑는다(text):
    """목적: 사람이 흔히 붙여 넣는 유튜브 영상 링크 꼴에서 모두 같은 영상 id가 나온다."""
    assert guard.parse_video_id(text) == TEST_ID


@pytest.mark.parametrize("text", [
    "https://www.youtube.com/@example_channel", "https://www.youtube.com/playlist?list=PL123",
    "https://example.com/watch?v=z_K3rHRbqck", "영상 링크 아님", "", "https://youtu.be/짧은",
])
def 예외_링크_영상링크가_아니면_None이다(text):
    """목적: 채널 주소·재생목록·다른 사이트·엉뚱한 글은 영상 id로 읽지 않는다."""
    assert guard.parse_video_id(text) is None
