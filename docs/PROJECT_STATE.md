# Project State — 실행 상태 및 미확정 결정

> 이 파일은 **진행 상황을 기록하는 동적 상태 파일**이다. AGENTS.md와 영구적 제품 명세에 임시 상태를 누적하지 않는다.

## 현재 상태 (2026-10-15 문서 대조 기준)

문서 탐색은 [문서 안내](README.md), 커밋별 누적 작업은 [구현 이력](IMPLEMENTATION_HISTORY.md)을 참고한다. 아래 기능 검증 기록은 별도 표시가 없으면 2026-10-12 실행 결과이며, 이번 문서 정리에서 재실행한 결과는 아니다.

- **Confirmed:** 미국 주식 중심 로컬 우선 워크스테이션. 차트별 독립 지표 및 선택 차트 프리셋 일괄 적용. 실제/수동/가상/백테스트 체결 분리. 1분봉 기본 저장과 소스 출처 무결성.
- **Decision:** Alpaca와 Massive는 동등한 _미선정_ 유료 후보이며 증권사 API/공개 정보 수집원과 병행 검증한다.
- **Runtime verified (2026-10-12):** macOS 15.7.9 x86_64, Python 3.10.19, uv 0.12.23, Node 22.23.2, pnpm 11.25.0, Tauri CLI 2.12.2. 2026-10-15에도 `command -v cargo`와 `command -v rustc`는 실행 파일을 찾지 못했다. 나머지 버전은 이번 정리에서 재검증하지 않았다.
- **Repository:** ZIP 배포 경로를 `git init -b main`으로 초기화하고 Phase 0 local baseline commit을 생성했다. 원격 저장소는 설정되지 않았고 CI 호스팅 실행은 아직 없다.
- **Phase 0 status:** P0-01~P0-07의 코드·계약·로컬 품질 게이트 완료. Tauri native dev/build만 `blocked: prerequisite (cargo missing)`이며 웹 셸은 실행 검증했다.
- **Open questions:** 원격 Git/CI 플랫폼, Rust toolchain 설치 시점, 실제 공급업체 계정 권한/저장 라이선스, 향후 예산. Phase 0은 외부 계정 연결을 요구하거나 수행하지 않았다.
- **Phase 1A status:** `FakeMarketDataProvider`가 합성 AAPL 1분봉을 canonical `MarketBarsResponse`와 `CoverageManifest`로 `GET /v1/market/bars`에 반환한다. 동일 요청은 `fake-aapl-20261009-r1`을 결정적으로 반환하며, 누락은 보간하지 않고 `partial/empty`와 명시적 누락 구간으로 노출한다.
- **Phase 1B status:** 명시적 `SIW_DATA_DIR`에서 SQLite migration·Coverage·수집 작업·revision pin·복구 journal과 content-addressed immutable Parquet cache가 동작한다. cache hit는 upstream을 다시 호출하지 않고, 중복 요청은 파일·Coverage를 늘리지 않는다.
- **Phase 1C status:** XNAS/XNYS 정규장 calendar와 `America/New_York` DST, 2026 휴장·반장, 버전된 extended-hours 정책, session-aligned 5m/15m/30m/60m 집계가 동작한다. 모든 bar는 quality와 source/calendar lineage를 보존한다.
- **Public boundary:** API 버전은 `v1`, 앱 버전은 `0.4.0-dev`다. `GET /v1/market/bars`는 `timeframe=1m`만 허용하며 큰 timeframe 집계는 내부 모듈에만 구현되어 있다. 실제 공급업체 사후 정정·재수집은 미구현이다.
- **Next action:** 사용자 승인 후 실제 Provider PoC 준비 또는 Phase 2 차트 수직 슬라이스 중 우선 경로를 정한다. 실제 Provider adapter·백테스트는 아직 시작하지 않는다.
- **Execution gate:** 사용자가 확인 없이 진행하도록 승인한 시간대·집계·품질 범위(Phase 1C)는 완료했다. 이미 승인한 범위 안의 하위 작업은 재승인을 요구하지 않는다. 그 밖의 후속 단계는 `EXECUTION_PROCESS.md`에 따라 다음 경로를 확인한 뒤 진행한다.

## Phase 0 acceptance evidence — 2026-10-12

- **Git/bootstrap:** `git init -b main`, `uv sync --all-groups`, `pnpm install --frozen-lockfile`; `uv.lock`과 `pnpm-lock.yaml` 생성.
- **API:** 실제 Uvicorn을 포트 8123에서 두 번 기동해 `GET /health`, `GET /version`, 구조화 404와 재시작 후 동일 계약을 확인했다. 로컬 포트 8000은 기존 PHP 서비스가 사용 중이어서 검증에 8123을 사용했다.
- **Collector:** `python -m services.collector doctor` exit 0, 없는 config exit 2, `run`의 SIGTERM 정상 종료 exit 0 확인. 외부 호출은 없었다.
- **Contracts:** JSON Schema Draft 2020-12 metaschema, 11 valid/6 invalid fixture, Python/TypeScript semantic validator와 생성 TypeScript 선언 검증.
- **Desktop:** `pnpm dev`가 `http://127.0.0.1:5173`에서 HTTP 200. 연결 성공·non-2xx·거부 후 복구·잘못된 payload 상태 테스트 통과. `pnpm build` 성공.
- **Quality:** `./scripts/verify.sh` 최종 통과 — Ruff format/lint, mypy, pytest 26개, Prettier, ESLint, TypeScript typecheck, Vitest 계약 17개/UI 4개, Vite production build.
- **CI:** `.github/workflows/ci.yml`이 동일 verify 스크립트를 실행하도록 구성했다. 원격 저장소가 없어 실제 GitHub Actions 실행은 **미실행**이다.
- **Tauri:** `tauri-cli 2.12.2` 확인. `pnpm desktop:tauri`는 `cargo metadata` 실행 파일 부재로 exit 1; native window/dev/build는 **미실행**이다.
- **Safety:** 시장 데이터·증권사·유료 API·계좌·주문·결제·크롤링 호출 없음. fixture는 합성 데이터이며 실제 키·계좌정보 없음.

## Phase 1A acceptance evidence — 2026-10-12

이 절의 한계는 Phase 1A 종료 당시 상태다. 영속 저장·거래소 시간 계산은 이후 Phase 1B/1C에서 구현했다.

- **Contracts:** `CoverageManifest`와 `MarketBarsResponse`를 JSON Schema 2020-12에 추가하고 Python/TypeScript semantic validator와 공통 valid/invalid fixture를 갱신했다.
- **Provider:** `MarketDataProvider` protocol 및 파일 기반 `FakeMarketDataProvider`를 추가했다. 1분봉·UTC 정렬·단일 pinned revision만 허용하며 중복 canonical identity를 fixture 로딩 시 거부한다.
- **API:** `GET /v1/market/bars`가 종목, UTC 범위, `1m`, 선택적 point-in-time cutoff를 검증한다. 알 수 없는 종목, 역전/비정렬/과도한 범위, 지원하지 않는 timeframe, 미래 cutoff는 구조화 오류로 거부한다.
- **Coverage:** fixture 결측과 cutoff 당시 미입수 bar를 서로 다른 reason으로 기록하며, OHLCV를 발명하거나 다른 source를 혼합하지 않는다.
- **Safety:** 네트워크 호출 차단 상태의 provider 단위 테스트를 통과했다. DB, Parquet, 외부 API, 계정, credential은 사용하지 않았다.
- **Actual API:** `uv run uvicorn services.api.main:app --host 127.0.0.1 --port 8124`로 기동해 합성 범위 요청 HTTP 200/4 bars/complete coverage와 미지원 종목 HTTP 404를 확인하고 정상 종료했다. 샌드박스 내부 최초 포트 bind는 권한 제한으로 실패해 승인된 로컬 실행으로 재검증했다.
- **Quality command:** `UV_CACHE_DIR=/private/tmp/siw-uv-cache ./scripts/verify.sh` 최종 통과 — Ruff/mypy, pytest 44개, Prettier/ESLint/TypeScript typecheck, Vitest 계약 20개/UI 4개, production build, 생성 타입 drift 검사 통과.
- **Remaining limits:** 현재 fixture는 AAPL 4분 합성 표본뿐이고 영속 저장·재시작 복구는 없다. 요청 분 수는 단순 1분 경계로 계산하며 거래소 휴장·DST·반장·세션별 예상 interval 계산은 Phase 1C 범위다. 실제 공급업체 품질·권한·저장권은 계속 `unverified`다.

## Phase 1B acceptance evidence — 2026-10-12

이 절의 시간대·캘린더 미구현 기록은 Phase 1B 종료 당시 상태이며 이후 Phase 1C에서 보완했다.

- **Migration/metadata:** 번호 기반 SQL migration으로 SQLite에 schema history, dataset revision, Coverage, acquisition job/checkpoint, revision pin, write journal을 생성한다. 재적용은 무변경이며 적용된 migration checksum 변경을 거부한다.
- **Parquet/atomicity:** PyArrow 25.0.1로 source/instrument/timeframe/UTC date/revision partition을 생성한다. canonical payload SHA-256 파일명, temp write, file/directory fsync, same-directory rename과 journal commit을 사용한다.
- **Recovery/idempotency:** 동일 요청 재저장은 Parquet·Coverage·journal 수를 늘리지 않는다. 실행 중 job은 재시작 후 checkpoint를 보존한 `interrupted`로 전환되고, rename 후 metadata commit 전 중단은 journal에서 복구된다. disk replace 실패는 temp/visible Coverage를 남기지 않는다.
- **Integrity/pinning:** cache read마다 Parquet payload hash와 canonical 계약을 재검증한다. revision pin은 재시작 후 유지되고 eviction guard를 차단하며, 같은 pin ID를 다른 revision에 재사용할 수 없다.
- **Actual API:** 임시 `SIW_DATA_DIR`로 Uvicorn을 포트 8125에서 기동해 HTTP 200 요청이 SQLite 1개/Parquet 1개/journal 1개를 생성함을 확인했다. 서버 재시작 후 응답 bytes가 동일했고 파일과 metadata 수가 증가하지 않았다.
- **Quality command:** `UV_CACHE_DIR=/private/tmp/siw-uv-cache ./scripts/verify.sh` 최종 통과 — Ruff/mypy, pytest 55개, Prettier/ESLint/TypeScript typecheck, Vitest 계약 20개/UI 4개, production build와 생성 타입 drift 검사 통과.
- **Safety/scope:** 실제 Provider·계정·credential·주문·결제·크롤링 호출 없음. SQLite 파일의 네트워크 공유, 자동 retention/GC, 거래소 캘린더와 세션별 예상 interval은 구현하지 않았다.

## Phase 1C acceptance evidence — 2026-10-12

- **Calendar/DST:** `exchange-calendars` 4.13.2의 XNAS→XNYS schedule과 `America/New_York`를 사용한다. 2026-03-06/03-09 DST 전후 정규장 UTC 이동, 2026-11-26 휴장, 11-27 13:00 ET 반장을 검증했다.
- **Sessions:** 버전된 정책으로 pre 04:00–09:30 ET, regular calendar interval, post full day 20:00 ET/early-close day 17:00 ET를 분리한다. bar가 경계를 넘거나 선언 session과 calendar가 충돌하면 거부한다.
- **Aggregation:** 1m→5m/15m/30m/60m를 각 session 시작에 정렬하고 세션을 섞지 않는다. 60m 마지막 30분 bucket과 반장 경계를 별도로 검증했다.
- **Quality/lineage:** `PriceBar`에 complete/partial/provisional/unknown quality와 source revision, expected/observed source count, calendar ID/version lineage를 추가했다. 결측은 partial, 미확정 source는 provisional이며 mixed revision·adjustment·source·calendar를 거부한다.
- **Coverage:** 기대 interval은 거래 가능한 pre/regular/post 분만 계산한다. 휴장 요청은 예상 0/empty Coverage로 남고 결측 bar를 발명하지 않는다.
- **Actual runtime:** 실제 Python 실행에서 2026-03-09 DST 이후 open `13:30Z`, 2026-11-27 반장 close `18:00Z`, 4/5개 source 분봉의 5m 결과 `partial`/expected 5/observed 4를 확인했다. 제한된 샌드박스에서는 PyArrow의 CPU cache `sysctlbyname` 조회 경고가 stderr에 나타났지만 exit 0과 계산 결과에는 영향이 없었다.
- **Quality command:** `UV_CACHE_DIR=/private/tmp/siw-uv-cache ./scripts/verify.sh` 최종 통과 — Ruff/mypy, pytest 70개, Prettier/ESLint/TypeScript typecheck, Vitest 계약 22개/UI 4개, production build와 생성 타입 drift 검사 통과.
- **Scope:** 실제 공급업체 entitlement와 extended-hours 제공 여부는 계속 미검증이다. 발표된 Nasdaq 23/5 일정은 현재 session 계약에 포함하지 않았으며 적용 시 새 정책·overnight 계약·검증이 필요하다.

## 문서 현행화 — 2026-10-15

- **변경:** `docs/README.md`, `docs/IMPLEMENTATION_HISTORY.md`를 추가하고 상태·진행 절차·로드맵·구조 문서 및 루트 README의 탐색 경로를 정리했다.
- **대조:** `git status --short`, `git log --oneline --decorate -8`, 단계별 `git show --stat`, `git remote -v`, API·calendar·aggregation 코드와 테스트 목록을 확인했다. 기준 커밋은 `1113ec2`, 원격 설정은 없으며 작업 시작 시 변경 파일도 없었다.
- **수정한 불일치:** 로드맵의 “코드 미구현” 문구, Phase 0 재착수 안내, 이미 승인한 Phase 1C 범위의 재승인 규칙, 정정 처리 완료로 오해할 수 있는 체크리스트를 수정했다.
- **검증 명령·결과:** `pnpm exec prettier --check README.md docs/README.md docs/IMPLEMENTATION_HISTORY.md docs/PROJECT_STATE.md docs/EXECUTION_PROCESS.md docs/IMPLEMENTATION_ROADMAP.md docs/REPOSITORY_STRUCTURE.md` 통과, `git diff --check` 통과. Node로 변경 문서 7개의 상대 링크 26개를 검사해 누락 0개를 확인했다.
- **미실행:** 기능 테스트·서버 재기동·Tauri native·원격 CI는 이번 문서 정리에서 **미실행**이다. 과거 테스트 수는 당시 기록으로 보존한다.
- **다음 작업:** Phase 2 합성 데이터 차트 또는 Phase 1D 실제 Provider PoC의 우선 경로 확정. 이번 변경은 문서 정리이며 새 기능 단계 착수를 의미하지 않는다.

## 검토 결함 수정 — 2026-10-16

- **변경 파일:** `services/api/main.py`, `services/market_data/{calendar,fake,storage}.py`, `packages/contracts/validator.py`, `tests/{api,contracts,market_data}/…`
- **500 응답:** `exchange-calendars`의 로드 범위(현재 날짜 기준 약 20년 전~1년 후) 밖 날짜는 `CalendarError`로 변환하고 Fake provider는 `422 invalid_market_data_query`로 반환한다. 저장소 오류는 `500 market_data_storage_error`, 그 밖의 예외는 `500 internal_error`로 내부 메시지 없이 `request_id` 오류 envelope과 `X-Request-ID` 헤더를 남긴다.
- **검증기 성능:** JSON Schema validator를 프로세스당 한 번만 컴파일한다(문서당 약 89ms → 무시할 수준, pytest 41s → 3s).
- **Revision 모호성:** 같은 요청에 여러 revision이 cache에 있으면 `CachedMarketDataProvider`는 임의 선택하지 않고 upstream이 명시한 revision을 가져와 그 revision으로 다시 읽는다. `read_exact`의 fail-closed 동작은 유지한다.
- **Event loop 블로킹:** `GET /v1/market/bars`를 sync endpoint로 바꿔 SQLite·Parquet·calendar 작업을 threadpool에서 실행한다.
- **검증:** 새 회귀 테스트 9개는 수정 전 코드에서 모두 실패하고 수정 후 통과함을 확인했다. `./scripts/verify.sh` 통과(exit 0) — Ruff/mypy, pytest 79개, Prettier/ESLint/typecheck, Vitest 계약 22개/UI 4개, build, 생성 타입 drift 검사. Tauri native·원격 CI는 **미실행**.
- **남은 위험:** 다중 프로세스 복구·미확정 bar 재사용 위험은 아래 항목에서 해소했다. TS 검증기는 가격 비교에 `Number`를 써 Python `Decimal`과 극단값에서 다를 수 있다. 모두 미수정.

## 데이터 안전성 위험 1~3 해소 — 2026-10-16

- **변경 파일:**
  - 코드: `services/market_data/storage.py`, `services/market_data/migrations/0002_store_owners.sql`, `packages/contracts/{validator.py,src/validator.ts}`
  - 테스트: `tests/market_data/test_storage.py`, `tests/fixtures/contracts/invalid/` 4개
  - 문서: `docs/decisions/0004-…`, `README.md`
- **다중 프로세스 복구:**
  - 각 store 인스턴스가 `owners/<owner_id>.lock`을 OS 잠금으로 보유하고, 이 owner를 journal과 job에 기록한다.
  - 복구는 owner가 종료된 작업만 처리하고, migration과 복구는 `maintenance.lock` 아래에서 직렬화한다.
  - 실패한 쓰기는 다른 프로세스가 참조했을 수 있는 content-addressed part를 지우지 않는다.
- **미확정 bar 재사용:** Coverage가 complete이고 모든 bar가 final일 때만 cache를 그대로 쓰고, 그 밖에는 upstream을 다시 조회한다.
- **Point-in-time 계약 (Python·TS 공통):**
  - final bar는 interval 종료 전에 공개될 수 없다.
  - 응답 bar는 요청 범위 안에 있어야 하고 시작 시각 기준으로 엄격히 정렬되어야 한다.
  - bar의 공개 시각이 cutoff 이후이면 거부한다.
- **검증:**
  - 새 테스트(Python 12개, TS 4개)는 이전 코드에서 모두 실패하고 수정 후 통과한다. 자식 프로세스를 실제로 kill하는 교차 프로세스 테스트도 포함한다.
  - `./scripts/verify.sh` 통과(exit 0) — Ruff/mypy, pytest 88개, Prettier/ESLint/typecheck, Vitest 계약 26개/UI 4개, build, 생성 타입 drift 검사.
- **미검증:** Windows 잠금 경로(`msvcrt.locking`), 네트워크 파일시스템(지원하지 않음), Tauri native, 원격 CI.
- **남은 위험:** 아직 job을 `succeeded`/`failed`로 끝내는 메서드가 없어서, Collector가 실제로 연결되면 job 종료 상태 전이를 정해야 한다.

## 남은 데이터 위험 4~8과 job 종료 처리 — 2026-10-16

- **변경 파일:**
  - 코드: `services/market_data/{provider,fake,storage,calendar,aggregation}.py`, `packages/contracts/src/validator.ts`
  - 테스트: `tests/market_data/test_{storage,calendar,aggregation,fake_provider}.py`, `packages/contracts/tests/contracts.test.ts`
  - fixture: calendar version 갱신, invalid fixture 1개 추가
  - 문서: ADR 0005, ADR 0003 상태, `README.md`
- **cache 전 query 검증 (4):** `validate_bars_query`가 cache 조회보다 먼저 UTC offset, 초 단위 정밀도, `end > start`를 검사한다. 소수점 초 `as_of_utc`가 cache key에서 잘려 cutoff 이후에 공개된 bar를 내주던 경로도 막는다.
- **calendar 버전과 범위 (5, 6):**
  - 지원 범위를 2000-01-03~2030-12-31로 고정했다.
  - `calendar_version`은 그 범위 일정의 SHA-256 fingerprint(`xnys-regular-…-sha256-2c7e1ffbb903c8b8`)다. 일정이 바뀌면 fixture guard 테스트가 실패한다.
  - calendar는 프로세스당 한 번만 불러온다.
- **공개 시각 비교 (7):** 집계 bar의 `available_at_utc`를 문자열이 아니라 시각으로 비교해 고른다.
- **TS decimal 비교 (8):** `compareDecimal`로 Python `Decimal`과 같은 정확한 비교를 한다.
- **job 종료:**
  - `complete_job`은 `succeeded`(종료 상태)로, `fail_job`은 짧은 코드와 함께 `failed`로 바꾼다. `failed` job은 `start_job`으로 재시도할 수 있다.
  - checkpoint와 종료는 job을 실행 중인 owner store만 할 수 있다.
- **검증:**
  - 새 동작 테스트(Python 6개, TS 8개)가 이전 코드에서 실패한다. calendar·aggregation 새 테스트는 이전 코드에서 import 단계부터 실패한다. 수정 후에는 모두 통과한다.
  - `./scripts/verify.sh` 통과(exit 0) — Ruff/mypy, pytest 98개, Prettier/ESLint/typecheck, Vitest 계약 34개/UI 4개, build, 생성 타입 drift 검사.
- **영향:** 이 변경 전에 만든 로컬 cache는 lineage에 `exchange-calendars-4.13.2`가 남아 있어 새 bar와 집계되지 않는다. 다시 만들어야 한다.
- **미검증:** Windows 잠금 경로, Tauri native, 원격 CI.

## 상태 변경 양식

- 날짜 / 작업 번호 / 변경 파일:
- 실제 검증 명령과 결과:
- 새로 확정된 사항과 근거:
- 변경한 가정 또는 남은 문제:
- 다음 작업:
