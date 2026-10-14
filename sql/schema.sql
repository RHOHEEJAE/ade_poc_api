-- 원시 DB 스키마 (PoC, Supabase Postgres)
-- 수집 API 가 요청 원본(ade_raw)과 풀어 낸 원시 이벤트(ade_event)를 쌓는다. 추가만 하고 고치지 않는다.
-- Supabase 대시보드 → SQL Editor 에서 한 번 실행한다.
--
-- 테이블을 public 이 아니라 ade 스키마에 둔다. Supabase 는 public 스키마를 REST API 로 그대로
-- 내보내서, 공개 키(anon)만 있으면 원시를 읽을 수 있게 된다. ade 스키마는 API 설정
-- (Settings → API → Exposed schemas)에 넣지 않는다.

CREATE SCHEMA IF NOT EXISTS ade;

-- ------------------------------------------------------------
-- ade_raw: 수집 요청 원본. 요청 하나가 한 행
-- ------------------------------------------------------------
CREATE TABLE ade.ade_raw (
  raw_id        uuid        PRIMARY KEY,  -- 수집 서버가 만드는 UUIDv7. 시간순으로 정렬된다
  received_at   timestamptz NOT NULL,     -- 서버가 받은 시각. 브라우저 시각이 아니다
  server_id     text        NOT NULL,     -- 받은 인스턴스 (Vercel 리전 · 배포 ID)
  route         text        NOT NULL CHECK (route IN ('track', 'engage', 'groups')),
  source_ip     inet,                     -- X-Forwarded-For 를 반영한 사용자 IP
  origin        text,                     -- Origin 헤더 원값. 없거나 'null' 일 수 있다
  referer       text,
  user_agent    text,
  content_type  text,                     -- Content-Type 헤더 원값
  query         text,                     -- 쿼리 문자열 원문 (예: verbose=1&ip=0&_=1791436353481)
  body          bytea       NOT NULL,     -- 본문 바이트 그대로
  body_bytes    integer     NOT NULL CHECK (body_bytes >= 0),
  event_count   integer     NOT NULL DEFAULT 0,  -- 이 요청에서 ade_event 로 넣은 행 수
  events_error  text                    -- 풀지 못한 사유. 풀었거나 /engage · /groups 면 null
);

CREATE INDEX ix_raw_received_at ON ade.ade_raw (received_at);

COMMENT ON TABLE ade.ade_raw IS '수집 요청 원본. 요청 하나가 한 행. 추가만 하고 고치지 않는다';

-- ------------------------------------------------------------
-- ade_event: 원시 이벤트. /track 요청을 풀어 이벤트 하나가 한 행
-- 자주 찾는 값만 칸으로 꺼낸다. 원형은 payload 에 그대로 있다.
-- service_id 만 원본에 없는 값이다 — 자기 선언의 role 을 어휘로 찾아 채운다.
-- ------------------------------------------------------------
CREATE TABLE ade.ade_event (
  event_id          uuid        PRIMARY KEY,  -- UUIDv7
  raw_id            uuid        NOT NULL,     -- 이 이벤트가 나온 ade_raw 행
  received_at       timestamptz NOT NULL,     -- 원본과 같은 값 (서버가 받은 시각)
  event_index       integer     NOT NULL,     -- 배치 안에서 몇 번째인지 (0부터)
  event             text,                     -- 이벤트 이름 (예: $ade_click)
  insert_id         text,                     -- properties.$insert_id. 재전송 중복을 거르는 키
  distinct_id       text,
  event_time        timestamptz,              -- properties.time (브라우저 시계)
  application_id    text,
  build_id          text,
  record_type       text,                     -- behavior · system · context
  collection_grade  text,                     -- guaranteed · observed
  collection_source text,                     -- codegen · direct · auto
  verdict           text,                     -- usable · needs_classification · collection_failed
  role              text,                     -- properties.ade_declaration.role
  service_id        text,                     -- role 로 찾은 주제. role 이 없으면 null
  payload           jsonb       NOT NULL,     -- 이벤트 원형 그대로
  UNIQUE (raw_id, event_index)
);

CREATE INDEX ix_event_received_at ON ade.ade_event (received_at);
CREATE INDEX ix_event_service_event ON ade.ade_event (service_id, event);
CREATE INDEX ix_event_insert_id ON ade.ade_event (insert_id);

COMMENT ON TABLE ade.ade_event IS '원시 이벤트. /track 요청을 풀어 이벤트 하나가 한 행. 추가만 하고 고치지 않는다';

-- ------------------------------------------------------------
-- ade_diag: SDK 오류 신호. 이벤트와 섞지 않는다
-- ------------------------------------------------------------
CREATE TABLE ade.ade_diag (
  diag_id        uuid        PRIMARY KEY,
  received_at    timestamptz NOT NULL,
  server_id      text        NOT NULL,
  source_ip      inet,
  origin         text,
  kind           text        NOT NULL,   -- 읽을 수 없으면 'unparsable'
  lib_version    text,
  application_id text
);

CREATE INDEX ix_diag_received_at ON ade.ade_diag (received_at);

COMMENT ON TABLE ade.ade_diag IS 'SDK 오류 신호. kind · lib_version · application_id 세 값과 받은 정보만';

-- ------------------------------------------------------------
-- 공개 API 역할은 이 스키마에 접근하지 못하게 한다
-- ------------------------------------------------------------
REVOKE ALL ON SCHEMA ade FROM anon, authenticated;

-- ------------------------------------------------------------
-- 수집 API 계정: 넣기 · 읽기만. 고치기 · 지우기 권한이 없다
-- 아래 비밀번호 자리를 바꿔 실행한다. 비밀번호는 이 파일에 남기지 않는다.
-- 연결 풀러로 접속할 때 사용자 이름은 'ade_collector.<프로젝트 ref>' 형식이다.
-- ------------------------------------------------------------
CREATE ROLE ade_collector LOGIN PASSWORD '<여기에-비밀번호>';
GRANT USAGE ON SCHEMA ade TO ade_collector;
GRANT INSERT, SELECT ON ade.ade_raw, ade.ade_event, ade.ade_diag TO ade_collector;
