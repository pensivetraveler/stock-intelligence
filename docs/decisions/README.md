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
- `0015-daily-bar-cache-by-year.md`: 일봉 캐시를 확정된 연도 단위로 저장, 올해는 항상 새로 조회, 리비전 혼합 금지
- `0016-ticker-search-symbol-directory.md`: 미국 상장 전 종목 티커 검색(Alpaca 자산 목록, 하루 단위 로컬 사본), 검색한 종목 차트·관심종목 추가
- `0017-toss-direct-integration.md`: 토스 직접 연동(키 1개 공유 세션, 조회 전용 허용목록, 그룹별 호출 한도), 일봉·실시간 시세, 단계 계획
- `0018-broker-api-execution-ledger.md`: 증권사 API 체결 원장(토스, 주문 1건당 1행·평균가·마지막 체결 시각), 계좌 조회 전용, 계좌번호 끝 4자리
- `0019-toss-realtime-relay-and-tick-store.md`: 토스 실시간 중계(API 단일 연결, Origin 검사, 재연결·PING), 체결 시 원장 자동 동기화, 유실 표시 틱 저장소(봉과 분리)
- `0020-korean-market-foundation.md`: 국내 주식 기반(K1) — XKRX 캘린더·NXT 확장 시간, kr: 종목 ID, KRW, 시장별 라우팅(국내=토스), 하루 전체 일봉(all_day)
- `0021-backtest-engine.md`: 백테스트 엔진(B1) — 규칙·Python 전략(격리 프로세스), 차트와 같은 인과적 지표, 다종목 포트폴리오, 다음 봉 시가 체결·수수료·슬리피지, 입력 고정, 대기열 실행
- `0022-chart-trade-review-agent.md`: 차트·매매 복기 Agent(Phase 5) — 로컬 근거(사후 정보 표시, 시점 이후 차단) + 요청 시 Claude 해설(근거만 전송, 투자 조언 아님), 차트 시점·실제 체결·백테스트 복기, 조회 전용
- `0023-python-custom-indicators.md`: Python 사용자 지표(Phase 4) — update(ctx)를 봉마다 호출해 구조적으로 미래 차단, 전략 샌드박스 재사용, 버전 고정(py:id@버전), 차트 통합
- `0024-filings-and-dark-pool-agents.md`: 공시·다크풀 근거 Agent(Phase 6) — SEC EDGAR(Submissions·CompanyFacts, User-Agent 명시, 초당 5회 이하)와 FINRA 주간 ATS/비ATS 집계(지연·방향 없음 표시), 게시 시각과 최초 관측 시각 분리, 시점 이후 차단, 조회 전용
- `0025-broker-reconciliation.md`: 증권사 API 원장과 수동·거래내역서 체결의 대사(조회 전용 계산 보기, 원장 불변) — 시각 정밀도별 창, bp 가격 허용 오차, 1:1·여러 체결 합산·주문번호 연결, 일치·불일치·한쪽에만 분류와 사유
- `0026-formula-indicators.md`: 수식 지표(Phase 4) — 허용 목록 함수·사칙연산의 안전한 수식, eval 없는 파서, 상수 길이·과거만 참조, 차트별 저장, 입력 중 검증
- `0027-higher-timeframe-indicators.md`: 큰 시간대 지표 — 분봉 차트에 일봉·주봉 지표, 확정·공개된 큰 봉만 각 차트 봉 종료 시점 기준으로 반영(미래 차단), 요청별 캐시
- `0028-electron-desktop-shell.md`: Electron 데스크톱 셸 — siw://app 출처로 화면 제공, 로컬 API는 저장소에서 찾거나 실행(번들 안 함), 서명 없는 x64 .app/.dmg
- `0035-alpaca-default-us-provider.md`: Alpaca(SIP·BOATS)를 미국 시세·종목 검색·기업 행위의 기본 공급업체로 선정(사용자 결정, 코드 기본값은 합성 데이터 유지), Massive는 미검증 대체 후보
- `0036-desktop-app-roles.md`: 데스크톱 앱 역할 분리 — Tauri(SIW Agent)는 승인된 전략 실행·단계별 주문 Gate(첫 단계 KIS 모의투자만), Electron(SIW Research)은 사용자·AI 연구·백테스트(주문 없음)
- `0037-paper-order-execution.md`: (accepted) SIW Agent 단계 1 설계 — 모의투자 주문(키움 먼저, KIS 다음), 증권사 중립 의도 계약·adapter, 별도 모의 원장, 주문 의도 계약, 실계좌 도메인 코드 차단, 한도·킬 스위치, 별도 실행기 프로세스, 화면 빌드 분리
- `0038-price-level-strategies.md`: (proposed) 가격대 진입형 전략 — 미리 걸어 두는 지정가(증권사 체결)와 실시간 감시 돌파(키움 모의는 지정가만), `levels` 규칙, 백테스트 대응, 킬 스위치·실시간 출처 미결
- `0039-indicator-style-and-lower-timeframes.md`: (accepted) 보조지표 설정을 TradingView식 입력·스타일 탭으로(소스, 선별 색·굵기·선 모양·표시, 기준선), 더 작은 시간대 지표(각 차트 봉의 마지막 작은 봉 값, 미래 값 없음)
