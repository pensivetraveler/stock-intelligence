# Execution Process — 단계별 진행 및 승인 Gate

- **상태:** active
- **적용일:** 2026-10-12
- **갱신일:** 2026-10-15 — Phase 1C까지의 구현과 사용자 승인 범위 반영

이 문서는 `IMPLEMENTATION_ROADMAP.md`의 장기 Phase를 실제 작업 가능한 작은 단계와 사용자 승인 Gate로 연결한다. 제품 범위와 안전 불변조건은 `AGENTS.md`, `SYSTEM_SPEC.md`, 각 Phase 명세가 우선한다.

## 진행 원칙

1. 한 번에 현재 승인된 단계만 수행한다.
2. 각 단계 종료 시 변경 파일, 실제 실행 명령과 결과, 실패·미실행 항목, 남은 위험을 `PROJECT_STATE.md`와 대화창에 보고한다.
3. 이미 승인된 범위 안의 하위 작업은 재확인 없이 진행한다. 사용자의 “내 확인 없이 시간대·집계·품질 구현까지 진행” 지시는 Phase 1C까지 적용되며 이 범위는 완료했다.
4. 승인된 범위의 종료 시 결과를 보고하고, 범위 밖의 다음 단계 진행 여부를 묻는다. Phase 1 내부 마무리(1E), Phase 2A·2B, Phase 3A·3B는 완료했다. 다음 경로(4·3 후속·1D)는 아직 정해지지 않았다.
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

**상태:** 2026-10-12 구현 및 로컬 검증 완료

목적: 외부 API·DB 없이 합성 1분봉이 canonical 계약을 통해 API까지 흐르는 첫 시장 데이터 경로를 만든다.

- [x] 요청·응답 및 `CoverageManifest` 최소 계약 확정
- [x] 합성 미국 주식 1분봉 fixture 작성
- [x] `MarketDataProvider` protocol과 `FakeMarketDataProvider` 구현
- [x] `GET /v1/market/bars` 구현
- [x] UTC, 세션, source/feed scope, revision, finalized 상태 보존
- [x] 잘못된 종목·기간·timeframe, 결측·중복·미래 cutoff 음성 테스트
- [x] 외부 네트워크 호출이 없음을 검증

완료 Gate:

- API와 provider 단위/통합 테스트 통과
- 동일 요청이 동일 fixture revision을 반환한다.
- Phase 1B 저장 계층을 구현하지 않고도 동작한다.

**다음 단계 승인 질문:** Phase 1B SQLite/Parquet cache와 Coverage 저장을 진행할지 확인한다.

## 단계 1B — 로컬 저장·Coverage 기반

**상태:** 2026-10-12 구현 및 로컬 검증 완료

- [x] SQLite metadata/job/coverage schema와 migration
- [x] Parquet 1분봉 partition과 atomic write
- [x] idempotent cache read/write, gap detection, revision pinning
- [x] 재시작·중단·중복·disk failure 테스트

**다음 단계 승인 질문:** Phase 1C 시간대·세션·집계를 진행할지 확인한다.

## 단계 1C — 시간대·세션·집계·품질

**상태:** 2026-10-12 구현 및 로컬 검증 완료

- [x] 미국 거래소 calendar, DST, 반장
- [x] pre/regular/post 세션 구분
- [x] 1m→5m/15m/30m/60m 집계
- [x] 결측·미확정 입력의 partial/provisional 품질 전파
- [x] source/calendar lineage·revision 보존과 혼합 입력 거부

집계는 이 단계에서 내부 모듈까지 구현했다. 공개 API 연결은 단계 1E에서 완료했다. 실제 공급업체 사후 정정 수집·재집계 자동화는 후속 범위다.

**다음 단계 승인 질문:** 실제 Provider PoC 준비 또는 Phase 2 차트 중 어느 경로를 우선할지 확인한다.

## 단계 1E — Phase 1 내부 마무리 (합성 데이터)

**상태:** 2026-10-16 구현 및 로컬 검증 완료. 사용자가 1D·2보다 먼저 진행하도록 승인했다.

- [x] 공개 API의 5m/15m/30m/60m 집계, bucket 정렬 검증과 집계 Coverage
- [x] 완료된 cache 범위 조합과 결측 범위 재조회
- [x] Collector backfill: job·chunk·checkpoint·재개·재시도·throttle
- [x] 실제 CLI subprocess와 uvicorn 서버로 런타임 확인

**다음 단계 승인 질문:** Phase 2 차트 또는 Phase 1D 실제 Provider PoC 중 어느 경로로 갈지 확인한다.

## 단계 2A — 차트 workspace 수직 슬라이스 (합성 데이터)

**상태:** 2026-10-16 구현 및 로컬·브라우저 검증 완료. 사용자가 1D보다 먼저 진행하도록 승인했다.

- [x] 합성 `SYNTH` 하루치(연장장·결측·provisional 포함)와 생성기·drift 검사
- [x] `GET /v1/market/instruments`, `GET /v1/market/sessions/{date}`와 공통 계약
- [x] 독립 설정 차트 2개, 시간 범위 동기화, 레이아웃 저장·복원
- [x] Coverage·품질·세션·lineage·결측 표시, ET 시간 축
- [x] 실제 API·Vite 서버와 in-app browser로 동작 확인(데스크톱·375 px)

## 단계 2B — 이름 붙인 workspace와 레이아웃 템플릿

**상태:** 2026-10-16 구현 및 검증 완료(사용자 "모두 진행" 승인).

- [x] workspace 저장·전환·이름 변경·삭제(마지막 workspace 보호), v1 저장분 이전
- [x] Trading Terminal·Trade Review 템플릿의 명시적 적용, 독립 복사, 되돌리기

## 단계 3A — 수동(실제)·가상 거래 원장

**상태:** 2026-10-16 구현 및 검증 완료(사용자 "모두 진행" 승인).

- [x] 계약 확장(주장 체결 시각·수수료·메모, 시나리오 방향·진입/청산 시점), 원장 응답 계약
- [x] 별도 `journal.sqlite3`, 멱등 기록·409 충돌, ET→UTC 변환과 DST 거부
- [x] 수동 FIFO P&L, 시나리오 P&L, 원장 분리 표시, 차트 마커
- [x] 실제 API·브라우저로 기록·표시·새로고침 후 유지 확인

## 단계 3B — 증권사 거래내역 CSV 가져오기·중복 검토

**상태:** 2026-10-17 구현 및 검증 완료(사용자 승인).

- [x] 열 매핑 추천·편집, 행 정규화·오류 설명, 미리보기
- [x] 체결 ID 기준 자동 중복 제외, ID 없는 동일 체결의 사용자 검토, 같은 파일 재가져오기 방지
- [x] 별도 statement import 원장·P&L·마커, 원본 파일과 매핑하지 않은 열은 저장하지 않음
- [x] 실제 API·브라우저로 미리보기·커밋·재가져오기 차단·계좌번호 미저장 확인

**후속 후보:** 증권사별 preset·XLSX, batch 무효화·재가져오기, 수동 기록과 가져온 체결의 대사, 계좌 구분, 지표(Phase 4), Phase 1D.

**다음 단계 승인 질문:** Phase 4, Phase 3 후속(대사·정정), 또는 Phase 1D 중 어느 경로로 갈지 확인한다.

## 단계 1D — 실제 Provider PoC (별도 권한 필요)

**상태:** 미착수

- 사용자 승인 credential에 한정한 읽기 전용 시험
- Toss/KIS/Kiwoom 및 Alpaca/Massive 동등 비교
- entitlement, feed scope, history, rights, cost 검증
- 주문·결제·자동 구독 없음

**다음 단계 승인 질문:** 검증 결과에 따라 canonical provider 결정을 보류·선택하거나 Phase 2로 진행할지 확인한다.

## 후속 Phase

아래 단계는 모두 미착수다. 누적 구현·검증·제약은 [구현 이력](IMPLEMENTATION_HISTORY.md)에 정리한다.

- Phase 2: 차트와 workspace — 2A·2B 완료
- Phase 3: 실제/수동/가상 거래 기록과 복기 — 3A(수동·가상 원장)·3B(CSV 가져오기) 완료, 정정·대사·broker 확인은 남음
- Phase 4: 지표 플랫폼과 격리 실행
- Phase 5: Replay와 Agent evidence
- Phase 5B: 재현 가능한 백테스트
- Phase 6: 읽기 전용 Broker adapter와 고급 Agent
- Phase 7: 원격 Collector와 Tick 확장

각 Phase는 착수 전에 더 작은 실행 단계와 승인 Gate로 구체화한다.
