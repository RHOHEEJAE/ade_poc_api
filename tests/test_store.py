# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : 저장소의 순수 함수 시험 — IP 칸과 칸 크기 맞추기
# ================================================================
from poc.store import fit, ip_or_none


def test_ip_keeps_ip_and_drops_non_ip() -> None:
    assert ip_or_none("211.234.10.5") == "211.234.10.5"
    assert ip_or_none("2001:db8::1") == "2001:db8::1"
    assert ip_or_none("testclient") is None
    assert ip_or_none(None) is None


def test_fit_cuts_only_values_over_size() -> None:
    assert fit("abc", 5) == "abc"
    assert fit("abcdef", 5) == "abcde"
    assert fit(None, 5) is None
