# ================================================================
# Team    : 데이터 엔지니어링팀
# Author  : 노희재
# Created : 2026-10-14
# Desc    : role → service_id 매핑. codegen 어휘에서 손으로 옮긴 사본이다
# ================================================================
#
# 출처: codegen 저장소 packages/core/src/topics/ (커밋 6c5815b, 2026-10-14)
#   store.ts  service_id 'store', roles: product
#
# codegen 어휘가 바뀌면 이 표를 같이 고친다. 어휘는 TypeScript 코드라 이 서버가 직접 읽을 수
# 없고, codegen 에 어휘를 JSON 으로 내보내는 결과물이 아직 없다.
#
# role 이름은 모든 주제를 통틀어 겹치지 않는다(선언 계약 Y8). 그래서 role 하나에 주제 하나다.
ROLE_TO_SERVICE: dict[str, str] = {
    "product": "store",
}

# 이 표를 옮겨 온 codegen 커밋. ade_event 에는 남기지 않고, 표를 고칠 때 함께 고친다
VOCAB_SOURCE = "codegen@6c5815b"


def service_for(role: str | None) -> str | None:
    """role 의 주제 이름. role 이 없거나 표에 없으면 None."""
    if not role:
        return None
    return ROLE_TO_SERVICE.get(role)
