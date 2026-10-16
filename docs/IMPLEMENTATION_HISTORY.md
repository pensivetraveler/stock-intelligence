# 구현 및 작업 이력

- **정리일:** 2026-10-15
- **대조 기준:** 현재 코드, 테스트 파일, Git 이력, [PROJECT_STATE.md](PROJECT_STATE.md)의 당시 검증 기록
- **구현 기준 커밋:** `ef2dbac` (`main`)
- **읽는 방법:** 아래 테스트 수와 서버 기동 결과는 2026-10-12의 기록이다. 2026-10-15 문서 정리에서 기능 테스트나 실제 서버 기동을 다시 수행한 결과로 해석하지 않는다.

## 1. 누적 작업 요약

| 단계                      | 커밋           | 주요 결과                                                              | 당시 로컬 검증                               | 상태                                |
| ------------------------- | -------------- | ---------------------------------------------------------------------- | -------------------------------------------- | ----------------------------------- |
| 문서 정비·Phase 0 기준선  | `465c396`      | 요구사항·안전 규칙·실행 명세, API·웹 셸·Collector·공통 계약·CI 구성    | Python 26, TS 계약 17, UI 4; 전체 검증 통과  | 로컬 기반 완료, Tauri native 미검증 |
| Phase 1A                  | `4b2b6a1`      | FakeMarketDataProvider, 1m API, Coverage, cutoff                       | Python 44, TS 계약 20, UI 4; 전체 검증 통과  | 완료                                |
| Phase 1B                  | `94aa4c2`      | SQLite metadata와 immutable Parquet cache, 중단 복구·pin               | Python 55, TS 계약 20, UI 4; 전체 검증 통과  | 완료                                |
| Phase 1C                  | `1113ec2`      | 거래 캘린더·DST·세션·분봉 집계·quality/lineage                         | Python 70, TS 계약 22, UI 4; 전체 검증 통과  | 승인된 내부 구현 범위 완료          |
| 문서 현행화               | 이번 문서 변경 | 문서 색인·누적 이력 추가, 상태·로드맵·진행 규칙 정합성 수정            | 문서 포맷·diff 검사; 기능 테스트 미실행      | 2026-10-15                          |
| 검토 결함·데이터 안전성   | `980e973`      | API 오류 envelope, 검증기 캐시, 다중 프로세스 복구, point-in-time 계약 | Python 88, TS 계약 26, UI 4; 전체 검증 통과  | 완료                                |
| 남은 데이터 위험·job 종료 | `fc96cc3`      | cache 전 query 검증, calendar fingerprint, TS decimal, job 종료        | Python 98, TS 계약 34, UI 4; 전체 검증 통과  | 완료                                |
| 운영 보강                 | `ef2dbac`      | 세션 날짜 partition, 연결 close, API lazy 시작, request ID, 단일 버전  | Python 110, TS 계약 34, UI 6; 전체 검증 통과 | 2026-10-16                          |
| Phase 1 내부 마무리(1E)   | `a3c8141`      | 공개 5m~60m 집계 API, cache 범위 조합, 재개 가능한 backfill            | Python 134, TS 계약 35, UI 6; 전체 검증 통과 | 2026-10-16                          |
| Phase 2A 차트 workspace   | `a3c8141`      | 차트 2개·독립 설정·동기화·레이아웃 저장, 합성 SYNTH, 종목·세션 API     | 전체 검증·브라우저 확인                      | 2026-10-16                          |
| Phase 2B·3A               | 커밋 전        | 이름 붙인 workspace·템플릿, 수동·가상 거래 원장과 P&L                  | 전체 검증·브라우저 확인                      | 2026-10-16                          |

Phase 1 전체가 완료된 것은 아니다. 실제 데이터 수집·사후 정정·재시도·공급업체 비교 등 장기 로드맵의 나머지 요구사항은 후속 작업이다.

## 2. 문서 정비와 Phase 0

초기 Markdown 검토와 권장사항 반영을 통해 전체 요구사항, 실행 명세, 단계별 진행 절차와 ADR을 기준선에 포함했다. Alpaca와 Massive는 동등한 미선정 유료 후보로 유지하고, 문서상 지원과 실제 계정의 권한·연동 성공을 구분했다. 개별 사전 문서 수정은 별도 커밋이 없으므로 `465c396`의 기준선에 포함된 것으로 기록한다.

- FastAPI `/health`, `/version`, 구조화된 오류와 request ID를 구현했다.
- React/TypeScript 웹 개발 셸에 API 연결 상태·실패·복구를 표시했다. Tauri 2 구성은 추가했으나 native 실행은 검증하지 못했다.
- Collector를 별도 CLI 프로세스로 구성하고 진단·설정 오류·종료 경로를 확인했다.
- JSON Schema Draft 2020-12를 공통 계약으로 정하고 Python/TypeScript validator, 공통 fixture, 생성 타입을 연결했다.
- 실제 증권사 체결·수동 체결·가상 시나리오·백테스트 체결 계약을 구분하고 Decimal string, UTC, OHLC·시간 관계를 검증했다.
- 포맷·린트·타입·테스트·빌드 명령과 CI workflow를 구성했다.

**주요 파일:** `services/api/main.py`, `services/collector/cli.py`, `apps/desktop/src/App.tsx`, `packages/contracts/schemas/contracts.schema.json`, `packages/contracts/validator.py`, `packages/contracts/src/validator.ts`, `scripts/verify.sh`, `.github/workflows/ci.yml`.

**당시 실행 근거:** `./scripts/verify.sh` 통과, 포트 8123의 실제 API 기동·재시작, `pnpm dev`의 HTTP 200, Collector doctor exit 0·잘못된 config exit 2·SIGTERM 종료 exit 0. `pnpm desktop:tauri`는 Cargo 부재로 실패했다. 원격 CI는 미실행이었다.

**결정:** [ADR 0001](decisions/0001-phase-0-toolchain-and-contract-source.md).

## 3. Phase 1A — 합성 시장 데이터 경로

- `MarketDataProvider` protocol과 파일 기반 `FakeMarketDataProvider`를 추가했다.
- 합성 AAPL 4개 1분봉을 `GET /v1/market/bars`로 반환한다. `MarketBarsResponse`와 `CoverageManifest`를 공통 계약에 추가했다.
- 동일 요청의 결정적 revision, 결측 구간, cutoff 당시 미입수 데이터, 잘못된 종목·기간·timeframe·중복을 검증했다.
- 외부 네트워크 호출이 없는 provider 테스트를 추가했다.

**주요 파일:** `services/market_data/provider.py`, `services/market_data/fake.py`, `tests/fixtures/market_data/fake_aapl_1m_r1.json`, `tests/market_data/test_fake_provider.py`, `tests/api/test_api.py`, 공통 계약·fixture.

**당시 실행 근거:** `UV_CACHE_DIR=/private/tmp/siw-uv-cache ./scripts/verify.sh` 통과. 포트 8124에서 합성 범위 HTTP 200·4 bars·complete Coverage 및 미지원 종목 HTTP 404 확인.

**인계 당시 한계:** 영속 저장과 거래소 캘린더는 미구현이었다. 이후 Phase 1B/1C에서 보완했다. 실제 시세와 다종목 데이터는 현재도 제공하지 않는다.

## 4. Phase 1B — 로컬 저장과 복구

- `SIW_DATA_DIR`가 지정된 경우에만 cache-first wrapper를 사용한다.
- SQLite에 번호 기반 migration, revision, Coverage, 수집 작업/checkpoint, revision pin, write journal을 저장한다.
- Parquet는 canonical payload의 SHA-256 파일명과 source/instrument/timeframe/UTC date/revision partition으로 저장한다.
- 임시 파일·fsync·atomic rename·journal을 이용해 중단 후 복구한다. 재조회 시 hash와 계약을 검사한다.
- 동일 요청 중복 저장 방지, 재시작, interrupted job, rename 이후 metadata commit 전 중단, disk replace 실패, pin 유지와 eviction guard를 시험했다.

**주요 파일:** `services/market_data/storage.py`, `services/market_data/migrations/0001_local_market_data.sql`, `tests/market_data/test_storage.py`, API cache 설정, `pyproject.toml`, `uv.lock`.

**당시 실행 근거:** 전체 검증 명령 통과. 임시 `SIW_DATA_DIR`와 포트 8125에서 SQLite 1개·Parquet 1개·journal 1개를 생성하고, 재시작 후 동일 응답 bytes 및 파일·metadata 수 불변 확인.

**현재 한계:** 자동 retention/GC와 실제 backfill scheduler는 미구현이다. SQLite 파일의 네트워크 공유는 지원하지 않는다. 작업/checkpoint 저장 구조가 있다는 사실은 외부 수집기가 구현되었다는 뜻이 아니다.

**결정:** [ADR 0002](decisions/0002-local-market-data-storage.md).

## 5. Phase 1C — 시간대·세션·집계·품질

- `exchange-calendars` 기반 XNAS→XNYS 일정과 `America/New_York`로 DST·휴장·반장을 처리한다.
- 버전된 extended-hours 정책은 pre 04:00–09:30 ET, 정규장 calendar 구간, post 일반일 20:00 ET/반장 17:00 ET까지다. 이는 애플리케이션 분류 정책이며 실제 feed 제공 권한을 보증하지 않는다.
- 1m→5m/15m/30m/60m를 세션 시작 기준으로 집계한다. 세션을 혼합하지 않으며 마지막 bucket은 세션 종료에서 자른다.
- Decimal OHLCV, source revision, 기대/관측 분봉 수, calendar ID/version을 보존한다. 입력에 미확정 봉이 있으면 `provisional`, 미확정 봉 없이 결측 또는 불완전 입력이 있으면 `partial`, 전체 기대 분봉이 complete이면 `complete`가 된다.
- 중복 분봉과 서로 다른 source/feed/volume unit/adjustment/revision/calendar 입력을 거부한다. cutoff 이후 입수된 데이터는 집계에서 제외한다.
- 거래 가능한 분만 Coverage의 기대 interval로 계산하고, 휴장일은 expected 0·empty로 처리한다.

**주요 파일:** `services/market_data/calendar.py`, `services/market_data/aggregation.py`, `tests/market_data/test_calendar.py`, `tests/market_data/test_aggregation.py`, `PriceBar` schema·양쪽 validator·fixture·Parquet lineage 필드.

**당시 실행 근거:** 전체 검증 명령 통과. 실제 Python 실행으로 2026-03-09 정규장 open `13:30Z`, 2026-11-27 반장 close `18:00Z`, 4/5 source 분봉의 집계 `partial`을 확인했다.

**1C 당시 한계:** 공개 API의 `timeframe`은 `1m`만 허용했고 큰 timeframe은 내부 집계 모듈에만 있었다(공개 API 연결은 1E에서 완료). 공급업체 사후 정정 수집·reconciliation과 Nasdaq overnight/23시간 세션은 미구현이다. 리비전 보존·혼합 거부를 실제 정정 처리 완료로 해석하지 않는다. 이전 schema의 로컬 합성 cache는 자동 변환하지 않는다([ADR 0003](decisions/0003-us-equity-calendar-and-aggregation.md)).

## 6. 검토 후속 보강 — 2026-10-16

전체 코드 검토에서 찾은 결함과 위험을 세 단위로 처리했다. 각 단위의 새 테스트는 이전 코드에서 실패하고 수정 후 통과함을 확인했다.

- **`980e973`:**
  - calendar 범위 밖 날짜는 422로, 저장소·예외 오류는 request ID가 있는 500 envelope으로 반환한다.
  - JSON Schema 검증기를 한 번만 컴파일한다.
  - cache에 revision이 여러 개면 upstream이 명시한 revision을 쓴다.
  - bars endpoint를 threadpool에서 실행한다.
  - owner lock으로 다중 프로세스 복구를 분리했다(migration `0002`).
  - 미확정 bar가 있는 cache는 다시 조회한다.
  - 응답 범위·정렬·cutoff와 final bar 공개 시각 계약을 추가했다([ADR 0004](decisions/0004-multi-process-store-and-point-in-time-contract.md)).
- **`fc96cc3`:**
  - cache 조회 전에 query를 검증해 소수점 초 cutoff 누출을 막았다.
  - calendar 범위를 2000-01-03~2030-12-31로 고정하고, 일정 SHA-256 fingerprint를 `calendar_version`으로 쓴다.
  - 집계 공개 시각을 시각 기준으로 비교한다.
  - TS 검증기가 decimal을 정확히 비교한다.
  - job `succeeded`/`failed` 종료와 owner 제한을 추가했다([ADR 0005](decisions/0005-calendar-fingerprint-query-boundary-and-job-lifecycle.md)).
- **운영 보강 (`ef2dbac`):**
  - Parquet `session_date` partition, SQLite 연결 close, 실패한 복구의 임시 파일 정리
  - API provider lazy 생성과 lifespan warm-up, 합성 fixture를 `services/market_data/fixtures/`로 이동
  - `X-Request-ID` 형식 검증
  - 데스크톱 health loop 세대 구분
  - 단일 제품 버전 `0.4.0-dev`와 일치 검사
  - `verify.sh`의 corepack fallback, AGENTS.md 현재 범위 정리
  - 근거: [ADR 0006](decisions/0006-session-partitions-api-startup-and-single-version.md)

**실행 근거:** 각 단위에서 `./scripts/verify.sh` 통과.

**미실행:** Windows 잠금 경로, Tauri native, 원격 CI.

**영향:** 이 변경 전에 만든 로컬 cache는 옛 calendar version lineage 때문에 새 bar와 집계되지 않는다. 기존 `date=` partition의 Parquet는 읽을 수 있지만, 새 쓰기는 `session_date=` 경로를 쓴다.

**Phase 1 내부 마무리(1E, `a3c8141`):**

- `MarketBarsService`가 공개 API에서 5m/15m/30m/60m을 세션 bucket 기준으로 집계한다.
- 결측 없는 cache 결과 여러 개로 다른 요청 범위에 응답한다.
- Collector `backfill`이 job·chunk·checkpoint·재개·재시도·throttle을 제공한다.
- 근거: [ADR 0007](decisions/0007-aggregated-api-cache-composition-and-backfill.md)

**Phase 2A 차트 workspace(`a3c8141`):**

- 독립 설정 차트 2개, 시간 범위 동기화, 레이아웃 저장·복원
- Coverage·품질·lineage·결측의 ET 표시
- 합성 `SYNTH` 하루치와 종목·세션 API
- 브라우저 검증에서 집계 순서·500 CORS·모바일 viewport 결함을 찾아 고쳤다([ADR 0008](decisions/0008-chart-workspace-slice.md)).

**Phase 2B·3A(커밋 전):**

- 이름 붙인 workspace, Terminal/Review 템플릿과 되돌리기([ADR 0010](decisions/0010-named-workspaces-and-layout-templates.md))
- 캔들 클릭으로 기록하는 수동(실제)·가상 거래 원장과 서로 분리된 P&L, 차트 마커([ADR 0009](decisions/0009-manual-and-hypothetical-trade-ledgers.md))
- 브라우저 검증에서 bars 요청이 4초 걸리는 문제를 찾았다. 계층마다 중복되던 검증을 제거하고 `$ref`를 인라인해 0.5~0.7초로 줄였다.

## 7. 현재 가능한 사용과 남은 작업

| 항목                       | 현재 범위                            | 다음에 필요한 작업                                    |
| -------------------------- | ------------------------------------ | ----------------------------------------------------- |
| 데이터 조회                | 합성 AAPL 1m API, 선택적 로컬 cache  | 다종목·충분한 fixture, 실제 Provider는 권한·권리 검증 |
| 큰 timeframe               | 공개 API 5m~60m 집계                 | 일봉(`1d`)과 차트 연결                                |
| 화면                       | 차트 2개, 이름 붙인 workspace·템플릿 | 지표(Phase 4), replay                                 |
| 네이티브 앱                | Tauri 구성                           | Rust/Cargo 등 prerequisite 설치 후 실행·빌드 검증     |
| 데이터 운영                | 로컬 저장·복구·pin, 합성 backfill    | 실제 Provider 수집, 정정 처리, retention              |
| 원격 CI                    | workflow 파일                        | Git 원격·CI 실행 환경 구성 및 실행 증거               |
| 매매 복기                  | 수동·가상 원장, 마커, 분리 P&L       | CSV 가져오기·중복 검토, 정정, broker 확인, 계좌 구분  |
| 지표·Replay·백테스트·Agent | 초기 계약 또는 장기 요구사항         | 해당 Phase 구현과 별도 수락 검증                      |

후속 선택지는 **Phase 2의 합성 데이터 기반 차트 수직 슬라이스** 또는 **Phase 1D 실제 Provider PoC**다. Phase 2를 선택하면 첫 차트에서 source·session·quality·Coverage를 표시하며, 이미 공개된 5m~60m 집계 API를 사용할 수 있다. Phase 1D는 사용할 계정·읽기 전용 권한·데이터 저장권·비용 범위 확인이 선행된다. 두 경로 모두 아직 착수하지 않았다.

## 8. 이력 확인과 갱신

```bash
git log --oneline --decorate
git show --stat 465c396
git show --stat 4b2b6a1
git show --stat 94aa4c2
git show --stat 1113ec2
git show --stat 980e973
git show --stat fc96cc3
git show --stat ef2dbac
./scripts/verify.sh
```

`git show`는 각 단계의 전체 변경 파일을 확인하는 명령이고, `verify.sh`는 현재 체크아웃의 기능을 다시 검증하는 명령이다. 새 작업마다 날짜·범위·주요 파일·실제 실행 명령과 결과·미실행 항목·남은 제약을 기록한다. 커밋하지 않은 작업에는 임의의 커밋 ID를 붙이지 않는다.
