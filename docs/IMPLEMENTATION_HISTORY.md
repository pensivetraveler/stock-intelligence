# 구현 및 작업 이력

- **정리일:** 2026-10-15
- **대조 기준:** 현재 코드, 테스트 파일, Git 이력, [PROJECT_STATE.md](PROJECT_STATE.md)의 당시 검증 기록
- **구현 기준 커밋:** `1113ec2` (`main`)
- **읽는 방법:** 아래 테스트 수와 서버 기동 결과는 2026-10-12의 기록이다. 2026-10-15 문서 정리에서 기능 테스트나 실제 서버 기동을 다시 수행한 결과로 해석하지 않는다.

## 1. 누적 작업 요약

| 단계                     | 커밋           | 주요 결과                                                           | 당시 로컬 검증                              | 상태                                |
| ------------------------ | -------------- | ------------------------------------------------------------------- | ------------------------------------------- | ----------------------------------- |
| 문서 정비·Phase 0 기준선 | `465c396`      | 요구사항·안전 규칙·실행 명세, API·웹 셸·Collector·공통 계약·CI 구성 | Python 26, TS 계약 17, UI 4; 전체 검증 통과 | 로컬 기반 완료, Tauri native 미검증 |
| Phase 1A                 | `4b2b6a1`      | FakeMarketDataProvider, 1m API, Coverage, cutoff                    | Python 44, TS 계약 20, UI 4; 전체 검증 통과 | 완료                                |
| Phase 1B                 | `94aa4c2`      | SQLite metadata와 immutable Parquet cache, 중단 복구·pin            | Python 55, TS 계약 20, UI 4; 전체 검증 통과 | 완료                                |
| Phase 1C                 | `1113ec2`      | 거래 캘린더·DST·세션·분봉 집계·quality/lineage                      | Python 70, TS 계약 22, UI 4; 전체 검증 통과 | 승인된 내부 구현 범위 완료          |
| 문서 현행화              | 이번 문서 변경 | 문서 색인·누적 이력 추가, 상태·로드맵·진행 규칙 정합성 수정         | 문서 포맷·diff 검사; 기능 테스트 미실행     | 2026-10-15                          |

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

**현재 한계:** 공개 API의 `timeframe`은 `1m`만 허용한다. 큰 timeframe은 내부 집계 모듈에만 구현되어 있다. 공급업체 사후 정정 수집·reconciliation과 Nasdaq overnight/23시간 세션은 미구현이다. 리비전 보존·혼합 거부를 실제 정정 처리 완료로 해석하지 않는다. 이전 schema의 로컬 합성 cache는 자동 변환하지 않는다([ADR 0003](decisions/0003-us-equity-calendar-and-aggregation.md)).

## 6. 현재 가능한 사용과 남은 작업

| 항목                                 | 현재 범위                           | 다음에 필요한 작업                                    |
| ------------------------------------ | ----------------------------------- | ----------------------------------------------------- |
| 데이터 조회                          | 합성 AAPL 1m API, 선택적 로컬 cache | 다종목·충분한 fixture, 실제 Provider는 권한·권리 검증 |
| 큰 timeframe                         | 내부 집계 함수                      | API 계약 확장과 응답·Coverage 통합 검증               |
| 화면                                 | API 상태를 표시하는 웹 개발 셸      | 차트 렌더링, 종목/timeframe 선택, workspace           |
| 네이티브 앱                          | Tauri 구성                          | Rust/Cargo 등 prerequisite 설치 후 실행·빌드 검증     |
| 데이터 운영                          | 로컬 저장·중단 복구·pin             | 실제 backfill/retry/throttle, 정정 처리, retention    |
| 원격 CI                              | workflow 파일                       | Git 원격·CI 실행 환경 구성 및 실행 증거               |
| 매매 복기·지표·Replay·백테스트·Agent | 초기 계약 또는 장기 요구사항        | 해당 Phase 구현과 별도 수락 검증                      |

후속 선택지는 **Phase 2의 합성 데이터 기반 차트 수직 슬라이스** 또는 **Phase 1D 실제 Provider PoC**다. Phase 2를 선택하면 첫 차트에서 source·session·quality·Coverage를 표시하고, 큰 timeframe API 연결은 명시적인 하위 작업으로 포함할 수 있다. Phase 1D는 사용할 계정·읽기 전용 권한·데이터 저장권·비용 범위 확인이 선행된다. 두 경로 모두 아직 착수하지 않았다.

## 7. 이력 확인과 갱신

```bash
git log --oneline --decorate
git show --stat 465c396
git show --stat 4b2b6a1
git show --stat 94aa4c2
git show --stat 1113ec2
./scripts/verify.sh
```

`git show`는 각 단계의 전체 변경 파일을 확인하는 명령이고, `verify.sh`는 현재 체크아웃의 기능을 다시 검증하는 명령이다. 새 작업마다 날짜·범위·주요 파일·실제 실행 명령과 결과·미실행 항목·남은 제약을 기록한다. 커밋하지 않은 작업에는 임의의 커밋 ID를 붙이지 않는다.
