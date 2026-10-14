# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 원시 이벤트 행 만들기 시험 — 칸 꺼내기, service_id 채우기, 시각 변환
# ================================================================
import json

from poc.decode import decode_body
from poc.events import ERR_PAYLOAD, to_event_row, to_event_rows


def _click(role: str | None) -> dict:
    return {
        "event": "$ade_click",
        "properties": {
            "$insert_id": "i1",
            "distinct_id": "d1",
            "time": 1791433113.572,
            "application_id": "shop-web",
            "record_type": "behavior",
            "collection_grade": "guaranteed",
            "collection_source": "codegen",
            "verdict": "usable",
            "ade_declaration": {"kind": "object", "role": role},
        },
    }


def test_known_role_fills_service_id() -> None:
    row = to_event_row(0, _click("product"))
    assert (row.role, row.service_id) == ("product", "store")
    assert row.collection_source == "codegen"
    assert row.event_time is not None and row.event_time.year == 2026


def test_unknown_or_missing_role_leaves_service_id_empty() -> None:
    assert to_event_row(0, _click("banner")).service_id is None
    assert to_event_row(0, _click(None)).service_id is None


def test_outcome_without_declaration_has_no_service_id() -> None:
    outcome = {"event": "order_paid", "properties": {"collection_source": "direct"}}
    row = to_event_row(0, outcome)
    assert (row.event, row.role, row.service_id) == ("order_paid", None, None)


def test_payload_keeps_original_event() -> None:
    item = _click("product")
    assert json.loads(to_event_row(0, item).payload) == item


def test_non_object_item_is_kept_as_payload() -> None:
    row = to_event_row(3, "oops")
    assert (row.event_index, row.event, row.payload) == (3, None, '"oops"')


def test_millisecond_time_is_accepted() -> None:
    item = {"event": "e", "properties": {"time": 1791433113572}}
    assert to_event_row(0, item).event_time.year == 2026


def test_nan_payload_fails_whole_batch() -> None:
    rows, err = to_event_rows([{"event": "a"}, {"event": "b", "properties": {"x": float("nan")}}])
    assert (rows, err) == ([], ERR_PAYLOAD)


def test_decode_keeps_plus_in_base64() -> None:
    # 폼 파서처럼 + 를 공백으로 바꾸면 base64 가 깨진다
    import base64

    raw = json.dumps([{"event": "e", "properties": {"k": "~~~>>>"}}]).encode()
    b64 = base64.b64encode(raw).decode()
    assert "+" in b64 or "/" in b64
    d = decode_body(f"data={b64}".encode(), "application/x-www-form-urlencoded")
    assert d.error is None and d.events[0]["event"] == "e"
