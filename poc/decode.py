# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-09-18 (collector app/services/decode.py 에서 그대로 옮김, 2026-10-14)
# Desc    : 벤더 SDK 가 보낸 요청 본문을 이벤트 목록으로 푼다
# ================================================================
#
# 클라이언트가 벤더 SDK 라서 요청 형식을 우리가 정할 수 없다. 실제로 오는 세 가지를 모두 받는다.
#   XHR 전송    application/x-www-form-urlencoded   data=<퍼센트 인코딩>
#   비콘 전송    text/plain                          같은 data= 문자열
#   직접 전송    application/json                    본문이 곧 이벤트
# data= 값은 SDK 설정에 따라 JSON 그대로이거나 base64 로 감싼 JSON 이다.
import base64
import json
from dataclasses import dataclass, field
from typing import Any

# 디코드 실패 사유. 격리 행의 reason 으로 그대로 쓴다
ERR_EMPTY = "empty_body"
ERR_NO_DATA = "no_data_param"
ERR_JSON = "json_parse"
ERR_BASE64 = "base64"

# 전송 방식. Content-Type 으로 가른다
TRANSPORT_XHR = "xhr"
TRANSPORT_BEACON = "beacon"
TRANSPORT_JSON = "json"

_DATA_KEY = b"data="
_PERCENT = 0x25
_AMP = b"&"

# 16진수 판별에 쓰는 ASCII 코드
_ASCII_0 = ord("0")
_ASCII_9 = ord("9")
_ASCII_A = ord("A")
_ASCII_F = ord("F")
_ASCII_a = ord("a")
_ASCII_f = ord("f")


@dataclass
class Decoded:
    """디코드 결과. 실패해도 예외가 아니라 `error` 에 사유 코드를 담아 돌려준다."""

    events: list[Any] = field(default_factory=list)
    transport: str = TRANSPORT_XHR
    payload_format: str = "none"
    error: str | None = None


def _hexval(b: int) -> int:
    """16진수 한 글자의 ASCII 코드를 0~15 로 바꾼다. 16진수가 아니면 -1."""
    if _ASCII_0 <= b <= _ASCII_9:
        return b - _ASCII_0
    if _ASCII_A <= b <= _ASCII_F:
        return b - (_ASCII_A - 10)
    if _ASCII_a <= b <= _ASCII_f:
        return b - (_ASCII_a - 10)
    return -1


def percent_decode(buf: bytes) -> str:
    """`%XX` 만 바이트로 되돌리고 UTF-8 로 읽는다. 깨진 바이트는 U+FFFD 로 남긴다.

    표준 폼 파서(`urllib.parse.parse_qs` · `unquote_plus`)를 쓰면 안 된다. 그것들은 `+` 를
    공백으로 바꾸는데, base64 로 감싼 본문에는 `+` 가 그대로 들어 있어서 공백이 되면 디코드가
    깨진다. `urllib.parse.unquote` 는 `+` 를 건드리지 않아 결과가 같고 속도도 비슷하다
    (1.4MB 본문에서 두 방식 모두 약 78ms). 여기서 직접 푸는 것은 본문이 이미 바이트로 있고
    `_extract_data` 가 자른 구간을 그대로 이어받기 때문이다.

    Args:
        buf: 퍼센트 인코딩된 바이트열.

    Returns:
        디코드한 문자열.
    """
    out = bytearray()
    i, n = 0, len(buf)
    while i < n:
        b = buf[i]
        if b == _PERCENT and i + 2 < n:
            hi, lo = _hexval(buf[i + 1]), _hexval(buf[i + 2])
            if hi >= 0 and lo >= 0:
                out.append((hi << 4) | lo)
                i += 3
                continue
        out.append(b)
        i += 1
    return out.decode("utf-8", errors="replace")


def _extract_data(body: bytes) -> bytes | None:
    """본문에서 `data=` 값 부분만 잘라낸다. 없으면 None.

    `data=` 는 본문 맨 앞이거나 `&` 바로 뒤에 있어야 한다. 값 안에 들어 있는 `data=` 를
    파라미터로 잘못 잡지 않기 위해서다.
    """
    start = 0
    while True:
        idx = body.find(_DATA_KEY, start)
        if idx < 0:
            return None
        if idx == 0 or body[idx - 1 : idx] == _AMP:
            begin = idx + len(_DATA_KEY)
            end = body.find(_AMP, begin)
            return body[begin:] if end < 0 else body[begin:end]
        start = idx + 1


def _as_list(parsed: Any) -> list[Any]:
    """배치는 배열, 단건은 객체로 오므로 항상 목록으로 맞춘다."""
    return parsed if isinstance(parsed, list) else [parsed]


def _parse_json_or_base64(text: str) -> tuple[list[Any], str, str | None]:
    """JSON 으로 먼저 읽고, 안 되면 base64 로 풀어 다시 읽는다.

    Returns:
        (이벤트 목록, 본문 형식, 실패 사유). 성공하면 사유는 None.
    """
    try:
        return _as_list(json.loads(text)), "json", None
    except (ValueError, RecursionError):
        # 깊게 중첩된 JSON 은 ValueError 가 아니라 RecursionError 로 온다
        pass
    try:
        decoded = base64.b64decode(text, validate=True)
    except ValueError:
        return [], "none", ERR_JSON
    try:
        return _as_list(json.loads(decoded.decode("utf-8", errors="replace"))), "base64", None
    except (ValueError, RecursionError):
        return [], "base64", ERR_BASE64


def decode_body(body: bytes, content_type: str | None) -> Decoded:
    """요청 본문을 이벤트 목록으로 푼다. 어떤 입력에도 예외를 던지지 않는다.

    Args:
        body: 요청 본문 바이트열.
        content_type: `Content-Type` 헤더. 전송 방식 판별에만 쓴다.

    Returns:
        이벤트 목록과 전송 방식, 본문 형식, 실패 사유.
    """
    ct = (content_type or "").lower()
    if ct.startswith("application/json"):
        d = Decoded(transport=TRANSPORT_JSON)
        if not body:
            d.error = ERR_EMPTY
            return d
        try:
            d.events = _as_list(json.loads(body.decode("utf-8", errors="replace")))
            d.payload_format = "json"
        except (ValueError, RecursionError):
            d.error = ERR_JSON
        return d

    # 비콘 전송은 헤더를 붙일 수 없어 text/plain 으로 온다
    transport = TRANSPORT_BEACON if ct.startswith("text/plain") else TRANSPORT_XHR
    d = Decoded(transport=transport)
    if not body:
        d.error = ERR_EMPTY
        return d
    data = _extract_data(body)
    if data is None:
        d.error = ERR_NO_DATA
        return d
    d.events, d.payload_format, d.error = _parse_json_or_base64(percent_decode(data))
    return d
