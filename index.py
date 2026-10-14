# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : Vercel 진입점. Vercel 이 이 파일의 최상위 app 을 ASGI 앱으로 읽는다
# ================================================================
#
# 로컬 실행: uv run uvicorn index:app --reload --port 8000
from poc.main import build_app

app = build_app()
