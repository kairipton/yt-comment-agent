"""fetch 테스트 — 네트워크 없이, 저장해 둔 꼴의 표본으로 읽어 풀기와 실패 처리를 본다."""

from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError
from youtube_transcript_api import TranscriptsDisabled

from agent import fetch
from agent.fetch import (
    FetchError,
    YouTubeClient,
    parse_comment_threads,
    parse_oembed,
    parse_rss,
    parse_videos_list,
)
from agent.models import Channel, Source
from tests.conftest import CHANNEL_A, TEST_ID, video

CH = Channel(id=CHANNEL_A, name="외롭여")


def rss(*entries: tuple[str, str, str]) -> str:
    """(영상 id, 올린 시각, 제목)으로 채널 RSS 꼴의 XML을 만든다. 실제 피드에서 쓰는 태그만 담았다."""
    body = "".join(f"""
  <entry>
    <yt:videoId>{vid}</yt:videoId>
    <yt:channelId>{CHANNEL_A}</yt:channelId>
    <title>{title}</title>
    <author><name>외롭여</name></author>
    <published>{when}</published>
    <media:group>
      <media:thumbnail url="https://i4.ytimg.com/vi/{vid}/hqdefault.jpg"/>
      <media:description>{title} 설명</media:description>
    </media:group>
  </entry>""" for vid, when, title in entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">{body}
</feed>"""


SAMPLE = rss(("old00000001", "2026-10-01T10:00:00+00:00", "옛 영상"),
             ("new00000001", "2026-10-18T08:30:08+00:00", "새 영상"),
             ("mid00000001", "2026-10-10T10:00:00+00:00", "중간 영상"))


# ── 목록 ──────────────────────────────────────────────────────────────────────
def 기능_목록_RSS에서_최신순으로_N개를_꺼낸다():
    """목적: 피드 순서와 상관없이 올린 날짜 최신순으로 per_channel개만 꺼낸다(D-11)."""
    assert [v.id for v in parse_rss(SAMPLE, CH, 2)] == ["new00000001", "mid00000001"]


def 기능_목록_제목_썸네일_설명을_읽는다():
    """목적: 목록 한 줄에 화면이 쓰는 값과 초안 재료(설명)가 담긴다."""
    v = parse_rss(SAMPLE, CH, 1)[0]
    assert (v.title, v.thumbnail_url, v.description, v.source) == (
        "새 영상", "https://i4.ytimg.com/vi/new00000001/hqdefault.jpg", "새 영상 설명", Source.CHANNEL)


def 기능_목록_RSS에서_조회수를_읽는다():
    """목적: 실제 피드의 media:community/media:statistics views를 조회수로 읽는다 — 화면 ②가 「조회수 n회」를 보여 준다."""
    # 조회수 태그가 있는 항목 하나와 없는 항목 하나 — 없으면 None이어야 한다.
    xml = SAMPLE.replace(
        "<media:description>새 영상 설명</media:description>",
        "<media:description>새 영상 설명</media:description>"
        '<media:community><media:statistics views="32150"/></media:community>')
    views = {v.id: v.view_count for v in parse_rss(xml, CH, 3)}
    assert views == {"new00000001": 32150, "mid00000001": None, "old00000001": None}


def 경계_목록_올린날짜는_시간대가_붙는다():
    """목적: 기록장이 시간대 없는 시각을 거부하므로, 목록의 올린 날짜는 언제나 시간대가 붙어 있다."""
    assert parse_rss(SAMPLE, CH, 1)[0].published_at.tzinfo is not None


def 기능_목록_채널하나가_실패해도_나머지는_나온다(monkeypatch):
    """목적: 한 채널의 피드가 실패해도 다른 채널의 목록은 보여 주고, 실패는 이유와 함께 따로 돌려준다."""
    other = Channel(id="UCbbbbbbbbbbbbbbbbbbbbbb", name="다른채널")

    def get_text(self, url):
        if other.id in url:
            raise FetchError("유튜브 응답 오류(HTTP 500)")
        return SAMPLE

    monkeypatch.setattr(YouTubeClient, "_get_text", get_text)
    result = YouTubeClient(None, 5).latest_videos([CH, other], 3)
    assert (len(result.videos), list(result.failures)) == (3, ["다른채널"])


class 업로드API:
    """YouTube API 흉내 — 업로드 재생목록(playlistItems)과 영상 정보(videos)를 돌려주고, 받은 요청을 모아 둔다."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
        self._what = None

    def playlistItems(self):
        self._what = "playlist"
        return self

    def videos(self):
        self._what = "videos"
        return self

    def list(self, **kwargs):
        self.calls.append((self._what, kwargs))
        return self

    def execute(self):
        if self.fail:
            raise ConnectionResetError("끊김")
        if self._what == "playlist":
            return {"items": [{"contentDetails": {"videoId": "api00000001"}}]}
        return {"items": [{"id": "api00000001", "snippet": {
            "title": "API로 받은 영상", "channelId": CHANNEL_A, "channelTitle": "외롭여",
            "publishedAt": "2026-10-20T06:00:00Z", "thumbnails": {}}, "statistics": {"viewCount": "10"}}]}


def 기능_목록_RSS가_한번_실패하면_다시_읽는다(monkeypatch):
    """목적: 피드가 200과 404를 오락가락하므로, 첫 번째가 실패해도 한 번 더 읽어 목록을 낸다(실패로 알리지 않는다)."""
    tries = []

    def get_text(self, url):
        tries.append(url)
        if len(tries) == 1:
            raise FetchError("유튜브 응답 오류(HTTP 404)")
        return SAMPLE

    monkeypatch.setattr(YouTubeClient, "_get_text", get_text)
    result = YouTubeClient(None, 5).latest_videos([CH], 3)
    assert (len(result.videos), result.failures, len(tries)) == (3, {}, 2)


def 기능_목록_RSS가_계속_실패하면_업로드목록으로_대신한다(monkeypatch):
    """목적: RSS가 두 번 다 실패하고 키가 있으면 채널의 업로드 재생목록(UU…)으로 영상을 가져온다 — 출처는 지정 채널 그대로."""
    def get_text(self, url):
        raise FetchError("유튜브 응답 오류(HTTP 404)")

    api = 업로드API()
    monkeypatch.setattr(YouTubeClient, "_get_text", get_text)
    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: api)
    result = YouTubeClient("키", 5).latest_videos([CH], 3)
    v = result.videos[0]
    assert (v.id, v.source, v.channel_id, result.failures) == ("api00000001", Source.CHANNEL, CHANNEL_A, {})
    assert api.calls[0][1]["playlistId"] == "UU" + CHANNEL_A[2:]


def 예외_목록_대체도_실패하면_두_이유를_함께_알린다(monkeypatch):
    """목적: RSS도 API 대체도 실패하면 그 채널을 실패로 알리되, 두 이유를 함께 담는다(무엇을 고쳐야 할지 보이게)."""
    def get_text(self, url):
        raise FetchError("유튜브 응답 오류(HTTP 404)")

    monkeypatch.setattr(YouTubeClient, "_get_text", get_text)
    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: 업로드API(fail=True))
    reason = YouTubeClient("키", 5).latest_videos([CH], 3).failures["외롭여"]
    assert "HTTP 404" in reason and "API 대체도 실패" in reason


def 기능_목록_업로드목록에서_영상id를_꺼낸다():
    """목적: playlistItems 응답에서 영상 id만 차례대로 꺼낸다(id 없는 항목은 건너뜀)."""
    from agent.fetch import parse_playlist_items
    data = {"items": [{"contentDetails": {"videoId": "a1"}}, {"contentDetails": {}}, {"contentDetails": {"videoId": "b2"}}]}
    assert parse_playlist_items(data) == ["a1", "b2"]


# ── 테스트 영상 ────────────────────────────────────────────────────────────────
def 기능_테스트영상_키없으면_oEmbed로_채널id를_비운다():
    """목적: 키 없이 만든 테스트 영상은 채널 id를 추정해 채우지 않는다 — 허용 범위 우회 통로를 막는다."""
    v = parse_oembed({"title": "썸 타는 사람", "author_name": "새벽민트", "thumbnail_url": "t.jpg"}, TEST_ID)
    assert (v.channel_id, v.source, v.published_at) == ("", Source.TEST, None)


def 기능_테스트영상_키있으면_설명까지_읽는다():
    """목적: videos.list 응답에서 제목·채널·날짜·설명을 모두 읽는다."""
    data = {"items": [{"id": TEST_ID, "snippet": {
        "title": "썸", "channelId": "UCcccccccccccccccccccccc", "channelTitle": "새벽민트",
        "publishedAt": "2026-10-20T06:00:00Z", "description": "고민 두 줄",
        "thumbnails": {"high": {"url": "h.jpg"}}}}]}
    v = parse_videos_list(data)[0]
    assert (v.description, v.published_at.tzinfo is not None, v.thumbnail_url) == ("고민 두 줄", True, "h.jpg")


def 기능_테스트영상_키있으면_조회수까지_읽는다():
    """목적: videos.list(part=snippet,statistics) 응답의 viewCount를 조회수로 읽는다."""
    data = {"items": [{"id": TEST_ID, "snippet": {"title": "썸", "thumbnails": {}},
                       "statistics": {"viewCount": "1234"}}]}
    assert parse_videos_list(data)[0].view_count == 1234


def 경계_테스트영상_조회수가_없어도_목록에서_빠지지_않는다():
    """목적: 조회수가 비공개(statistics 없음)여도 영상은 목록에 남고 조회수만 None — 뜨기 직전 측정의 건너뛰기가 옮겨 오면 테스트 영상이 사라진다."""
    data = {"items": [{"id": TEST_ID, "snippet": {"title": "썸", "thumbnails": {}}, "statistics": {}}]}
    videos = parse_videos_list(data)
    assert [(v.id, v.view_count) for v in videos] == [(TEST_ID, None)]


# ── 초안 재료 ──────────────────────────────────────────────────────────────────
def 기능_재료_자막을_못받아도_None으로_돌려준다(monkeypatch):
    """목적: 자막이 없거나 막혀도 재료 가져오기는 실패하지 않는다 — 작가가 제목·설명으로 대신한다."""
    class NoTranscript:
        def __init__(self, **kwargs):  # 진짜처럼 http_client를 받는다
            pass

        def fetch(self, video_id, languages):
            raise TranscriptsDisabled(video_id)

    monkeypatch.setattr(fetch, "YouTubeTranscriptApi", NoTranscript)
    assert YouTubeClient(None, 5).context(video()).transcript is None


def 기능_재료_키가없으면_인기댓글은_빈목록이다(monkeypatch):
    """목적: YouTube 키가 없으면 인기 댓글 없이 진행한다(API를 부르지 않는다)."""
    class Transcript:
        def __init__(self, **kwargs):  # 진짜처럼 http_client를 받는다
            pass

        def fetch(self, video_id, languages):
            return [SimpleNamespace(text="자막")]

    monkeypatch.setattr(fetch, "YouTubeTranscriptApi", Transcript)
    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: pytest.fail("키 없이 API를 부름"))
    assert YouTubeClient(None, 5).context(video()).top_comments == ()


def 기능_재료_인기댓글_본문만_꺼낸다():
    """목적: commentThreads 응답에서 최상위 댓글 본문만 꺼내고 빈 댓글은 버린다."""
    data = {"items": [{"snippet": {"topLevelComment": {"snippet": {"textOriginal": " 공감해요 "}}}},
                      {"snippet": {"topLevelComment": {"snippet": {"textOriginal": ""}}}}]}
    assert parse_comment_threads(data) == ("공감해요",)


def 기능_오류문구에_키값이_없다(monkeypatch):
    """목적: Google API 오류 원문에 실린 요청 주소(key=…)가 실패 이유로 새어 나가지 않는다."""
    secret = "AIzaSECRETSECRET"
    error = HttpError(SimpleNamespace(status=403, reason="Forbidden"), b"{}",
                      uri=f"https://youtube.googleapis.com/youtube/v3/videos?key={secret}")

    class Api:
        def videos(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self):
            raise error

    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: Api())
    result = YouTubeClient(secret, 5).test_videos([TEST_ID])
    assert secret not in str(result.failures) and "403" in result.failures[TEST_ID]


# ── 링크 입력 ───────────────────────────────────────────────────────────
def 기능_링크영상_출처는_링크이고_채널id를_담는다(monkeypatch):
    """목적: 링크로 가져온 영상은 출처가 「링크」이고, 키가 있으면 videos.list의 진짜 채널 id를 담는다 — 허용 범위를 채널로 판정하게."""
    data = {"items": [{"id": "abcdefghijk", "snippet": {
        "title": "남의 영상", "channelId": "UCzzzzzzzzzzzzzzzzzzzzzz", "channelTitle": "다른 채널",
        "publishedAt": "2026-10-20T06:00:00Z", "thumbnails": {}}}]}

    class Api:
        def videos(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self):
            return data

    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: Api())
    v = YouTubeClient("키", 5).videos_by_id(["abcdefghijk"], Source.LINK).videos[0]
    assert (v.source, v.channel_id) == (Source.LINK, "UCzzzzzzzzzzzzzzzzzzzzzz")


# ── 뜨기 직전 후보 ──────────────────────────────────────────────────────
def 기능_뜨기직전_검색은_id를_측정은_조회수를_읽는다():
    """목적: 검색 응답에서는 영상 id만(채널 결과는 건너뜀), 측정 응답에서는 조회수·출처 RISING·잰 시각을 읽는다."""
    from agent.fetch import parse_measured, parse_search
    from tests.conftest import at
    search = {"items": [{"id": {"kind": "youtube#video", "videoId": "abcdefghijk"}},
                        {"id": {"kind": "youtube#channel", "channelId": "UCzzzzzzzzzzzzzzzzzzzzzz"}}]}
    assert parse_search(search) == ["abcdefghijk"]
    data = {"items": [{"id": "abcdefghijk", "snippet": {"title": "썸", "channelId": "UCzz", "channelTitle": "누군가",
                                                         "publishedAt": "2026-10-20T01:00:00Z", "thumbnails": {}},
                       "statistics": {"viewCount": "2300"}}]}
    m = parse_measured(data, at(5))[0]
    assert (m.views, m.video.source, m.measured_at) == (2300, Source.RISING, at(5))


def 예외_뜨기직전_키가_없으면_검색하지_않는다(monkeypatch):
    """목적: YouTube 키가 없으면 API를 부르지 않고 이유와 함께 FetchError."""
    from agent.fetch import FetchError
    from tests.conftest import at
    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: pytest.fail("키 없이 API를 부름"))
    with pytest.raises(FetchError, match="YouTube API 키"):
        YouTubeClient(None, 5).search_recent("연애", at(0))


def 기능_재료_연결이_끊겨도_초안재료는_대신한다(monkeypatch):
    """목적: 자막·인기 댓글을 받다 연결이 끊기면(시간 초과·연결 끊김) 초안 전체를 실패시키지 않고 없는 재료로 대신한다."""
    class 끊김:
        def __init__(self, **kwargs):
            pass

        def fetch(self, *args, **kwargs):
            raise ConnectionResetError("끊김")

    class Api:
        def commentThreads(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self):
            raise TimeoutError("시간 초과")

    monkeypatch.setattr(fetch, "YouTubeTranscriptApi", 끊김)
    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: Api())
    ctx = YouTubeClient("키", 5).context(video(TEST_ID, channel_id="", source=Source.TEST))
    assert (ctx.transcript, ctx.top_comments) == (None, ())


def 기능_목록_연결이_끊기면_이유를_담아_알린다(monkeypatch):
    """목적: API 호출 중 연결이 끊겨도 서버 오류(500)로 터지지 않고, 그 영상을 「연결 문제」 이유와 함께 못 가져온 것으로 돌려준다."""
    class Api:
        def videos(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self):
            raise ConnectionResetError("끊김")

    monkeypatch.setattr(YouTubeClient, "_youtube", lambda self: Api())
    result = YouTubeClient("키", 5).test_videos([TEST_ID])
    assert result.videos == [] and "연결 문제" in result.failures[TEST_ID]
