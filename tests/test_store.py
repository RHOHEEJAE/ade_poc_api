# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 저장소의 순수 함수 시험 — 행 번호와 IP 칸
# ================================================================
import uuid

from poc.store import inet_or_none, uuid7


def test_uuid7_has_version_and_variant() -> None:
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_sorts_by_time() -> None:
    assert uuid7(now_ms=1_000) < uuid7(now_ms=2_000)


def test_uuid7_is_unique_within_same_ms() -> None:
    assert len({uuid7(now_ms=1_000) for _ in range(1000)}) == 1000


def test_inet_keeps_ip_and_drops_non_ip() -> None:
    assert inet_or_none("211.234.10.5") == "211.234.10.5"
    assert inet_or_none("2001:db8::1") == "2001:db8::1"
    assert inet_or_none("testclient") is None
    assert inet_or_none(None) is None
