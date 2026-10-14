# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 요청에서 서버만 알 수 있는 정보를 읽는 도구 — 받은 시각, 클라이언트 IP, CORS, 본문
# ================================================================
import re
from datetime import UTC, datetime

from fastapi import Request

# 허용 목록이 비었을 때만 쓰는 개발용 출처
_LOCALHOST = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")

# 브라우저가 preflight 결과를 캐시하는 시간(초)
PREFLIGHT_MAX_AGE_SEC = 600


def now_iso() -> str:
    """지금 시각을 UTC ISO 8601 문자열로. 브라우저 시계가 아니라 서버가 받은 시각이다."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def client_ip(request: Request, trust_hops: int) -> str | None:
    """요청을 보낸 클라이언트 IP.

    `X-Forwarded-For` 의 맨 왼쪽 값은 클라이언트가 위조할 수 있다. 프록시는 받은 주소를
    오른쪽에 덧붙이므로, 앞에 있는 프록시 수(`trust_hops`)만큼만 오른쪽에서 거슬러 올라간다.
    항목이 그보다 적으면 헤더를 버리고 소켓 주소를 쓴다.
    """
    sock = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if trust_hops <= 0 or not forwarded:
        return sock
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    idx = len(hops) - trust_hops
    return hops[idx] if 0 <= idx < len(hops) else sock


def is_allowed_origin(origin: str | None, allowed: frozenset[str]) -> bool:
    """이 출처에 CORS 헤더를 붙여도 되는지. 등록 목록이 비면 localhost 만 허용한다."""
    if not origin:
        return False
    if allowed:
        return origin in allowed
    return bool(_LOCALHOST.match(origin))


def cors_headers(request: Request, allowed: frozenset[str]) -> dict[str, str]:
    """등록된 출처면 CORS 헤더, 아니면 빈 dict.

    벤더 SDK 가 자격 증명을 포함해 보내므로 `*` 대신 요청한 출처를 그대로 돌려준다.
    등록되지 않은 출처도 저장은 한다 — 헤더만 비운다.
    """
    origin = request.headers.get("origin")
    if not is_allowed_origin(origin, allowed):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
        "Access-Control-Expose-Headers": "Retry-After",
    }


def preflight_headers(request: Request, allowed: frozenset[str]) -> dict[str, str]:
    """OPTIONS 응답 헤더. 기본 CORS 헤더에 메서드 · 헤더 · 캐시 시간을 더한다."""
    base = cors_headers(request, allowed)
    if not base:
        return {}
    return {
        **base,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": str(PREFLIGHT_MAX_AGE_SEC),
    }


async def read_body_capped(request: Request, limit: int) -> bytes | None:
    """본문을 상한까지만 읽는다. 넘으면 그 자리에서 멈추고 None.

    길이 헤더가 없는 청크 전송도 있으므로, 헤더만 믿지 않고 읽으면서 잰다.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)
