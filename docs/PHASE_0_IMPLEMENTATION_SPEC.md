# Phase 0 Implementation Specification — Codex 실행 명세 v1.5

- **상태:** 2026-10-12 구현 및 로컬 검증 완료 / Tauri 네이티브 실행만 Rust prerequisite 부재로 미검증
- **범위:** Repository Bootstrap + Cross-Language Contracts + Basic Health Checks
- **선행 문서:** `AGENTS.md`, `docs/PROJECT_INSTRUCTIONS.md`, `docs/SYSTEM_SPEC.md`, `docs/IMPLEMENTATION_ROADMAP.md`
- **범위 밖:** 실제 시장 데이터/API 호출, 실거래·계좌 접근, 결제, 웹 크롤링, 차트 기능 완성, Python 지표 실행, 백테스트 엔진, Agent 오케스트레이션

## A. 목적과 Phase 0 완료 정의

Phase 0은 대규모 기능 구현이 아니라 **후속 Phase 1~7을 안전하게 연결할 실행 가능한 기반**이다. 신규 Codex 세션이 문서→코드→테스트로 일관되게 이어질 수 있어야 한다.

완료 시 입증해야 하는 관찰 결과:

1. 새 저장소 체크아웃 후 README에 기재된 설치/실행 절차를 따라 API 서버와 데스크톱 개발 셸을 실행할 수 있다(해당 OS의 Tauri 전제조건을 명시).
2. `GET /health`와 `GET /version`이 실제 HTTP 응답을 반환한다. health는 readiness를 가장하지 않고 기본 프로세스 liveness를 나타낸다.
3. 표준 데이터 계약이 유효한 Fixture는 수용하고, 핵심 잘못된 Fixture는 거부한다.
4. 검증 명령으로 포맷·린트·타입체크·단위/계약 테스트가 통과한다. CI가 같은 경로를 실행한다.
5. Broker 또는 유료 데이터 Provider를 선택·구독하거나 외부 계정/실거래를 호출하지 않았다.

## B. 순서 및 산출물

### P0-01 — Runtime inventory / repository bootstrap

- 작업 전 `git status`, OS, Python/Node/Rust 설치 여부, 초기 상태, 기존 파일을 확인. 기존 사용자의 수정 사항을 덮어쓰지 않는다.
- 단일 저장소 구조를 `docs/REPOSITORY_STRUCTURE.md`와 일치시킨다. 패키지 관리자/버전을 선택하고 잠금 파일과 tool version 정보를 남긴다.
- `.gitignore`에 키·`.env`·로컬 DB·Parquet·테스트 임시물·빌드 산출물을 제외한다. `.env.example`은 가짜 값을 쓰거나 빈 값만 제공한다.
- 산출물: README 최초 실행 절차, 부팅 가능한 작업공간, `docs/PROJECT_STATE.md` 상태 갱신.

### P0-02 — API 최소 실행 경로

- `services/api`에 FastAPI 패키지 구성, 명시적 API 버전, 구조화된 에러 응답, 프로세스 시작 명령을 작성한다.
- 계약 예시(경로는 최종 합의 전까지 임시):
  - `GET /health` → HTTP 200 `{ "status": "ok" }`
  - `GET /version` → HTTP 200 `{ "name": "stock-intelligence-workstation-api", "api_version": "v1", "app_version": "0.1.0-dev" }`
- API가 반환하는 모든 애플리케이션 오류는 최소 `{ "error": { "code": "stable_machine_code", "message": "human-readable message", "request_id": "opaque-id-or-null" } }` 형태를 사용한다. `code`는 안정적인 계약이고 `message`는 비밀정보·stack trace를 포함하지 않는다. FastAPI 기본 404/validation 오류도 이 envelope로 정규화한다.
- `health`에는 실제로 연결하지 않은 DB/Collector/Provider 상태를 성공으로 허위 보고하지 않는다. 선택 사항으로 `/ready`를 만들더라도 정확한 의존성만 검사한다.
- 검증: `curl` 또는 FastAPI 테스트 클라이언트로 성공 응답과 없는 경로의 404가 위 계약을 따르는지 확인한다. Phase 0 공개 endpoint에 validation 입력이 없다면 합성 `RequestValidationError`로 exception handler를 단위 테스트하며, 테스트 전용 공개 endpoint를 추가하지 않는다. 서버를 재시작해 같은 계약이 유지되는지 확인한다. 서로 다른 요청의 `request_id`가 실제로 생성되는 구현이면 고정값을 테스트하지 않는다.

### P0-03 — Desktop 최소 실행 경로

- `apps/desktop`에 React + TypeScript 개발 화면을 만들고, Tauri 2 데스크톱 셸을 연결한다. 첫 화면은 앱 버전, 현재 API 연결 상태, `/health` 결과를 표시하면 충분하다.
- 실패한 API 요청/백엔드 중단 상태를 사용자에게 표시한다. 실제 차트/도킹/증권사 로그인 화면은 만들지 않는다.
- 개발 중 API URL을 환경 변수로 지정 가능하게 하고, CORS는 로컬 개발 원본만 허용한다. 임의의 와일드카드 허용은 쓰지 않는다.
- 연결 상태는 최소 `checking | connected | disconnected`로 구분한다. 5초 이내의 제한된 health 요청이 2xx와 유효한 payload를 반환할 때만 `connected`이며, timeout·network error·non-2xx·잘못된 payload는 `disconnected`다. 기본 재시도 간격은 5초로 하되 테스트에서 주입 가능해야 하고, 수동 재시도도 제공할 수 있다. 마지막 오류와 마지막 성공 시각은 실제 관찰값일 때만 표시한다.
- 검증: 데스크톱 개발 셸 실행 후 (1) API가 처음부터 정상인 경우, (2) 연결 거부/timeout/non-2xx, (3) 중단 후 복구되어 다음 재시도에서 `connected`로 돌아오는 상태 전이를 fake timer 또는 제어된 테스트 서버로 확인한다. GUI 빌드가 현재 OS에서 불가능하면 웹 화면/타입체크를 검증하고 Tauri 빌드는 `blocked: prerequisite`로 기록한다.

### P0-04 — Contracts v0.1: language-neutral schemas

- JSON Schema Draft 2020-12를 계약 기준으로 쓰는 **제안**. 기존 코드나 도구 제약이 발견되면 대안을 ADR로 기록하고 변경한다. 모든 계약에 `schema_version`과 명시적 필드 요구사항을 둔다.
- 최소 객체 집합(`packages/contracts/schemas/`, 정확한 파일명은 구현 시 결정):

| 계약                       | 최소 필수 의미                                                                                                                           | Phase 0 안전 불변식                                                                              |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `InstrumentRef`            | 고유 ID, symbol, 거래소/자산 클래스, 통화, 식별 기준 날짜/출처                                                                           | 심볼만으로 안정 ID 가정 금지                                                                     |
| `PriceBar`                 | instrument, timeframe, UTC interval start/end, OHLCV, session, finalized, adjustment policy, source & feed scope, revision, available_at | `open<=high`, `low<=close<=high` 등 OHLC 범위, interval end>start, 타임존 필수, volume 음수 금지 |
| `SourceCapabilities`       | source ID, 타입(paid/broker-market/public/web), capability/entitlement/verified 상태, feeds, rights, observed_at                         | `documented` ≠ `tested` ≠ `licensed`; unknown을 true로 기본값 설정 금지                          |
| `SourceRecord`             | source ID, record reference/hash, event/published/available/retrieved timestamps (존재 여부 구분), parser version                        | 획득 시각과 발표 시각 분리                                                                       |
| `ExecutionBase`            | 직접 인스턴스화하지 않는 공통 id/instrument/side/quantity/price/currency/provenance 필드                                                 | 공통 필드 재사용용이며 단일 혼합 원장 타입으로 사용 금지                                         |
| `BrokerConfirmedExecution` | broker/account reference, native execution ID, exact broker timestamp, verification/correction lineage                                   | broker 증거가 있는 실제 체결만 허용                                                              |
| `ManualExecution`          | 사용자가 주장한 실제 체결, 시간 정밀도, 선택한 candle interval, evidence/reconciliation 상태                                             | candle 클릭 시 정확 체결 시각·가격 가정 금지; broker-confirmed로 자동 승격 금지                  |
| `BacktestFill`             | run/order ID, simulated fill time/price, fill model/version/reason                                                                       | 실제·수동 체결 원장에 저장 금지                                                                  |
| `ScenarioDefinition`       | id, origin, hypothetical entry/exit, fees/slippage assumptions, version                                                                  | 모의 시나리오는 실제 체결 데이터가 아님                                                          |
| `IndicatorDefinition`      | id, version, parameters, warmup, output metadata, point-in-time constraints                                                              | 차트별 instance는 독립; 코드 실행 자체는 미구현                                                  |
| `AgentEvidence`            | source refs, data revisions, cutoff, observed vs inferred, uncertainty                                                                   | 사후 데이터로 과거 판단을 꾸며내지 않음                                                          |
| `BacktestRunConfig`        | dataset/revision, strategy/version, time range, capital, fill policy, fee/slippage, seed if applicable                                   | 백테스트 데이터·가정을 고정; 결과 실행기는 후속 Phase 5B                                         |

- 정확한 스키마 필드/식별 모델은 기존 `SYSTEM_SPEC.md`의 불변조건을 위반하지 않는 선에서 작은 ADR로 선택한다. 금액·가격·수량은 부동소수 JSON number가 아니라 정규화된 base-10 Decimal string(예: `^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`) 또는 손실 없는 동등 표현을 **필수**로 사용한다. 음수 허용 여부와 단위·통화를 필드별로 명시한다. 거래량도 단위를 명시하고 정수 여부를 source capability와 함께 정의한다.
- 저장·계약의 canonical timestamp는 ISO 8601 UTC `Z` 형식이어야 한다. 입력 경계에서 다른 offset을 받을 수 있다면 즉시 UTC로 정규화하며, naïve timestamp와 존재하지 않거나 모호한 현지시간을 거부한다. 거래소 표시 시간은 IANA zone `America/New_York`과 calendar/session 정책으로 별도 파생한다.
- JSON Schema Draft 2020-12만으로 비교하기 어려운 `interval_end > interval_start`, `high >= max(open, close, low)`, `low <= min(open, close, high)`, 시간 정렬, 유형별 원장 분리 같은 교차 필드 규칙은 **별도 semantic validator**에서 검사한다. JSON Schema 통과만으로 계약 유효를 선언하지 않는다.
- 교차 언어 검증은 Phase 0 필수다. 동일한 canonical JSON Schema와 fixture 집합을 Python과 TypeScript 양쪽에서 검증한다. TypeScript 타입을 schema에서 생성하거나 schema 기반 validator에서 타입을 유도하고, 생성 산출물이 있다면 재생성 후 diff가 없어야 한다. Python 모델을 별도로 둘 경우 같은 schema 검증을 통과해야 하며 손으로 복제한 필드 정의가 canonical schema와 어긋나지 않는 CI 검사를 둔다.
- 필수 검증: schema 자체 metaschema 검사, valid/invalid fixture, Python 계약 테스트, TypeScript 계약 테스트, semantic validator 음성 테스트. BrokerConfirmedExecution·ManualExecution·ScenarioDefinition·BacktestFill의 discriminator와 저장 대상이 교차 수용되지 않는 fixture를 포함한다.

### P0-05 — Collector process boundary

- `services/collector`에 독립 프로세스 시작점과 기본 진단 메커니즘만 정의한다. Fake Provider/fixture 진입점은 인터페이스까지만 만들 수 있다.
- API에 Collector 내부 DB 파일을 직접 참조하는 경로를 만들지 않는다. 원격 이동 가능한 통신 경계를 주석/계약 문서로 명시한다.
- Alpaca, Massive, Toss, KIS, Kiwoom은 **Adapter 이름/등록용 capability placeholder**만 허용. 테스트하지 않은 기능은 `unverified/not_tested`; 외부 HTTP 호출·키 발급·구독은 만들지 않는다.
- 진단 명령은 API 서버나 외부 네트워크 없이 실행되고 stdout에 최소 `{ "status": "ok", "service": "collector", "external_calls": false }`를 출력하며 정상일 때 exit code `0`을 반환한다. 잘못된 사용자 설정은 `2`, 예상하지 못한 내부 실패는 `1`을 반환하고 stderr에 비밀정보나 stack trace를 기본 노출하지 않는다. 일반 실행은 SIGINT/SIGTERM을 받아 진행 중 작업이 없는 Phase 0 상태에서 제한 시간 내 정상 종료(`0`)한다.
- 검증: API 서버가 없는 상태의 진단 성공, 의도적으로 잘못된 설정의 exit code `2`, 독립 시작 후 SIGINT/SIGTERM 정상 종료를 확인한다. 정확한 명령 경로는 선택한 Python 패키지 구조와 함께 README에 고정한다.

### P0-06 — Quality gates / CI

- Python formatting/lint/type + pytest, TypeScript lint/type/build + unit/contract tests, JSON Schema metaschema/valid/invalid 검증과 semantic validator 실행 명령을 제공한다.
- CI에서 실제 실행과 동일한 검증을 사용한다. GUI 빌드가 CI 환경상 어렵다면 분리된 의존성 검증·문서화가 필요하다.
- 최소 실패 사례: OHLC 값 불일치, interval 역전, non-UTC/naïve timestamp, 부동소수 금액·수량, source entitlement `unknown`을 true로 취급, broker/manual/scenario/backtest 타입 교차 수용, 문서 지원만으로 검증 완료 처리, API 연결 거부·timeout·non-2xx·잘못된 health payload.
- 버전/환경 제약으로 일부 테스트 실행이 불가능하면 출력/원인과 우회 없이 남은 테스트를 보고한다. 성공을 가정하지 않는다.

### P0-07 — Handoff / acceptance report

- 변경 파일, 실행 명령, 실제 성공·실패 로그 요약, 테스트 미수행 항목, 남은 위험, 후속 Phase 1의 첫 작업을 README와 `docs/PROJECT_STATE.md`에 기록한다.
- 애매한 선택(도구 버전, 모노레포 워크스페이스, 스키마 코드생성) 중 장기 변경 비용이 있는 것만 ADR로 남긴다.
- 최종 결과가 Phase 0 수락 기준에 못 미치면 `partial/blocked`로 보고하고 해결에 필요한 조건을 특정한다.

## C. Phase 0 비범위 / 비용 안전 장치

- Alpaca·Massive 비교는 정식 도입 미결정. 어느 한쪽을 과거분봉/실시간 전용 기본값으로 암묵 지정하지 않는다.
- 개인 증권사 계정·유료 API 토큰 요구는 Phase 0에서 필요하지 않다. 로그인/인증·계좌 조회·주문 endpoints를 구현하지 않는다.
- Data ingestion, Parquet 저장, 분봉 집계, chart candle click, 실제/가상 거래 UI 및 백테스트 결과는 후속 구현이다. 데이터 계약의 샘플 fixture가 있더라도 기능 성공을 주장하지 않는다.
- 시스템 밖 사이트의 HTML 수집은 이 단계에서 수행하지 않는다.

## D. 검증 체크리스트 (Codex 완료 보고용)

- [ ] README의 fresh clone 또는 ZIP+`git init` 설치 경로 확인
- [ ] `GET /health` / `GET /version` 실제 HTTP 응답 확인
- [ ] 데스크톱 React/TS 실행 + API 장애 상태 확인
- [ ] Tauri 2 dev 확인, 또는 환경 제약 `blocked` 명시
- [ ] Collector 독립 진단/종료 확인
- [ ] JSON Schema 자체 검증 + valid/invalid fixture 검사
- [ ] Python/TypeScript가 동일 schema·fixture를 검증하고 생성 산출물 drift가 없음
- [ ] semantic validator의 OHLC/interval/UTC/Decimal/source provenance/원장 타입 음성 테스트
- [ ] 구조화 404/validation 오류 envelope 및 request ID 정책 확인
- [ ] API 연결 checking/connected/disconnected·timeout·복구 상태 전이 확인
- [ ] Collector 진단 JSON·exit code·SIGINT/SIGTERM 종료 확인
- [ ] Python/TS 포맷·린트·타입·단위/계약 테스트 및 CI
- [ ] 가짜 키·로그·Fixture에 실제 계좌 정보가 없음
- [ ] docs/PROJECT_STATE 업데이트, 필요 ADR 작성
- [ ] 외부 결제·실거래·무단 크롤링 없이 종료

## E. Phase 1 인계 경계

Phase 0 종료 시 **실제 분봉/Cache/API Provider는 아직 없어도 정상**이다. 다음 Phase 1은 FakeMarketDataProvider로 fixture 1분봉을 응답하는 API부터 시작하고, Parquet/SQLite 캐시·CoverageManifest·시간대/세션·누락 복구를 순서대로 연결한다. 알파카/매시브·증권사 실제 연동은 capability/권한/권리 확인과 별도 승인을 거친다.
