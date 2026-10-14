# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 풀어 낸 이벤트를 ade_event 행으로 바꾼다. 자주 찾는 값만 칸으로 꺼내고 원형은 둔다
# ================================================================
#
# 값을 고치지 않는다. 칸으로 꺼내는 값은 원형(payload)에도 그대로 남는다.
# service_id 만 원본에 없는 값이다 — 이벤트 자기 선언의 role 을 어휘(poc/vocab.py)로 찾아 채운다.
# 직접 전달 이벤트(outcome)는 role 이 없어 service_id 가 비는 것이 정상이다(SDK 인계 계약 §9-4).
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from poc.vocab import service_for

# 이벤트 원형을 JSON 으로 다시 쓸 수 없을 때 (NaN · Infinity 같은 값)
ERR_PAYLOAD = "payload_not_json"

# time 이 이 값보다 크면 밀리초로 본다. 초 단위로는 서기 5138 년이다
_MS_THRESHOLD = 1e11


@dataclass(frozen=True)
class EventRow:
    """`ade_event` 한 행. 배치 안의 이벤트 하나."""

    event_index: int
    event: str | None
    insert_id: str | None
    distinct_id: str | None
    event_time: datetime | None
    application_id: str | None
    build_id: str | None
    record_type: str | None
    collection_grade: str | None
    collection_source: str | None
    verdict: str | None
    role: str | None
    service_id: str | None
    payload: str


def _str(v: Any) -> str | None:
    """문자열이면 그대로, 아니면 None. 숫자를 문자열로 바꿔 넣지 않는다."""
    return v if isinstance(v, str) else None


def _event_time(v: Any) -> datetime | None:
    """`properties.time` (초, 소수 가능) 을 시각으로. 숫자가 아니거나 범위 밖이면 None."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    seconds = v / 1000 if v > _MS_THRESHOLD else v
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _role(props: dict[str, Any]) -> str | None:
    """이벤트 자기 요소의 role. 조상의 role 은 보지 않는다."""
    decl = props.get("ade_declaration")
    return _str(decl.get("role")) if isinstance(decl, dict) else None


def to_event_row(index: int, item: Any) -> EventRow:
    """배치 안의 항목 하나를 행으로. 객체가 아니어도 원형은 남긴다.

    Raises:
        ValueError: 원형을 JSON 으로 다시 쓸 수 없을 때 (NaN · Infinity).
    """
    payload = json.dumps(item, ensure_ascii=False, allow_nan=False)
    obj = item if isinstance(item, dict) else {}
    props = obj.get("properties")
    props = props if isinstance(props, dict) else {}
    role = _role(props)
    return EventRow(
        event_index=index,
        event=_str(obj.get("event")),
        insert_id=_str(props.get("$insert_id")),
        distinct_id=_str(props.get("distinct_id")),
        event_time=_event_time(props.get("time")),
        application_id=_str(props.get("application_id")),
        build_id=_str(props.get("build_id")),
        record_type=_str(props.get("record_type")),
        collection_grade=_str(props.get("collection_grade")),
        collection_source=_str(props.get("collection_source")),
        verdict=_str(props.get("verdict")),
        role=role,
        service_id=service_for(role),
        payload=payload,
    )


def to_event_rows(items: list[Any]) -> tuple[list[EventRow], str | None]:
    """배치 전체를 행으로. 하나라도 원형을 쓸 수 없으면 전부 넣지 않는다.

    일부만 넣으면 그 요청에서 어떤 이벤트가 빠졌는지 원시만 보고는 알 수 없다. 원본(ade_raw)에는
    요청이 그대로 남으므로, 실패 사유만 남기고 다시 풀 수 있게 둔다.

    Returns:
        (행 목록, 실패 사유). 성공하면 사유는 None.
    """
    try:
        return [to_event_row(i, item) for i, item in enumerate(items)], None
    except ValueError:
        return [], ERR_PAYLOAD
