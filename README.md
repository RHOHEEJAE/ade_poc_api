# ade_poc_api

ADE 개념증명(PoC)용 수집 API. 웹 수집 SDK 가 보낸 요청을 원시 DB(Postgres)에 넣는다.

```
SDK → 수집 API (Vercel) → 원시 DB (Supabase Postgres)
                            ├ ade.collection_request_history  요청 원본. 요청 하나가 한 행, 본문은 base64
                            ├ ade.event_history               원시 이벤트. /track 을 풀어 이벤트 하나가 한 행
                            └ ade.diagnosis_history           SDK 오류 신호
```

테이블 · 컬럼 이름은 사내 DB 네이밍 컨벤션(hecto-db-naming)을 따른다. 정의는 `sql/schema.sql` 에 있다.

PoC 에서 하지 않는 것: 토큰 확인 · 중복 제거 · 속도 제한 · Kafka · S3.

## 엔드포인트

| 경로 | 하는 일 | 응답 |
| --- | --- | --- |
| `POST /track` | 요청 원본 1행을 `collection_request_history` 에, 풀어 낸 이벤트 N행을 `event_history` 에 저장 | 200 · 413 · 503 |
| `POST /engage` · `/groups` | 요청 원본만 `collection_request_history` 에 저장 (프로필 갱신이라 이벤트로 풀지 않음) | 200 · 413 · 503 |
| `POST /diag` | SDK 오류 신호의 `kind` · `lib_version` · `application_id` 만 `diagnosis_history` 에 저장 | 200 · 503 |
| `OPTIONS` (위 경로) | CORS 사전 요청 | 204 |
| `/settings` · `/record` · `/flags` | 쓰지 않는 벤더 기능 | 404 |
| `GET /livez` | 프로세스 확인 | 200 |
| `GET /readyz` | 원시 DB 접속과 테이블 셋 확인 | 200 · 503 |

- 200 은 DB 저장이 끝난 뒤에만 준다. 본문 `1`, `?verbose=1` 이면 `{"status": 1, "error": null}`.
- 깨진 본문 · 등록되지 않은 출처도 저장하고 200 을 준다. 등록된 출처에만 CORS 헤더를 붙인다.
- 그 밖의 4xx 는 주지 않는다. SDK 가 배치를 지우기 때문이다.

## 원시 이벤트 (`ade.event_history`)

- `/track` 본문을 이벤트 단위로 풀어 한 행씩 넣는다. 원형은 `event_properties`(json)에 그대로 있고, 자주 찾는 값(`event_name` · `source_event_no`(`$insert_id`) · `application_no` · `collection_source_code` · `verdict_code` 등)만 칸으로 꺼낸다.
- **`service_code`** 는 원본에 없는 값이다. 이벤트 자기 선언의 role(`properties.ade_declaration.role`)을 어휘로 찾아 codegen 의 `service_id` 를 채운다. role 이 없으면(자동 수집 · 직접 전달 이벤트) 비어 있다.
- 원본과 원시는 한 트랜잭션으로 넣는다. 본문을 풀지 못하거나 DB 가 값을 거절하면 원시는 넣지 않고, `collection_request_history.parse_error_type_code` 에 사유를 남긴 채 원본만 넣고 200 을 준다.

| `parse_error_type_code` | 뜻 |
| --- | --- |
| `empty_body` · `no_data_param` · `json_parse` · `base64` | 본문을 풀지 못했다 |
| `payload_not_json` | 이벤트에 JSON 으로 쓸 수 없는 값(NaN 등)이 있다 |
| `event_insert_failed` | DB 가 값을 거절했다 (예: 이벤트 이름의 NUL 문자, 칸 크기를 넘는 코드값) |

### 어휘 (`poc/vocab.py`)

`role → service_id` 표는 codegen 저장소의 `packages/core/src/topics/` 를 손으로 옮긴 사본이다. 출처 커밋을 파일 머리에 적어 두었다. **codegen 에서 주제나 role 을 더하면 이 표도 같이 고친다.** 이미 넣은 행의 `service_code` 는 바뀌지 않으므로, 필요하면 `collection_request_history` 원본에서 다시 만든다.

## 원시 DB 준비 (Supabase)

0. (네이밍 컨벤션 적용 전 테이블이 있으면) `sql/drop_old_tables.sql` 을 먼저 실행한다. 들어 있던 데이터도 지워진다.
1. Supabase 대시보드 → SQL Editor 에서 `sql/schema.sql` 을 실행한다. 여러 번 실행해도 된다. 맨 아래 비밀번호 자리를 바꿔서 실행하고, 비밀번호는 파일에 남기지 않는다.
2. `ade` 스키마는 Settings → API → Exposed schemas 에 넣지 않는다. 넣으면 공개 키로 원시를 읽을 수 있다.
3. 접속 주소는 연결 풀러(transaction 모드, 포트 6543)를 쓴다. 사용자 이름은 `ade_collector.<프로젝트 ref>` 형식이다.

## 로컬 실행

```bash
cp .env.example .env   # ADE_DB_URL 을 채운다
uv sync
uv run uvicorn index:app --reload --port 8000 --env-file .env
```

## 시험

```bash
uv run pytest -q
uv run ruff check .
```

## Vercel 배포

- `index.py` 의 최상위 `app` 이 진입점이다. `vercel.json` 이 FastAPI 프리셋을 고정한다.
- Project Settings → Environment Variables 에 `ADE_DB_URL` · `ADE_ORIGINS` 를 넣는다.
- `server_name` 칸에는 Vercel 의 리전 · 배포 ID(`VERCEL_REGION` · `VERCEL_DEPLOYMENT_ID`)가 들어간다. System Environment Variables 를 켜야 보인다.

## 서버리스라서 생기는 한계

| 항목 | 내용 |
| --- | --- |
| DB 연결 | 인스턴스 하나에 연결 최대 2개. 인스턴스가 늘면 연결도 늘어나므로 풀러를 거친다 |
| 응답 뒤 작업 | 하지 않는다. 저장을 끝낸 뒤 200 을 준다 |
| 속도 제한 | 없다. 메모리로 세면 인스턴스마다 따로 세져 의미가 없다 |
| 본문 상한 | 2MiB (`ADE_BODY_LIMIT`). Vercel 함수 상한 4.5MB 보다 작다 |

## 규약 예외 · 대체

| 컬럼 | 내용 |
| --- | --- |
| `event_history.analytics_user_no` | 번호 분류어는 `varchar(40)` 인데 SDK `distinct_id` 가 44자(`$device:` + UUID)라 `varchar(200)` 으로 예외 |
| `collection_request_history.request_body_content` | 바이트를 담는 분류어가 없어 `content`(text)에 base64 로 담는다 |
| `diagnosis_history.library_version_code` | 점 표기 문자열이라 `version`(integer) 대신 `code` |

개인정보 등급 personal(분류어 기준 잠정): `client_ip` · `analytics_user_no` · `request_body_content` · `event_properties`.
