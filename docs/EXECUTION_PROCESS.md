# Execution Process — 단계별 진행 및 승인 Gate

- **상태:** active
- **적용일:** 2026-10-12

이 문서는 `IMPLEMENTATION_ROADMAP.md`의 장기 Phase를 실제 작업 가능한 작은 단계와 사용자 승인 Gate로 연결한다. 제품 범위와 안전 불변조건은 `AGENTS.md`, `SYSTEM_SPEC.md`, 각 Phase 명세가 우선한다.

## 진행 원칙

1. 한 번에 현재 승인된 단계만 수행한다.
2. 각 단계 종료 시 변경 파일, 실제 실행 명령과 결과, 실패·미실행 항목, 남은 위험을 `PROJECT_STATE.md`와 대화창에 보고한다.
3. 보고 후 사용자에게 다음 단계 진행 여부를 묻는다.
4. 사용자의 명시적 승인을 받기 전에는 다음 단계 구현을 시작하지 않는다.
5. 외부 계정, 유료 서비스, Broker, 주문, 크롤링 또는 credential이 필요한 단계는 일반 단계 승인과 별도로 구체적인 권한을 확인한다.

## 단계 0 — Phase 0 기준선 확정

목적: 이후 변경의 비교 기준이 되는 재현 가능한 초기 상태를 고정한다.

- [x] P0-01~P0-07 구현
- [x] 로컬 전체 검증 스크립트 통과
- [x] 실제 API 재시작 및 웹 개발 셸 기동 확인
- [x] Tauri 네이티브 미검증 사유 분리
- [x] 변경 파일 최종 검토와 비밀정보 검사
- [x] 초기 Git commit 생성
- [x] 원격 저장소/CI 실행 여부 기록 — 원격 미설정, 원격 CI 미실행

완료 Gate:

- 작업 트리가 깨끗하거나 남은 변경이 명확히 설명된다.
- `./scripts/verify.sh`가 통과한다.
- 초기 commit ID와 원격 CI 상태가 보고된다.

**다음 단계 승인 질문:** Phase 1A FakeMarketDataProvider 수직 슬라이스를 진행할지 확인한다.

## 단계 1A — FakeMarketDataProvider 수직 슬라이스

목적: 외부 API·DB 없이 합성 1분봉이 canonical 계약을 통해 API까지 흐르는 첫 시장 데이터 경로를 만든다.

- 요청·응답 및 `CoverageManifest` 최소 계약 확정
- 합성 미국 주식 1분봉 fixture 작성
- `MarketDataProvider` protocol과 `FakeMarketDataProvider` 구현
- `GET /v1/market/bars` 구현
- UTC, 세션, source/feed scope, revision, finalized 상태 보존
- 잘못된 종목·기간·timeframe, 결측·중복·미래 cutoff 음성 테스트
- 외부 네트워크 호출이 없음을 검증

완료 Gate:

- API와 provider 단위/통합 테스트 통과
- 동일 요청이 동일 fixture revision을 반환한다.
- Phase 1B 저장 계층을 구현하지 않고도 동작한다.

**다음 단계 승인 질문:** Phase 1B SQLite/Parquet cache와 Coverage 저장을 진행할지 확인한다.

## 단계 1B — 로컬 저장·Coverage 기반

- SQLite metadata/job/coverage schema와 migration
- Parquet 1분봉 partition과 atomic write
- idempotent cache read/write, gap detection, revision pinning
- 재시작·중단·중복·disk failure 테스트

**다음 단계 승인 질문:** Phase 1C 시간대·세션·집계를 진행할지 확인한다.

## 단계 1C — 시간대·세션·집계·정정

- 미국 거래소 calendar, DST, 반장
- pre/regular/post 세션 구분
- 1m→5m/15m/30m/60m 집계
- partial/missing/correction 품질 전파

**다음 단계 승인 질문:** 실제 Provider PoC 준비 또는 Phase 2 차트 중 어느 경로를 우선할지 확인한다.

## 단계 1D — 실제 Provider PoC (별도 권한 필요)

- 사용자 승인 credential에 한정한 읽기 전용 시험
- Toss/KIS/Kiwoom 및 Alpaca/Massive 동등 비교
- entitlement, feed scope, history, rights, cost 검증
- 주문·결제·자동 구독 없음

**다음 단계 승인 질문:** 검증 결과에 따라 canonical provider 결정을 보류·선택하거나 Phase 2로 진행할지 확인한다.

## 후속 Phase

- Phase 2: 차트와 workspace
- Phase 3: 실제/수동/가상 거래 기록과 복기
- Phase 4: 지표 플랫폼과 격리 실행
- Phase 5: Replay와 Agent evidence
- Phase 5B: 재현 가능한 백테스트
- Phase 6: 읽기 전용 Broker adapter와 고급 Agent
- Phase 7: 원격 Collector와 Tick 확장

각 Phase는 착수 전에 더 작은 실행 단계와 승인 Gate로 구체화한다.
