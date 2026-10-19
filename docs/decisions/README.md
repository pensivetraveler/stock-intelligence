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

불필요한 자잘한 결정을 ADR로 남기지 않는다. 사용자와 합의된 `Alpaca + Massive 미선정` 상태를 구현 편의로 변경하지 않는다. 2026-10-17 사용자 결정에 따른 Alpaca 프로토타입(opt-in)은 정식 선정이 아니다(ADR 0012).

- `0001-phase-0-toolchain-and-contract-source.md`: Phase 0 toolchain과 canonical contract source
- `0002-local-market-data-storage.md`: SQLite metadata, immutable Parquet와 복구 journal
- `0003-us-equity-calendar-and-aggregation.md`: US equity calendar, 세션 정책과 1분봉 집계
- `0004-multi-process-store-and-point-in-time-contract.md`: 다중 프로세스 cache 소유권 복구와 point-in-time 응답 규칙
- `0005-calendar-fingerprint-query-boundary-and-job-lifecycle.md`: calendar 일정 fingerprint·고정 범위, cache 전 query 검증, job 종료 상태
- `0006-session-partitions-api-startup-and-single-version.md`: 세션 날짜 partition, 부작용 없는 API import, 단일 제품 버전
- `0007-aggregated-api-cache-composition-and-backfill.md`: 공개 집계 timeframe, cache 범위 조합, 재개 가능한 backfill
- `0008-chart-workspace-slice.md`: Phase 2A 차트 2개 workspace, 합성 SYNTH 데이터, 세션·종목 API
- `0009-manual-and-hypothetical-trade-ledgers.md`: 수동(실제)·가상 거래 원장, 멱등 기록, ET 시간 변환, 분리된 P&L
- `0010-named-workspaces-and-layout-templates.md`: 이름 붙인 workspace, Terminal/Review 템플릿, 되돌리기
- `0011-broker-statement-csv-import.md`: 증권사 거래내역 CSV 가져오기, 중복 검토, 별도 원장, 원본 미보관
- `0012-alpaca-prototype-provider.md`: Alpaca 시세 adapter 프로토타입(opt-in), 무료 요금제 15분 제한, 무체결 분 처리
- `0013-broker-market-data-adapters.md`: 한국투자·토스·키움 시세 adapter(공식 문서 기반, 실계정 확인 대기), 미래에셋 미지원
- `0014-provider-key-registry.md`: 화면에서 시세 키 등록, secret은 OS 키체인, 계좌별 여러 키와 호출 한도 시 다음 키로 전환
