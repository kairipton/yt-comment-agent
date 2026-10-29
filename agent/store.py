"""기록장 — SQLite 파일 하나에 게시 시도와 초안을 저장하고 조회한다. 명세: docs/systems/store.md

판정은 하지 않는다. 무엇이 예외인지·몇 개가 상한인지는 부르는 쪽(guard)이 정해 넘긴다.
단 하나, 「영상당 1개」만은 DB 제약으로도 막는다(D-05) — 판정 쪽에 실수가 있어도 저장에서 거부되는 마지막 방어선.

영상당 1개를 막는 방법:
  attempts.unique_video 열에 유니크 제약을 건다(SQLite는 NULL을 여러 개 허용한다).
  상태가 처음으로 「게시 버튼을 누른 상태」가 될 때, 예외가 아닌 줄만 그 열에 영상 id를 채운다.
  → 버튼 전 실패 줄·재시도 줄·예외(dev_mode의 테스트 영상) 줄은 NULL이라 서로 부딪치지 않는다.

서버와 게시 로봇(별도 프로세스)이 같은 파일을 함께 쓴다. 그래서 WAL 모드 + 바쁨 대기 + 짧은 트랜잭션.

시도의 일생: QUEUED(대기) → 게시 로봇이 claim으로 잡으면 OPENING → … → 끝난 상태(완료·실패·거부).
끝난 줄은 상태를 다시 바꾸지 않는다 — 같은 시도를 두 번 띄워도 완료 위에 거부가 덮이지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from agent.models import Attempt, Draft, PostStatus, Source, VideoItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    video_id      TEXT NOT NULL,
    video_title   TEXT NOT NULL,
    channel_id    TEXT NOT NULL,
    channel_name  TEXT NOT NULL,
    published_at  TEXT,
    thumbnail_url TEXT NOT NULL,
    source        TEXT NOT NULL,
    profile       TEXT NOT NULL,
    text          TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT,
    screenshot    TEXT,
    comment_url   TEXT,
    retry_no      INTEGER NOT NULL,
    exempt        INTEGER NOT NULL,
    submitted_at  TEXT,
    unique_video  TEXT UNIQUE,
    root_id       INTEGER,
    typed_chars   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS drafts (
    video_id   TEXT PRIMARY KEY,
    worry      TEXT NOT NULL,
    comment    TEXT NOT NULL,
    version    INTEGER NOT NULL,
    approved   INTEGER NOT NULL,
    issues     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    transcript_chars INTEGER,
    comments_read    INTEGER
);
-- 하루 상한 초기화 시각(개발 모드의 [초기화]). 게시 기록은 지우지 않고, 「여기부터 다시 센다」는 표시만 남긴다.
CREATE TABLE IF NOT EXISTS quota_resets (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_at TEXT NOT NULL
);
"""

# 나중에 생긴 열 — 옛 기록장 파일에는 없어서, 열 때 없는 것만 더한다(_migrate). (표, 열, 열 정의)
_ADDED_COLUMNS = (
    ("attempts", "typed_chars", "INTEGER NOT NULL DEFAULT 0"),  # 크롬에 친 글자 수(화면 B안)
    ("drafts", "transcript_chars", "INTEGER"),                  # AI가 읽은 자막 글자 수. NULL = 옛 초안
    ("drafts", "comments_read", "INTEGER"),                     # AI가 읽은 인기 댓글 수. NULL = 키 없음 또는 옛 초안
)


class DuplicatePostError(Exception):
    """예외가 아닌 같은 영상에 두 번째 게시를 저장하려 했다(D-05 마지막 방어선)."""


def _ts(dt: datetime) -> str:
    """시각을 저장용 문자열로. 시간대 없는 값은 받지 않는다 — 하루 경계가 어긋나는 것을 막는다.

    자릿수가 늘 같은 UTC 꼴이라 문자열 크기 비교가 곧 시각 비교다(submitted_count가 이것에 기댄다).
    """
    if dt.tzinfo is None:
        raise ValueError("시간대가 없는 시각은 받지 않아요(예: datetime.now(UTC)).")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dt(text: str | None) -> datetime | None:
    if text is None:
        return None
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class Store:
    def __init__(self, db_path: Path, test_ids: set[str] | frozenset[str]):
        """파일·표가 없으면 만든다. test_ids는 「예외 표시가 테스트 영상에만 붙었나」 검증에 쓴다."""
        self.db_path = Path(db_path)
        self.test_ids = frozenset(test_ids)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """옛 기록장 파일에 새 열을 더한다. CREATE TABLE IF NOT EXISTS는 이미 있는 표에 열을 더하지 않는다.

        서버와 게시 로봇이 거의 동시에 같은 파일을 열면, 한쪽이 먼저 더한 열을 다른 쪽이 또 더하려다
        「duplicate column」 오류가 난다 — 그 오류만 넘긴다(열은 이미 있다). 다른 오류는 그대로 올린다.
        """
        for table, column, spec in _ADDED_COLUMNS:
            have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column in have:
                continue
            try:
                with conn:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 게시 시도 ─────────────────────────────────────────────────────────────
    def start_attempt(self, video: VideoItem, profile: str, text: str, now: datetime,
                      *, exempt: bool, retry_no: int = 0, root_id: int | None = None) -> int:
        """시도 한 줄을 「대기 중」 상태로 만들고 번호를 돌려준다.

        root_id: 재시도 줄이면 ③ 한 번 누름의 첫 시도 id. 첫 시도(None)는 자기 id가 root_id가 된다.
        """
        if exempt and video.id not in self.test_ids:
            # 판정 쪽 실수로 지정 채널 영상이 예외로 와도 여기서 막는다(D-05).
            raise ValueError(f"예외 표시는 테스트 영상에만 붙일 수 있어요: {video.id}")
        if retry_no < 0:
            raise ValueError("retry_no는 0 이상이어야 해요.")
        stamp = _ts(now)
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                """INSERT INTO attempts (created_at, updated_at, video_id, video_title, channel_id, channel_name,
                       published_at, thumbnail_url, source, profile, text, status, retry_no, exempt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (stamp, stamp, video.id, video.title, video.channel_id, video.channel_name,
                 _ts(video.published_at) if video.published_at else None, video.thumbnail_url,
                 video.source.value, profile, text, PostStatus.QUEUED.value, retry_no, int(exempt)),
            )
            aid = int(cur.lastrowid)
            conn.execute("UPDATE attempts SET root_id = ? WHERE id = ?", (root_id or aid, aid))
            return aid

    def claim(self, attempt_id: int, now: datetime) -> bool:
        """대기 중인 시도만 「프로필 여는 중」으로 바꾼다. 이미 잡힌 시도면 False — 아무것도 쓰지 않는다.

        UPDATE 한 문장이 「대기 중인가 확인 + 바꾸기」를 함께 해서, 로봇 둘이 동시에 잡아도 한쪽만 성공한다.
        """
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("UPDATE attempts SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                               (PostStatus.OPENING.value, _ts(now), attempt_id, PostStatus.QUEUED.value))
            return cur.rowcount == 1

    def set_status(self, attempt_id: int, status: PostStatus, now: datetime, *, reason: str | None = None,
                   screenshot: Path | None = None, comment_url: str | None = None) -> None:
        """진행 상태를 바꾼다.

        처음으로 「게시 버튼을 누른 상태」가 되는 순간 submitted_at을 적고(하루 상한이 센다),
        예외가 아니면 unique_video에 영상 id를 채운다(영상당 1개). 같은 영상이 이미 채워져 있으면
        DuplicatePostError — 트랜잭션이 통째로 취소되어 이 줄의 상태도 바뀌지 않는다.
        """
        stamp = _ts(now)
        with closing(self._connect()) as conn, conn:
            # 읽기 전에 쓰기 잠금부터 — 「끝났나 확인 → 바꾸기」 사이에 다른 프로세스(서버·로봇)가 끼어들지 못하게.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT video_id, exempt, submitted_at, status FROM attempts WHERE id = ?",
                               (attempt_id,)).fetchone()
            if row is None:
                raise LookupError(f"없는 시도예요: {attempt_id}")
            if PostStatus(row["status"]).finished:
                raise ValueError(f"이미 끝난 시도예요({PostStatus(row['status']).label}): {attempt_id}")
            conn.execute(
                """UPDATE attempts SET status = ?, updated_at = ?,
                       reason = COALESCE(?, reason), screenshot = COALESCE(?, screenshot),
                       comment_url = COALESCE(?, comment_url)
                   WHERE id = ?""",
                (status.value, stamp, reason, str(screenshot) if screenshot else None, comment_url, attempt_id),
            )
            if status.pressed_submit and row["submitted_at"] is None:
                unique = None if row["exempt"] else row["video_id"]
                try:
                    conn.execute("UPDATE attempts SET submitted_at = ?, unique_video = ? WHERE id = ?",
                                 (stamp, unique, attempt_id))
                except sqlite3.IntegrityError as e:
                    raise DuplicatePostError(f"이미 댓글을 단 영상이에요: {row['video_id']}") from e

    def set_typed(self, attempt_id: int, count: int, now: datetime) -> None:
        """크롬에 친 글자 수만 적는다(화면의 「86자 중 45자」). 상태는 바꾸지 않고, 끝난 줄이면 아무것도 쓰지 않는다."""
        finished = [s.value for s in PostStatus if s.finished]
        marks = ",".join("?" * len(finished))
        with closing(self._connect()) as conn, conn:
            conn.execute(f"UPDATE attempts SET typed_chars = ?, updated_at = ? WHERE id = ? AND status NOT IN ({marks})",
                         (count, _ts(now), attempt_id, *finished))

    def posted_attempt(self, video_id: str) -> Attempt | None:
        """그 영상의 「먼저 단 게시」 — 영상당 1개 열(unique_video)이 채워진 줄. 예외(dev_mode 테스트 영상) 줄은 비어 있어 나오지 않는다."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM attempts WHERE unique_video = ?", (video_id,)).fetchone()
        return _attempt(row) if row is not None else None

    def get(self, attempt_id: int) -> Attempt:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise LookupError(f"없는 시도예요: {attempt_id}")
        return _attempt(row)

    def latest_in_root(self, root_id: int) -> Attempt:
        """③ 한 번 누름(root_id)의 가장 최근 줄 — 재시도가 돌면 그 재시도 줄. 화면은 첫 id로 이것을 묻는다."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM attempts WHERE root_id = ? ORDER BY id DESC LIMIT 1",
                               (root_id,)).fetchone()
        if row is None:
            raise LookupError(f"없는 시도예요: {root_id}")
        return _attempt(row)

    def fail_unfinished(self, except_id: int, now: datetime, reason: str) -> list[int]:
        """진행 중으로 남은 다른 줄(앞선 로봇이 죽어 끝나지 못한 줄)을 실패로 끝내고 그 번호들을 돌려준다.

        게시 버튼 기록(submitted_at)이 있으면 버튼 후 실패, 없으면 버튼 전 실패.
        게시 로봇이 잠금을 잡은 뒤에만 부른다 — 그때 진행 중인 다른 줄은 죽은 프로세스의 것이다.
        """
        running = [s.value for s in PostStatus if not s.finished and s is not PostStatus.QUEUED]
        marks = ",".join("?" * len(running))
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT id FROM attempts WHERE id != ? AND status IN ({marks})",
                                (except_id, *running)).fetchall()
        return [row["id"] for row in rows if self.fail_stopped(row["id"], now, reason) is not None]

    def fail_stopped(self, attempt_id: int, now: datetime, reason: str) -> PostStatus | None:
        """끝나지 못하고 멈춘 줄(대기·진행 중) 하나를 실패로 끝낸다. 이미 끝난 줄이면 건드리지 않고 None.

        게시 버튼 기록(submitted_at)이 있으면 버튼 후 실패, 없으면 버튼 전 실패 — 이미 달렸을 수 있는 줄을 「전」으로 적지 않게.
        서버(로봇 프로세스가 끝났는데 줄이 안 끝났을 때)와 다음 로봇(fail_unfinished)이 함께 쓴다.
        """
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT submitted_at, status FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
            if row is None or PostStatus(row["status"]).finished:
                return None
            status = PostStatus.FAILED_AFTER_SUBMIT if row["submitted_at"] else PostStatus.FAILED_BEFORE_SUBMIT
            conn.execute("UPDATE attempts SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
                         (status.value, reason, _ts(now), attempt_id))
            return status

    def fail_queued(self, now: datetime, reason: str) -> list[int]:
        """남은 대기 줄(QUEUED)을 버튼 전 실패로 끝내고 그 번호들을 돌려준다.

        서버가 시작할 때 부른다 — 서버가 꺼지기 전에 띄우지 못한(또는 로봇이 잡지 못한) 시도가 「대기 중」에 영원히 남지 않게.
        진행 중(OPENING~) 줄은 건드리지 않는다 — 그것은 다음 게시 로봇이 정리한다(fail_unfinished).
        """
        with closing(self._connect()) as conn, conn:
            rows = conn.execute("SELECT id FROM attempts WHERE status = ?", (PostStatus.QUEUED.value,)).fetchall()
            conn.execute("UPDATE attempts SET status = ?, reason = ?, updated_at = ? WHERE status = ?",
                         (PostStatus.FAILED_BEFORE_SUBMIT.value, reason, _ts(now), PostStatus.QUEUED.value))
        return [row["id"] for row in rows]

    def history(self, limit: int = 100) -> list[Attempt]:
        """최근 것이 앞."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_attempt(r) for r in rows]

    def submitted_count(self, since: datetime, until: datetime) -> int:
        """[since, until) 구간에 게시 버튼을 누른 시도 수. 예외(테스트 영상) 줄도 센다(D-13·D-17)."""
        with closing(self._connect()) as conn:
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE submitted_at >= ? AND submitted_at < ?",
                (_ts(since), _ts(until)),
            ).fetchone()
        return int(count)

    def reset_quota(self, now: datetime) -> None:
        """하루 상한 초기화 시각을 남긴다. 게시 기록은 지우지 않는다 — 개발 모드인지는 부르는 쪽(서버)이 본다."""
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO quota_resets (reset_at) VALUES (?)", (_ts(now),))

    def last_quota_reset(self) -> datetime | None:
        """마지막 초기화 시각. 한 번도 안 했으면 None. 어느 구간을 셀지는 guard.quota_window가 정한다."""
        with closing(self._connect()) as conn:
            (latest,) = conn.execute("SELECT MAX(reset_at) FROM quota_resets").fetchone()
        return _dt(latest)

    def has_posted(self, video_id: str) -> bool:
        """예외가 아닌 게시 기록이 있나. dev_mode로 단 테스트 영상 게시는 나중에도 세지 않는다."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT 1 FROM attempts WHERE unique_video = ?", (video_id,)).fetchone()
        return row is not None

    # ── 초안 ──────────────────────────────────────────────────────────────────
    def save_draft(self, draft: Draft, now: datetime) -> None:
        """영상당 최신 초안 하나를 덮어쓴다. 문지기 불합격 이유(issues)도 함께 저장한다 — 이유가 있는 초안은 게시할 수 없다.

        approved 열은 옛 기록장 파일과 맞추려 남겨 둔 것이고 늘 0이다(승인 단계는 없앴다 — D-45).
        """
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO drafts (video_id, worry, comment, version, approved, issues, updated_at,
                       transcript_chars, comments_read)
                   VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
                   ON CONFLICT(video_id) DO UPDATE SET worry = excluded.worry, comment = excluded.comment,
                       version = excluded.version, approved = 0, issues = excluded.issues,
                       updated_at = excluded.updated_at, transcript_chars = excluded.transcript_chars,
                       comments_read = excluded.comments_read""",
                (draft.video_id, draft.worry, draft.comment, draft.version,
                 json.dumps(list(draft.issues), ensure_ascii=False), _ts(now),
                 draft.transcript_chars, draft.comments_read),
            )

    def get_draft(self, video_id: str) -> Draft | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE video_id = ?", (video_id,)).fetchone()
        if row is None:
            return None
        return Draft(video_id=row["video_id"], worry=row["worry"], comment=row["comment"],
                     version=row["version"],
                     issues=tuple(json.loads(row["issues"])),
                     transcript_chars=row["transcript_chars"], comments_read=row["comments_read"])

    def comparable_texts(self, exclude_video_id: str) -> list[str]:
        """「영상마다 다르게」 비교 대상: 다른 영상들의 게시한 댓글 + 규칙 검사를 통과한 최신 초안. 같은 영상의 글은 뺀다.

        불합격 초안은 넣지 않는다 — 게시될 수 없는 글 때문에 멀쩡한 초안이 「너무 비슷해요」로 막히지 않게
        (A 합격 → B가 A와 비슷해 불합격 → A를 게시하려니 B와 비슷하다고 막히는 꼬임).
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT text FROM attempts WHERE submitted_at IS NOT NULL AND video_id != ?
                   UNION ALL
                   SELECT comment FROM drafts WHERE video_id != ? AND issues = '[]'""",
                (exclude_video_id, exclude_video_id),
            ).fetchall()
        return [r[0] for r in rows]


def _attempt(row: sqlite3.Row) -> Attempt:
    video = VideoItem(id=row["video_id"], title=row["video_title"], channel_id=row["channel_id"],
                      channel_name=row["channel_name"], published_at=_dt(row["published_at"]),
                      thumbnail_url=row["thumbnail_url"], source=Source(row["source"]))
    return Attempt(
        id=row["id"], created_at=_dt(row["created_at"]), video=video, profile=row["profile"], text=row["text"],
        status=PostStatus(row["status"]), updated_at=_dt(row["updated_at"]), reason=row["reason"],
        screenshot=Path(row["screenshot"]) if row["screenshot"] else None, comment_url=row["comment_url"],
        retry_no=row["retry_no"], exempt=bool(row["exempt"]), submitted_at=_dt(row["submitted_at"]),
        root_id=row["root_id"], typed_chars=row["typed_chars"],
    )
