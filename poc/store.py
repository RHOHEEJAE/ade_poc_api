# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 원시 DB 저장소. 요청 원본(ade_raw)과 풀어 낸 원시 이벤트(ade_event)를 함께 넣는다
# ================================================================
#
# 넣기만 하고 저장 뒤에 고치지 않는다. 테이블 정의는 sql/schema.sql 에 있고 여기서는 만들지
# 않는다 — 수집 계정은 넣기 · 읽기 권한만 가진다(UPDATE 도 없다).
#
# 원본과 원시는 한 트랜잭션이다. 원시를 먼저 저장점(savepoint) 안에서 넣고, 실패하면 그 부분만
# 되돌린 뒤 실패 사유를 원본 행에 적어 넣는다. 원시 때문에 원본을 잃지 않게 하려는 것이다 —
# 원본이 남으면 언제든 다시 풀 수 있다. 원본까지 실패하면 전부 되돌리고 503 이다.
#
# 서버리스(Vercel)에서는 인스턴스가 요청마다 늘어날 수 있다. 인스턴스마다 연결을 많이 잡으면
# DB 연결이 바닥나므로 인스턴스 하나에 최대 두 개만 쓰고, Supabase 연결 풀러(transaction 모드)
# 를 거친다. 그 모드는 연결을 문장마다 바꿔 주므로 prepared statement 를 끈다.
import ipaddress
import secrets
import time
import uuid
from dataclasses import dataclass

import psycopg
from psycopg_pool import ConnectionPool

from poc.events import EventRow

# 원시 이벤트를 넣다가 DB 가 거절했을 때 (예: 문자열 안의 \u0000 은 jsonb 가 받지 않는다)
ERR_EVENT_INSERT = "event_insert_failed"

# 연결을 기다리는 최대 초. 넘으면 저장 실패로 보고 503 을 낸다
POOL_TIMEOUT_SEC = 5
POOL_MAX = 2

_MS_BITS = 48
_RAND_A_BITS = 12
_RAND_B_BITS = 62


def uuid7(now_ms: int | None = None) -> uuid.UUID:
    """UUIDv7 을 만든다. 앞 48비트가 밀리초 시각이라 만든 순서대로 정렬된다.

    Python 3.11 · 3.12 에는 `uuid.uuid7` 이 없어서 RFC 9562 의 배치대로 직접 짠다.
    """
    ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    value = (ms & ((1 << _MS_BITS) - 1)) << 80
    value |= 0x7 << 76  # 버전
    value |= secrets.randbits(_RAND_A_BITS) << 64
    value |= 0b10 << 62  # 변형(RFC 4122)
    value |= secrets.randbits(_RAND_B_BITS)
    return uuid.UUID(int=value)


def inet_or_none(ip: str | None) -> str | None:
    """`inet` 칸에 넣을 수 있는 IP 면 그대로, 아니면 None.

    IP 가 아닌 값을 넣으면 저장 자체가 실패한다. IP 칸 하나 때문에 요청을 잃지 않게 비운다.
    """
    if not ip:
        return None
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


@dataclass(frozen=True)
class RawRow:
    """`ade_raw` 한 행. 요청 하나를 받은 그대로."""

    received_at: str
    route: str
    source_ip: str | None
    origin: str | None
    referer: str | None
    user_agent: str | None
    content_type: str | None
    query: str | None
    body: bytes


@dataclass(frozen=True)
class DiagRow:
    """`ade_diag` 한 행. SDK 오류 신호에서 허용한 세 값과 받은 정보만."""

    received_at: str
    source_ip: str | None
    origin: str | None
    kind: str
    lib_version: str | None
    application_id: str | None


class PgStore:
    """Postgres 연결 풀로 원본을 넣는다. 스레드풀에서 불러도 된다."""

    def __init__(self, url: str, server_id: str) -> None:
        self.server_id = server_id
        # 첫 요청 때 연다. 모듈을 읽는 순간(콜드 스타트) DB 가 멈춰 있어도 앱은 뜬다
        self._pool = ConnectionPool(
            url,
            min_size=1,
            max_size=POOL_MAX,
            timeout=POOL_TIMEOUT_SEC,
            open=False,
            kwargs={"prepare_threshold": None, "autocommit": True},
            # 풀러가 쉬는 연결을 끊어 둘 수 있다. 꺼내기 전에 살아 있는지 본다
            check=ConnectionPool.check_connection,
        )
        self._opened = False

    def _conn(self):
        if not self._opened:
            self._pool.open()
            self._opened = True
        return self._pool.connection()

    def store_raw(self, row: RawRow, events: list[EventRow], events_error: str | None) -> str:
        """요청 원본 한 행과 풀어 낸 원시 이벤트를 넣는다. 끝나야 200 을 줄 수 있다.

        Args:
            row: 요청 원본.
            events: 풀어 낸 원시 이벤트. 풀지 못했으면 빈 목록.
            events_error: 풀지 못한 사유. 풀었으면 None.

        Returns:
            새 원본 행의 `raw_id`.

        Raises:
            psycopg.Error: 원본 넣기 실패. 부른 쪽이 503 을 낸다.
        """
        raw_id = uuid7()
        with self._conn() as conn, conn.transaction():
            if events:
                try:
                    with conn.transaction(), conn.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO ade.ade_event (event_id, raw_id, received_at,
                                 event_index, event, insert_id, distinct_id, event_time,
                                 application_id, build_id, record_type, collection_grade,
                                 collection_source, verdict, role, service_id, payload)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [
                                (
                                    uuid7(),
                                    raw_id,
                                    row.received_at,
                                    e.event_index,
                                    e.event,
                                    e.insert_id,
                                    e.distinct_id,
                                    e.event_time,
                                    e.application_id,
                                    e.build_id,
                                    e.record_type,
                                    e.collection_grade,
                                    e.collection_source,
                                    e.verdict,
                                    e.role,
                                    e.service_id,
                                    e.payload,
                                )
                                for e in events
                            ],
                        )
                except psycopg.DataError:
                    # 값 때문에 거절됐다. 원시만 포기하고 원본은 넣는다. 연결 · 권한 문제는
                    # 여기서 잡지 않는다 — 원본도 실패할 것이므로 그대로 올려 503 을 낸다
                    events, events_error = [], ERR_EVENT_INSERT
            conn.execute(
                """INSERT INTO ade.ade_raw (raw_id, received_at, server_id, route, source_ip,
                     origin, referer, user_agent, content_type, query, body, body_bytes,
                     event_count, events_error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    raw_id,
                    row.received_at,
                    self.server_id,
                    row.route,
                    inet_or_none(row.source_ip),
                    row.origin,
                    row.referer,
                    row.user_agent,
                    row.content_type,
                    row.query,
                    row.body,
                    len(row.body),
                    len(events),
                    events_error,
                ),
            )
        return str(raw_id)

    def store_diag(self, row: DiagRow) -> str:
        """오류 신호 하나를 `ade_diag` 에 넣는다.

        Raises:
            psycopg.Error: 넣기 실패. 부른 쪽이 503 을 낸다.
        """
        diag_id = uuid7()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO ade.ade_diag (diag_id, received_at, server_id, source_ip, origin,
                     kind, lib_version, application_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    diag_id,
                    row.received_at,
                    self.server_id,
                    inet_or_none(row.source_ip),
                    row.origin,
                    row.kind,
                    row.lib_version,
                    row.application_id,
                ),
            )
        return str(diag_id)

    def ping(self) -> None:
        """저장소에 닿는지 본다. 쓰지 않는다.

        Raises:
            psycopg.Error: 닿지 않으면.
        """
        with self._conn() as conn:
            conn.execute("SELECT 1")
