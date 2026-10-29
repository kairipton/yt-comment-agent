"""수집 담당 — 유튜브에서 목록(지정 채널 최신 영상 · 테스트 영상)과 초안 재료(자막 · 인기 댓글)를 가져온다.
명세: docs/systems/fetch.md

판정하지 않는다. 가져온 것을 models의 자료형으로 돌려줄 뿐이다.
YouTube API 키는 없어도 된다(D-28):
  · 목록은 채널 RSS(키 불필요)        · 테스트 영상은 키가 있으면 videos.list, 없으면 oEmbed
  · 자막은 youtube-transcript-api(키 불필요) · 인기 댓글만 키가 있을 때 commentThreads.list

받아 오기(_get_text · _youtube)와 읽어 풀기(parse_*)를 나눠 두었다 — 단위 테스트는 네트워크 없이
저장해 둔 표본으로 parse_*를 검사하고, 받아 오기는 바꿔 끼운다(TESTING.md).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httplib2
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import CouldNotRetrieveTranscript, YouTubeTranscriptApi

from agent.models import Channel, Source, VideoContext, VideoItem

log = logging.getLogger(__name__)

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
OEMBED_URL = "https://www.youtube.com/oembed?format=json&url={}"
WATCH_URL = "https://www.youtube.com/watch?v={}"
MAX_TOP_COMMENTS = 20
SEARCH_MAX = 50  # search.list · videos.list 한 번에 받는 최대 개수
_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
       "media": "http://search.yahoo.com/mrss/"}


class FetchError(Exception):
    """가져오기 실패. 무엇을 하다 실패했는지만 담는다 — 키 값·요청 주소는 넣지 않는다."""


@dataclass(frozen=True)
class VideoList:
    """목록과, 가져오지 못한 것(이름 → 이유). 하나가 실패해도 나머지는 보여 주려고 함께 돌려준다."""

    videos: list[VideoItem]
    failures: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Measured:
    """뜨기 직전 후보 하나 — 영상과 그때 잰 조회수. 판정은 trend가 한다."""

    video: VideoItem
    views: int
    measured_at: datetime


# ── 읽어 풀기 (네트워크 없음) ──────────────────────────────────────────────────
def parse_rss(xml_text: str, channel: Channel, per_channel: int) -> list[VideoItem]:
    """채널 RSS에서 최신 영상 per_channel개를 꺼낸다. 피드에 있는 영상을 가리지 않는다(쇼츠 포함)."""
    root = ET.fromstring(xml_text)
    videos = []
    for entry in root.findall("atom:entry", _NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=_NS)
        thumb = entry.find("media:group/media:thumbnail", _NS)
        stats = entry.find("media:group/media:community/media:statistics", _NS)  # 조회수 — 피드에 없으면 None
        videos.append(VideoItem(
            id=vid,
            title=entry.findtext("atom:title", default="", namespaces=_NS),
            channel_id=entry.findtext("yt:channelId", default=channel.id, namespaces=_NS),
            channel_name=entry.findtext("atom:author/atom:name", default=channel.name, namespaces=_NS),
            published_at=_parse_time(entry.findtext("atom:published", default="", namespaces=_NS)),
            thumbnail_url=thumb.get("url", "") if thumb is not None else _thumb(vid),
            source=Source.CHANNEL,
            description=entry.findtext("media:group/media:description", default="", namespaces=_NS),
            view_count=_int_or_none(stats.get("views") if stats is not None else None),
        ))
    # 피드는 대개 최신순이지만 보장되지 않는다 — 올린 날짜로 다시 정렬한다. 날짜 없는 항목은 뒤로.
    videos.sort(key=lambda v: v.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return videos[:per_channel]


def parse_oembed(data: dict, video_id: str, source: Source = Source.TEST) -> VideoItem:
    """키 없이 영상 정보를 만든다(테스트 영상 · 링크). 날짜·설명은 없고, 채널 id는 추정하지 않고 비워 둔다(허용 범위 우회 방지)."""
    return VideoItem(id=video_id, title=data.get("title", ""), channel_id="",
                     channel_name=data.get("author_name", ""), published_at=None,
                     thumbnail_url=data.get("thumbnail_url") or _thumb(video_id), source=source)


def parse_videos_list(data: dict, source: Source = Source.TEST) -> list[VideoItem]:
    """YouTube Data API videos.list(part=snippet[,statistics]) 응답을 영상으로 읽는다. 출처는 부르는 쪽이 정한다(테스트 영상 · 링크).

    조회수가 없는 영상(비공개 집계)도 빼지 않는다 — 조회수만 None. 빼는 것은 뜨기 직전 측정(parse_measured)의 몫이다.
    """
    videos = []
    for item in data.get("items", []):
        snip = item.get("snippet", {})
        thumbs = snip.get("thumbnails", {})
        thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
        videos.append(VideoItem(
            id=item["id"], title=snip.get("title", ""), channel_id=snip.get("channelId", ""),
            channel_name=snip.get("channelTitle", ""), published_at=_parse_time(snip.get("publishedAt", "")),
            thumbnail_url=thumb or _thumb(item["id"]), source=source,
            description=snip.get("description", ""),
            view_count=_int_or_none(item.get("statistics", {}).get("viewCount")),
        ))
    return videos


def parse_playlist_items(data: dict) -> list[str]:
    """playlistItems.list(part=contentDetails) 응답에서 영상 id만 차례대로 꺼낸다(id 없는 항목은 건너뛴다)."""
    return [vid for item in data.get("items", []) if (vid := item.get("contentDetails", {}).get("videoId"))]


def _uploads_playlist(channel_id: str) -> str:
    """채널의 「업로드한 영상」 재생목록 id — 채널 id의 앞 UC를 UU로 바꾼 것(YouTube 규칙)."""
    return "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id


def parse_search(data: dict) -> list[str]:
    """search.list 응답에서 영상 id만 꺼낸다(채널·재생목록 결과는 건너뛴다)."""
    return [item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")]


def parse_measured(data: dict, now: datetime) -> list[Measured]:
    """videos.list(part=snippet,statistics) 응답을 「영상 + 조회수」로. 출처는 뜨기 직전(RISING). 조회수가 없으면(비공개 집계) 건너뛴다."""
    measured = []
    for video in parse_videos_list(data, Source.RISING):
        item = next(i for i in data["items"] if i["id"] == video.id)
        views = item.get("statistics", {}).get("viewCount")
        if views is None or video.published_at is None:
            continue
        measured.append(Measured(video=video, views=int(views), measured_at=now))
    return measured


def parse_comment_threads(data: dict) -> tuple[str, ...]:
    """commentThreads.list 응답에서 최상위 댓글 본문만 꺼낸다."""
    texts = []
    for item in data.get("items", []):
        snip = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
        text = (snip.get("textOriginal") or snip.get("textDisplay") or "").strip()
        if text:
            texts.append(text)
    return tuple(texts)


def _parse_time(text: str) -> datetime | None:
    """ISO 시각 → 시간대가 붙은 datetime. 기록장이 시간대 없는 값을 거부하므로 시간대는 언제나 붙인다."""
    if not text:
        return None
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else None


def _int_or_none(text: str | None) -> int | None:
    """조회수 문자열 → 정수. 없거나 숫자가 아니면 None — 조회수는 보여 주기만 하는 값이라 목록을 깨지 않는다."""
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


# ── 받아 오기 ──────────────────────────────────────────────────────────────────
class YouTubeClient:
    def __init__(self, api_key: str | None, timeout: float):
        """api_key가 없으면 키 없는 경로(RSS · oEmbed · 자막)만 쓴다."""
        self._api_key = api_key
        self.timeout = timeout

    @property
    def has_key(self) -> bool:
        return self._api_key is not None

    def latest_videos(self, channels: list[Channel], per_channel: int) -> VideoList:
        """① 채널마다 최신 per_channel개. 실패한 채널은 failures에 이유와 함께 담는다.

        RSS(키·할당량 불필요)를 먼저 읽고, 실패하면 한 번 더 읽는다 — 피드가 200과 404를 오락가락한다.
        그래도 실패하고 YouTube 키가 있으면 채널의 업로드 재생목록으로 대신 가져온다(채널당 할당량 2).
        """
        videos, failures = [], {}
        for channel in channels:
            try:
                videos += self._channel_rss(channel, per_channel)
                continue
            except (FetchError, ET.ParseError) as e:
                reason = str(e) if isinstance(e, FetchError) else "피드 형식을 읽지 못함"
            if self.has_key:
                fallback = self._channel_uploads(channel, per_channel)
                if fallback.videos:
                    log.info("채널 RSS 실패 — 업로드 목록으로 대신함: %s (%s)", channel.name, reason)
                    videos += fallback.videos
                    continue
                reason = f"{reason} · API 대체도 실패: {', '.join(set(fallback.failures.values())) or '영상 없음'}"
            log.warning("채널 목록 실패: %s — %s", channel.name, reason)
            failures[channel.name] = reason
        return VideoList(videos, failures)

    def _channel_rss(self, channel: Channel, per_channel: int) -> list[VideoItem]:
        """채널 RSS를 읽는다. 실패하면 한 번 더 — 두 번째 실패는 그대로 올린다."""
        try:
            return parse_rss(self._get_text(RSS_URL.format(channel.id)), channel, per_channel)
        except (FetchError, ET.ParseError):
            return parse_rss(self._get_text(RSS_URL.format(channel.id)), channel, per_channel)

    def _channel_uploads(self, channel: Channel, per_channel: int) -> VideoList:
        """RSS 대신: 업로드 재생목록(UC… → UU…)에서 최신 영상 id를 얻고, videos_by_id로 정보를 채운다. 출처는 지정 채널."""
        try:
            data = self._youtube().playlistItems().list(
                part="contentDetails", playlistId=_uploads_playlist(channel.id), maxResults=per_channel).execute()
        except _API_ERRORS as e:
            return VideoList([], {channel.name: _http_reason("업로드 목록", e)})
        found = self.videos_by_id(parse_playlist_items(data), Source.CHANNEL)
        newest = sorted(found.videos, key=lambda v: v.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return VideoList(newest[:per_channel], found.failures)

    def test_videos(self, ids: list[str]) -> VideoList:
        """① 테스트 영상(일부 공개라 RSS에 없음)."""
        return self.videos_by_id(ids, Source.TEST)

    def videos_by_id(self, ids: list[str], source: Source) -> VideoList:
        """id로 영상을 가져온다 — 테스트 영상과 링크 입력이 같은 길을 쓴다. 키가 있으면 videos.list, 없으면 oEmbed."""
        if not ids:
            return VideoList([])
        if self.has_key:
            try:
                data = self._youtube().videos().list(part="snippet,statistics", id=",".join(ids)).execute()
            except _API_ERRORS as e:
                return VideoList([], {i: _http_reason("영상 정보", e) for i in ids})
            found = parse_videos_list(data, source)
            missing = {i: "영상을 찾지 못함(삭제·비공개?)" for i in ids if i not in {v.id for v in found}}
            return VideoList(found, missing)
        videos, failures = [], {}
        for vid in ids:
            try:
                data = json.loads(self._get_text(OEMBED_URL.format(urllib.parse.quote(WATCH_URL.format(vid), safe=""))))
                videos.append(parse_oembed(data, vid, source))
            except (FetchError, json.JSONDecodeError) as e:
                failures[vid] = str(e) if isinstance(e, FetchError) else "응답 형식을 읽지 못함"
        return VideoList(videos, failures)

    def context(self, video: VideoItem) -> VideoContext:
        """② 초안 재료. 자막은 못 받으면 None, 인기 댓글은 키가 없거나 못 받으면 빈 튜플(이유는 기록)."""
        return VideoContext(video=video, transcript=self._transcript(video.id), top_comments=self._top_comments(video.id))

    # ── 바꿔 끼울 수 있는 받아 오기 (테스트가 대신 넣는다) ──────────────────────
    def _get_text(self, url: str) -> str:
        """주소를 열어 본문을 문자열로. 실패하면 무엇을 하다 실패했는지만 담은 FetchError."""
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise FetchError(f"유튜브 응답 오류(HTTP {e.code})") from None
        except OSError as e:  # URLError · 시간 초과 · 연결 끊김(ConnectionResetError)까지
            reason = getattr(e, "reason", e)
            raise FetchError(f"유튜브에 연결하지 못함({type(reason).__name__})") from None

    def _youtube(self):
        """YouTube Data API 클라이언트. 키가 있을 때만 부른다."""
        return build("youtube", "v3", developerKey=self._api_key, cache_discovery=False,
                     http=httplib2.Http(timeout=self.timeout))

    def _transcript(self, video_id: str) -> str | None:
        """한국어 자막(사람이 단 것 우선, 없으면 자동 생성)을 이어 붙인다. 자르지 않는다."""
        try:
            fetched = YouTubeTranscriptApi(http_client=_TimeoutSession(self.timeout)).fetch(video_id, languages=["ko"])
        except CouldNotRetrieveTranscript as e:
            log.info("자막 없음 — 제목·설명으로 대신함: %s (%s)", video_id, type(e).__name__)
            return None
        except (requests.RequestException, OSError) as e:  # 연결 문제 — 자막은 없어도 되는 재료라 None으로 대신한다
            log.warning("자막을 받지 못함(연결 문제) — 제목·설명으로 대신함: %s (%s)", video_id, type(e).__name__)
            return None
        text = " ".join(s.text.strip() for s in fetched if s.text.strip())
        return text or None

    def search_recent(self, keyword: str, published_after: datetime) -> list[str]:
        """뜨기 직전 후보 검색 — 한국 · 한국어 · 영상만, 그 시각 뒤에 올라온 것을 최신순으로 최대 50개. 할당량 100.

        조회수 순은 이미 뜬 영상(수만~수십만)만 와서 기준(1,000~5,000)에 하나도 안 든다 — 실제로 재 봤다.
        최신순이면 갓 올라와 빠르게 오르는 영상이 많이 담긴다(50개 중 20개가 기준 통과).

        YouTube 키가 있어야 한다 — 키 없이 검색할 길이 없다.
        """
        if not self.has_key:
            raise FetchError("뜨기 직전 영상 찾기에는 YouTube API 키가 필요해요(README의 「키 설정」 참고).")
        after = published_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            data = self._youtube().search().list(
                part="id", q=keyword, type="video", publishedAfter=after, regionCode="KR",
                relevanceLanguage="ko", order="date", maxResults=SEARCH_MAX).execute()
        except _API_ERRORS as e:
            raise FetchError(_http_reason("영상 검색", e)) from None
        return parse_search(data)

    def measure(self, ids: list[str], now: datetime) -> list[Measured]:
        """영상 정보와 조회수를 잰다(50개씩, 할당량 1). 출처는 RISING."""
        if not self.has_key:
            raise FetchError("조회수를 재려면 YouTube API 키가 필요해요(README의 「키 설정」 참고).")
        measured: list[Measured] = []
        for i in range(0, len(ids), SEARCH_MAX):
            try:
                data = self._youtube().videos().list(part="snippet,statistics", id=",".join(ids[i:i + SEARCH_MAX])).execute()
            except _API_ERRORS as e:
                raise FetchError(_http_reason("조회수", e)) from None
            measured += parse_measured(data, now)
        return measured

    def _top_comments(self, video_id: str) -> tuple[str, ...]:
        if not self.has_key:
            return ()
        try:
            data = self._youtube().commentThreads().list(
                part="snippet", videoId=video_id, order="relevance", maxResults=MAX_TOP_COMMENTS,
                textFormat="plainText").execute()
        except _API_ERRORS as e:
            log.warning("인기 댓글 못 받음: %s — %s", video_id, _http_reason("인기 댓글", e))
            return ()
        return parse_comment_threads(data)


# YouTube Data API 호출에서 날 수 있는 오류 — 응답 오류(HttpError) · httplib2 오류 · 연결 문제(시간 초과·끊김은 OSError).
_API_ERRORS = (HttpError, httplib2.HttpLib2Error, OSError)


def _http_reason(what: str, error: Exception) -> str:
    """API 오류를 상태 코드(또는 오류 종류)만 담은 문구로. 오류 원문에는 요청 주소(key=…)가 실릴 수 있어 쓰지 않는다."""
    if isinstance(error, HttpError):
        return f"{what} 가져오기 실패(HTTP {getattr(error.resp, 'status', '?')})"
    return f"{what} 가져오기 실패(연결 문제: {type(error).__name__})"


class _TimeoutSession(requests.Session):
    """요청마다 시간 한도를 거는 세션 — 자막 라이브러리가 기본으로 한도 없이 기다려, 응답이 멎으면 무한히 멈추는 것을 막는다."""

    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)
