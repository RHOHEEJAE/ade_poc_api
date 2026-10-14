# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 수집 경로 시험. 진짜 DB 대신 받은 행을 모으는 가짜 저장소를 쓴다
# ================================================================
import psycopg
from fastapi.testclient import TestClient

from poc.config import Settings
from poc.events import EventRow
from poc.main import create_app
from poc.store import DiagRow, RawRow

FORM = "application/x-www-form-urlencoded"
BODY = b"data=%5B%7B%22event%22%3A%22%24ade_click%22%7D%5D"
ORIGIN = "https://shop.example.com"


class FakeStore:
    """`PgStore` 와 같은 모양. 넣은 행을 모으고, `fail` 이면 저장소 오류를 낸다."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.raw: list[RawRow] = []
        self.events: list[EventRow] = []
        self.errors: list[str | None] = []
        self.diag: list[DiagRow] = []

    def store_raw(self, row: RawRow, events: list[EventRow], events_error: str | None) -> str:
        if self.fail:
            raise psycopg.OperationalError("down")
        self.raw.append(row)
        self.events.extend(events)
        self.errors.append(events_error)
        return "id"

    def store_diag(self, row: DiagRow) -> str:
        if self.fail:
            raise psycopg.OperationalError("down")
        self.diag.append(row)
        return "id"

    def ping(self) -> None:
        if self.fail:
            raise psycopg.OperationalError("down")


def _client(store: FakeStore | None, **kw) -> TestClient:
    settings = Settings(origins=frozenset({ORIGIN}), **kw)
    return TestClient(create_app(settings, store))


def test_track_stores_request_as_received() -> None:
    store = FakeStore()
    r = _client(store).post(
        "/track?ip=0&_=123",
        content=BODY,
        headers={
            "content-type": FORM,
            "origin": ORIGIN,
            "referer": f"{ORIGIN}/a",
            "user-agent": "ua",
            "x-forwarded-for": "211.234.10.5",
        },
    )
    assert r.status_code == 200 and r.text == "1"
    assert r.headers["access-control-allow-origin"] == ORIGIN
    [row] = store.raw
    assert row.route == "track"
    assert row.body == BODY
    assert row.content_type == FORM
    assert row.query == "ip=0&_=123"
    assert row.referer == f"{ORIGIN}/a"
    assert row.source_ip == "211.234.10.5"


def test_verbose_returns_json() -> None:
    r = _client(FakeStore()).post("/track/?verbose=1", content=BODY)
    assert r.json() == {"status": 1, "error": None}


def test_engage_and_groups_keep_their_route() -> None:
    store = FakeStore()
    c = _client(store)
    c.post("/engage", content=b"data=e30=")
    c.post("/groups/", content=b"data=e30=")
    assert [r.route for r in store.raw] == ["engage", "groups"]


def test_broken_body_is_stored_with_200() -> None:
    store = FakeStore()
    assert _client(store).post("/track", content=b"\xff\xfe garbage").status_code == 200
    assert store.raw[0].body == b"\xff\xfe garbage"


def test_unknown_origin_is_stored_without_cors_headers() -> None:
    store = FakeStore()
    r = _client(store).post("/track", content=BODY, headers={"origin": "https://other.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
    assert len(store.raw) == 1


def test_too_large_is_413_and_not_stored() -> None:
    store = FakeStore()
    r = _client(store, body_limit=10).post("/track", content=b"x" * 11)
    assert r.status_code == 413
    assert store.raw == []


def test_store_failure_is_503() -> None:
    assert _client(FakeStore(fail=True)).post("/track", content=BODY).status_code == 503


def test_no_db_configured_is_503() -> None:
    assert _client(None).post("/track", content=BODY).status_code == 503


def test_preflight_allows_registered_origin_only() -> None:
    c = _client(FakeStore())
    ok = c.options("/track", headers={"origin": ORIGIN})
    assert ok.status_code == 204
    assert ok.headers["access-control-allow-methods"] == "POST, OPTIONS"
    other = c.options("/track", headers={"origin": "https://other.example"})
    assert "access-control-allow-origin" not in other.headers


def test_diag_keeps_three_fields_only() -> None:
    store = FakeStore()
    r = _client(store).post(
        "/diag",
        json={"kind": "transmission_failure", "lib_version": "2.82.1", "secret": "x"},
    )
    assert r.status_code == 200
    [row] = store.diag
    assert (row.kind, row.lib_version, row.application_id) == (
        "transmission_failure",
        "2.82.1",
        None,
    )


def test_diag_unreadable_or_too_large_is_unparsable_200() -> None:
    store = FakeStore()
    c = _client(store, body_limit=10)
    assert c.post("/diag", content=b"not json").status_code == 200
    assert c.post("/diag", content=b"x" * 11).status_code == 200
    assert [r.kind for r in store.diag] == ["unparsable", "unparsable"]


def test_stub_routes_are_404() -> None:
    assert _client(FakeStore()).get("/flags").status_code == 404


def test_health() -> None:
    assert _client(FakeStore()).get("/livez").status_code == 200
    assert _client(FakeStore()).get("/readyz").status_code == 200
    assert _client(FakeStore(fail=True)).get("/readyz").status_code == 503
    assert _client(None).get("/readyz").status_code == 503


CLICK_BATCH = (
    b"data=%5B"
    b"%7B%22event%22%3A%22%24ade_click%22%2C%22properties%22%3A%7B%22%24insert_id%22%3A%22i1%22%2C"
    b"%22application_id%22%3A%22shop-web%22%2C%22ade_declaration%22%3A%7B%22role%22%3A%22product%22%7D%7D%7D"
    b"%2C%7B%22event%22%3A%22%24ade_web_page_view%22%2C%22properties%22%3A%7B%22%24insert_id%22%3A%22i2%22%7D%7D"
    b"%5D"
)


def test_track_batch_is_exploded_into_events_with_service_id() -> None:
    store = FakeStore()
    r = _client(store).post("/track", content=CLICK_BATCH, headers={"content-type": FORM})
    assert r.status_code == 200
    assert len(store.raw) == 1 and store.errors == [None]
    click, view = store.events
    assert (click.event, click.insert_id, click.role, click.service_id) == (
        "$ade_click",
        "i1",
        "product",
        "store",
    )
    assert (view.event_index, view.role, view.service_id) == (1, None, None)


def test_undecodable_track_keeps_raw_and_records_reason() -> None:
    store = FakeStore()
    r = _client(store).post("/track", content=b"nope", headers={"content-type": FORM})
    assert r.status_code == 200
    assert len(store.raw) == 1 and store.events == []
    assert store.errors == ["no_data_param"]


def test_engage_is_not_exploded() -> None:
    store = FakeStore()
    _client(store).post("/engage", content=b"data=%5B%7B%22%24set%22%3A%7B%7D%7D%5D")
    assert len(store.raw) == 1 and store.events == [] and store.errors == [None]
