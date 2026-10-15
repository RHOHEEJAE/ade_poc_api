-- 네이밍 컨벤션 적용 전 테이블을 지운다. 들어 있던 데이터도 함께 지워지고 되돌릴 수 없다.
-- 순서: 이 파일 실행 → schema.sql 실행 → 새 코드 배포
DROP TABLE IF EXISTS ade.ade_event, ade.ade_raw, ade.ade_diag;
