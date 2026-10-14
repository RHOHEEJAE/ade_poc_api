# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 환경 변수에서 읽는 설정
# ================================================================
import os
import socket
from dataclasses import dataclass

# 본문 상한 기본값. 넘으면 413 을 주고, SDK 가 배치를 반으로 나눠 다시 보낸다
DEFAULT_BODY_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    """앱 설정. 시험은 값을 직접 넣어 만든다."""

    db_url: str = ""
    origins: frozenset[str] = frozenset()
    body_limit: int = DEFAULT_BODY_LIMIT
    trust_proxy_hops: int = 1
    server_id: str = "local"


def _server_id() -> str:
    """요청을 받은 인스턴스 이름. Vercel 이면 리전과 배포 ID, 아니면 호스트 이름."""
    if os.environ.get("ADE_SERVER_ID"):
        return os.environ["ADE_SERVER_ID"]
    region = os.environ.get("VERCEL_REGION")
    deployment = os.environ.get("VERCEL_DEPLOYMENT_ID")
    if region or deployment:
        return "-".join(p for p in (region, deployment) if p)
    return socket.gethostname()


def load_settings() -> Settings:
    """환경 변수를 읽어 설정을 만든다.

    - `ADE_DB_URL`: 원시 DB 접속 주소. Supabase 면 연결 풀러(transaction 모드, 6543) 주소
    - `ADE_ORIGINS`: CORS 를 허용할 출처 (쉼표 구분). 비면 localhost 만
    - `ADE_BODY_LIMIT`: 본문 상한 바이트
    - `ADE_TRUST_PROXY_HOPS`: `X-Forwarded-For` 를 믿을 프록시 단계 수
    - `ADE_SERVER_ID`: 인스턴스 이름을 직접 줄 때
    """
    origins = os.environ.get("ADE_ORIGINS", "")
    return Settings(
        db_url=os.environ.get("ADE_DB_URL", ""),
        origins=frozenset(o.strip() for o in origins.split(",") if o.strip()),
        body_limit=int(os.environ.get("ADE_BODY_LIMIT", DEFAULT_BODY_LIMIT)),
        trust_proxy_hops=int(os.environ.get("ADE_TRUST_PROXY_HOPS", "1")),
        server_id=_server_id(),
    )
