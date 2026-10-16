# Architecture Decision Records

결정에 중대한 변경 비용이 있을 때에만 `0001-의사결정-제목.md` 형식의 ADR을 작성한다.

```text
Status: proposed | accepted | superseded
Context: 어떤 제약/문제가 있었나
Decision: 무엇을 선택했나
Alternatives: 어떤 대안이 있었나
Trade-offs: 이점/비용/실패 모드
Evidence/verification: 확인한 코드/테스트/근거
Revisit when: 어느 조건에서 재검토하나
```

불필요한 자잘한 결정을 ADR로 남기지 않는다. 사용자와 합의된 `Alpaca + Massive 미선정` 상태를 구현 편의로 변경하지 않는다.

- `0001-phase-0-toolchain-and-contract-source.md`: Phase 0 toolchain과 canonical contract source
- `0002-local-market-data-storage.md`: SQLite metadata, immutable Parquet와 복구 journal
- `0003-us-equity-calendar-and-aggregation.md`: US equity calendar, 세션 정책과 1분봉 집계
- `0004-multi-process-store-and-point-in-time-contract.md`: 다중 프로세스 cache 소유권 복구와 point-in-time 응답 규칙
- `0005-calendar-fingerprint-query-boundary-and-job-lifecycle.md`: calendar 일정 fingerprint·고정 범위, cache 전 query 검증, job 종료 상태
- `0006-session-partitions-api-startup-and-single-version.md`: 세션 날짜 partition, 부작용 없는 API import, 단일 제품 버전
