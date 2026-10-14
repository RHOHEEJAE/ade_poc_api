# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : PoC 수집 API. 요청 원본과 풀어 낸 원시 이벤트를 원시 DB 에 넣고, 200 · 413 · 503 만 준다
# ================================================================
#
# 상태 코드를 좁게 쓰는 이유는 벤더 SDK 의 배치 처리 때문이다.
#   200  받았다. 본문이 깨졌어도 원문을 남기고 200 을 준다. SDK 는 배치를 지운다
#   413  본문이 너무 크다. SDK 가 배치를 반으로 줄여 다시 보낸다
#   503  우리 쪽 실패다. SDK 가 다시 보낸다
# 그 밖의 4xx 를 주면 SDK 가 배치를 지워 영구 손실이 된다.
#
# PoC 범위: 요청 원본(ade_raw)을 남기고, /track 본문을 이벤트 단위로 풀어 원시(ade_event)에 넣는다.
# 원시에는 어휘로 찾은 service_id 를 채운다. 토큰 확인 · 중복 제거 · 속도 제한은 하지 않는다.
import json
from collections.abc import Awaitable, Callable

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger

from poc.config import Settings, load_settings
from poc.decode import decode_body
from poc.events import EventRow, to_event_rows
from poc.store import DiagRow, PgStore, RawRow
from poc.web import (
    client_ip,
    cors_headers,
    now_iso,
    preflight_headers,
    read_body_capped,
)

COLLECT_ROUTES = ("track", "engage", "groups")
# 벤더가 부를 수 있지만 쓰지 않는 기능. 404 를 받으면 SDK 가 그 기능을 건너뛴다
STUB_ROUTES = ("settings", "record", "flags")

DIAG_UNPARSABLE = "unparsable"

# 저장 실패로 보고 503 을 낼 예외
STORE_ERRORS = (psycopg.Error, RuntimeError, OSError)


def _ok(request: Request, headers: dict[str, str]) -> Response:
    """성공 응답. 벤더 규약대로 `verbose=1` 이면 JSON, 아니면 본문 `1`."""
    if request.query_params.get("verbose") == "1":
        return JSONResponse({"status": 1, "error": None}, headers=headers)
    return PlainTextResponse("1", headers=headers)


def _error(status: int, message: str, headers: dict[str, str]) -> Response:
    return JSONResponse({"status": 0, "error": message}, status_code=status, headers=headers)


def _explode(
    route: str, body: bytes, content_type: str | None
) -> tuple[list[EventRow], str | None]:
    """본문을 원시 이벤트 행으로 푼다. `/track` 만 푼다.

    `/engage` · `/groups` 는 이벤트가 아니라 프로필 갱신이라 원본(ade_raw)에만 남긴다.
    풀지 못해도 예외를 던지지 않는다 — 사유를 돌려주고 원본은 그대로 저장한다.
    """
    if route != "track":
        return [], None
    decoded = decode_body(body, content_type)
    if decoded.error:
        return [], decoded.error
    return to_event_rows(decoded.events)


def _pick_diag(body: bytes | None) -> tuple[str, str | None, str | None]:
    """진단 본문에서 허용한 세 값만 꺼낸다. 읽을 수 없으면 종류를 `unparsable` 로."""
    if body is None:
        return DIAG_UNPARSABLE, None, None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return DIAG_UNPARSABLE, None, None
    if not isinstance(data, dict) or not isinstance(data.get("kind"), str):
        return DIAG_UNPARSABLE, None, None
    lib = data.get("lib_version")
    app_id = data.get("application_id")
    return (
        data["kind"],
        lib if isinstance(lib, str) else None,
        app_id if isinstance(app_id, str) else None,
    )


def create_app(settings: Settings, store: PgStore | None) -> FastAPI:
    """설정과 저장소를 받아 앱을 만든다. 시험은 가짜 저장소를 넘긴다.

    저장소가 None 이면(접속 주소가 없으면) 수집 경로는 모두 503 이다.
    """
    app = FastAPI(title="ADE PoC 수집 API", docs_url="/docs", redoc_url=None)

    def make_collect(route: str) -> Callable[[Request], Awaitable[Response]]:
        async def collect(request: Request) -> Response:
            headers = cors_headers(request, settings.origins)
            body = await read_body_capped(request, settings.body_limit)
            if body is None:
                return _error(413, "payload too large", headers)
            if store is None:
                return _error(503, "temporarily unavailable", headers)
            row = RawRow(
                received_at=now_iso(),
                route=route,
                source_ip=client_ip(request, settings.trust_proxy_hops),
                origin=request.headers.get("origin"),
                referer=request.headers.get("referer"),
                user_agent=request.headers.get("user-agent"),
                content_type=request.headers.get("content-type"),
                # 받은 문자열 그대로. dict 로 바꾸면 같은 키가 여러 번 올 때 값이 사라진다
                query=request.url.query or None,
                body=body,
            )
            events, events_error = await run_in_threadpool(_explode, route, body, row.content_type)
            try:
                # 200 전에 저장을 끝낸다. 서버리스는 응답 뒤 작업이 끝까지 돈다는 보장이 없다
                await run_in_threadpool(store.store_raw, row, events, events_error)
            except STORE_ERRORS as e:
                logger.error("ade_raw 적재 실패: {}", type(e).__name__)
                return _error(503, "temporarily unavailable", headers)
            return _ok(request, headers)

        return collect

    async def preflight(request: Request) -> Response:
        return Response(status_code=204, headers=preflight_headers(request, settings.origins))

    for route in COLLECT_ROUTES:
        # 끝 슬래시가 있든 없든 받는다. 하나만 받으면 307 로 돌려보내는데, 따라가지 않는 SDK 가 있다
        for path in (f"/{route}", f"/{route}/"):
            app.add_api_route(path, make_collect(route), methods=["POST"])
            app.add_api_route(path, preflight, methods=["OPTIONS"], include_in_schema=False)

    async def diag(request: Request) -> Response:
        headers = cors_headers(request, settings.origins)
        body = await read_body_capped(request, settings.body_limit)
        if store is None:
            return _error(503, "temporarily unavailable", headers)
        # 상한을 넘어도 413 을 주지 않는다. 진단 때문에 SDK 가 재전송을 반복하지 않게 한다
        kind, lib, app_id = _pick_diag(body)
        row = DiagRow(
            received_at=now_iso(),
            source_ip=client_ip(request, settings.trust_proxy_hops),
            origin=request.headers.get("origin"),
            kind=kind,
            lib_version=lib,
            application_id=app_id,
        )
        try:
            await run_in_threadpool(store.store_diag, row)
        except STORE_ERRORS as e:
            logger.error("ade_diag 적재 실패: {}", type(e).__name__)
            return _error(503, "temporarily unavailable", headers)
        return _ok(request, headers)

    for path in ("/diag", "/diag/"):
        app.add_api_route(path, diag, methods=["POST"])
        app.add_api_route(path, preflight, methods=["OPTIONS"], include_in_schema=False)

    async def stub() -> Response:
        return Response(status_code=404)

    for name in STUB_ROUTES:
        for path in (f"/{name}", f"/{name}/"):
            app.add_api_route(path, stub, methods=["GET", "POST"], include_in_schema=False)

    @app.get("/livez")
    async def livez() -> dict[str, str]:
        """프로세스가 응답하는지. DB 는 보지 않는다."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> Response:
        """원시 DB 에 닿는지. 쓰지는 않는다."""
        if store is None:
            return _error(503, "db not configured", {})
        try:
            await run_in_threadpool(store.ping)
        except STORE_ERRORS as e:
            logger.error("readyz 실패: {}", type(e).__name__)
            return _error(503, "db unreachable", {})
        return JSONResponse({"status": "ok"})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> Response:
        """잡지 못한 예외는 503. 4xx 를 내면 SDK 가 배치를 버린다. 예외 종류만 남긴다."""
        logger.error("처리하지 못한 예외: {}", type(exc).__name__)
        return _error(503, "temporarily unavailable", cors_headers(request, settings.origins))

    return app


def build_app() -> FastAPI:
    """환경 변수로 앱을 만든다. Vercel · 로컬 실행이 이 함수를 쓴다."""
    settings = load_settings()
    store = PgStore(settings.db_url, settings.server_id) if settings.db_url else None
    if store is None:
        logger.warning("ADE_DB_URL 이 없다. 수집 경로는 503 을 준다")
    return create_app(settings, store)
