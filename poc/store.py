# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 원시 DB 저장소. 수집 요청 원본과 풀어 낸 원시 이벤트, SDK 오류 신호를 넣는다
# ================================================================
#
# 테이블 이름과 컬럼 이름은 사내 DB 네이밍 컨벤션(hecto-db-naming)을 따른다. 정의는
# sql/schema.sql 에 있고 여기서는 만들지 않는다 — 수집 계정은 넣기 · 읽기 · 채번 권한만 가진다
# (UPDATE 도 없다). 그래서 감사 컬럼의 updated_ts 는 언제나 created_ts 와 같은 시각이다.
#
# 요청 원본과 원시 이벤트는 한 트랜잭션이다. 원본의 식별자를 먼저 채번하고, 원시를 저장점
# (savepoint) 안에서 넣은 뒤 원본을 넣는다. 원시가 값 때문에 거절되면 그 부분만 되돌리고 사유를
# 원본 행에 적는다 — 원시 때문에 원본을 잃지 않게 하려는 것이다. 원본까지 실패하면 전부 되돌리고
# 503 이다.
#
# 서버리스(Vercel)에서는 인스턴스가 요청마다 늘어날 수 있다. 인스턴스마다 연결을 많이 잡으면
# DB 연결이 바닥나므로 인스턴스 하나에 최대 두 개만 쓰고, Supabase 연결 풀러(transaction 모드)
# 를 거친다. 그 모드는 연결을 문장마다 바꿔 주므로 prepared statement 를 끈다.
import base64
import ipaddress
from dataclasses import dataclass

import psycopg
from psycopg_pool import ConnectionPool

from poc.events import EventRow

# 원시 이벤트를 넣다가 DB 가 값을 거절했을 때 (예: 칸 크기를 넘는 코드값)
ERR_EVENT_INSERT = "event_insert_failed"

# 감사 컬럼 created_by · updated_by 에 넣는 주체. 고객 요청을 처리한 시스템 이름이다
WRITER = "ade-poc-api"

# 이 저장소가 쓰는 테이블. /readyz 가 셋 다 닿는지 본다
TABLES = ("collection_request_history", "event_history", "diagnosis_history")

# 연결을 기다리는 최대 초. 넘으면 저장 실패로 보고 503 을 낸다
POOL_TIMEOUT_SEC = 5
POOL_MAX = 2

# 요청 헤더를 담는 칸의 크기. 분류어가 정한 크기다(url 2000 · name 200)
URL_SIZE = 2000
NAME_SIZE = 200
# 진단 칸의 크기 (code 20 · no 40)
CODE_SIZE = 20
NO_SIZE = 40


def ip_or_none(ip: str | None) -> str | None:
    """IP 주소면 그대로, 아니면 None. 프록시 헤더에 엉뚱한 값이 와도 칸에 남기지 않는다."""
    if not ip:
        return None
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


def fit(value: str | None, size: int) -> str | None:
    """칸 크기에 맞게 자른다.

    요청 원본은 SDK 가 다시 보낼 수 없는 값이 아니다 — 저장이 실패하면 503 이고 SDK 는 같은 배치를
    계속 다시 보낸다. 헤더 하나가 칸보다 길어서 그 요청이 영영 저장되지 않는 것보다, 그 헤더만
    잘리는 쪽이 낫다. 본문은 자르지 않는다.
    """
    if value is None:
        return None
    return value[:size]


@dataclass(frozen=True)
class RawRow:
    """수집 요청 원본 한 건. 요청 하나를 받은 그대로."""

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
    """SDK 오류 신호 한 건. 허용한 세 값과 받은 정보만."""

    received_at: str
    source_ip: str | None
    origin: str | None
    kind: str
    lib_version: str | None
    application_id: str | None


class PgStore:
    """Postgres 연결 풀로 원시를 넣는다. 스레드풀에서 불러도 된다."""

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
        """수집 요청 원본 한 행과 풀어 낸 원시 이벤트를 넣는다. 끝나야 200 을 줄 수 있다.

        Args:
            row: 요청 원본.
            events: 풀어 낸 원시 이벤트. 풀지 못했으면 빈 목록.
            events_error: 풀지 못한 사유. 풀었으면 None.

        Returns:
            새 원본 행의 `collection_request_id`.

        Raises:
            psycopg.Error: 원본 넣기 실패. 부른 쪽이 503 을 낸다.
        """
        with self._conn() as conn, conn.transaction():
            # 원시가 원본을 가리켜야 하므로 원본의 식별자를 먼저 받는다
            request_id = conn.execute(
                "SELECT nextval('ade.seq_collection_request_id')"
            ).fetchone()[0]
            if events:
                try:
                    with conn.transaction(), conn.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO ade.event_history (collection_request_id,
                                 received_ts, batch_seq, event_name, source_event_no,
                                 analytics_user_no, occurred_ts, application_no, build_no,
                                 record_type_code, collection_grade_code,
                                 collection_source_code, verdict_code, role_code,
                                 service_code, event_properties,
                                 created_ts, updated_ts, created_by, updated_by)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                       now(), now(), %s, %s)""",
                            [
                                (
                                    request_id,
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
                                    WRITER,
                                    WRITER,
                                )
                                for e in events
                            ],
                        )
                except psycopg.DataError:
                    # 값 때문에 거절됐다. 원시만 포기하고 원본은 넣는다. 연결 · 권한 문제는
                    # 여기서 잡지 않는다 — 원본도 실패할 것이므로 그대로 올려 503 을 낸다
                    events, events_error = [], ERR_EVENT_INSERT
            conn.execute(
                """INSERT INTO ade.collection_request_history (collection_request_id,
                     received_ts, server_name, endpoint_type_code, client_ip, origin_url,
                     referrer_url, user_agent_content, content_type_name, query_string_content,
                     request_body_content, body_byte_num, event_count, parse_error_type_code,
                     created_ts, updated_ts, created_by, updated_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now(), %s, %s)""",
                (
                    request_id,
                    row.received_at,
                    fit(self.server_id, NAME_SIZE),
                    row.route,
                    ip_or_none(row.source_ip),
                    fit(row.origin, URL_SIZE),
                    fit(row.referer, URL_SIZE),
                    row.user_agent,
                    fit(row.content_type, NAME_SIZE),
                    row.query,
                    # 바이트를 담는 분류어가 없어 base64 문자열로 담는다
                    base64.b64encode(row.body).decode("ascii"),
                    len(row.body),
                    len(events),
                    events_error,
                    WRITER,
                    WRITER,
                ),
            )
        return str(request_id)

    def store_diag(self, row: DiagRow) -> str:
        """오류 신호 하나를 `diagnosis_history` 에 넣는다.

        Raises:
            psycopg.Error: 넣기 실패. 부른 쪽이 503 을 낸다.
        """
        with self._conn() as conn:
            diagnosis_id = conn.execute(
                """INSERT INTO ade.diagnosis_history (received_ts, server_name, client_ip,
                     origin_url, diagnosis_type_code, library_version_code, application_no,
                     created_ts, updated_ts, created_by, updated_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s, now(), now(), %s, %s)
                   RETURNING diagnosis_id""",
                (
                    row.received_at,
                    fit(self.server_id, NAME_SIZE),
                    ip_or_none(row.source_ip),
                    fit(row.origin, URL_SIZE),
                    row.kind,
                    fit(row.lib_version, CODE_SIZE),
                    fit(row.application_id, NO_SIZE),
                    WRITER,
                    WRITER,
                ),
            ).fetchone()[0]
        return str(diagnosis_id)

    def ping(self) -> None:
        """원시 테이블 셋에 닿는지 본다. 행은 읽지 않는다.

        접속만 보면 스키마를 실행하지 않은 DB 에서도 통과한다. 그러면 배포 직후에는 준비됐다고
        나오고 수집은 전부 503 이 된다.

        Raises:
            psycopg.Error: 닿지 않거나 테이블 · 권한이 없으면.
        """
        with self._conn() as conn:
            for table in TABLES:
                conn.execute(f"SELECT 1 FROM ade.{table} LIMIT 0")
