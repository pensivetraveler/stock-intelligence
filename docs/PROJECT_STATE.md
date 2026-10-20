# Project State — 실행 상태 및 미확정 결정

> 이 파일은 **진행 상황을 기록하는 동적 상태 파일**이다. AGENTS.md와 영구적 제품 명세에 임시 상태를 누적하지 않는다.

## 현재 상태 (2026-10-15 문서 대조 기준)

문서 탐색은 [문서 안내](README.md), 커밋별 누적 작업은 [구현 이력](IMPLEMENTATION_HISTORY.md)을 참고한다. 아래 기능 검증 기록은 별도 표시가 없으면 2026-10-12 실행 결과이며, 이번 문서 정리에서 재실행한 결과는 아니다.

- **Confirmed:** 미국 주식 중심 로컬 우선 워크스테이션. 차트별 독립 지표 및 선택 차트 프리셋 일괄 적용. 실제/수동/가상/백테스트 체결 분리. 1분봉 기본 저장과 소스 출처 무결성.
- **Decision:** Alpaca와 Massive는 동등한 _미선정_ 유료 후보이며 증권사 API/공개 정보 수집원과 병행 검증한다.
- **Candidate register (2026-10-19):** 사용자가 지정한 13곳(Webull, Massive, Databento, Alpaca, 토스·키움·한국투자·메리츠·DB·LS·KB증권, ChartExchange, Unusual Whales)을 [공급업체 평가 8절](DATA_PROVIDER_EVALUATION_v1.5.md#8-후보-등록부-2026-10-19-사용자-등록)에 후보로 등록했다. 등록은 선정·결제·구현 승인이 아니며, 구현된 채널은 fake·Alpaca·KIS·토스·키움·메리츠뿐이다.
- **Runtime verified (2026-10-12):** macOS 15.7.9 x86_64, Python 3.10.19, uv 0.12.23, Node 22.23.2, pnpm 11.25.0, Tauri CLI 2.12.2. 2026-10-15에도 `command -v cargo`와 `command -v rustc`는 실행 파일을 찾지 못했다. 나머지 버전은 이번 정리에서 재검증하지 않았다.
- **Repository:** ZIP 배포 경로를 `git init -b main`으로 초기화하고 Phase 0 local baseline commit을 생성했다. 2026-10-19 원격 `origin`(https://github.com/pensivetraveler/stock-intelligence)을 연결하고 `main`을 푸시해 upstream으로 설정했다. 같은 날 GitHub에서 `main` 보호 규칙을 켰다: PR 필수(승인 0명), `verify` 검사 통과와 최신 상태 필수, 직선 이력, 강제 푸시·삭제 금지. 저장소는 공개 상태다.
- **Phase 0 status:** P0-01~P0-07의 코드·계약·로컬 품질 게이트 완료. Tauri native dev/build만 `blocked: prerequisite (cargo missing)`이며 웹 셸은 실행 검증했다.
- **Open questions:** Rust toolchain 설치 시점, 실제 공급업체 계정 권한/저장 라이선스, 향후 예산. Phase 0은 외부 계정 연결을 요구하거나 수행하지 않았다.
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
- **CI:** `.github/workflows/ci.yml`이 동일 verify 스크립트를 실행하도록 구성했다. 2026-10-19 첫 GitHub Actions 실행(run 39173054570)은 `pnpm-workspace.yaml`의 `allowBuilds.esbuild`가 pnpm 자리표시 값으로 남아 `ERR_PNPM_IGNORED_BUILDS`로 실패했다. 값을 `true`로 고쳤다.
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

## 남은 운영 위험 9~17 정리 — 2026-10-16

- **변경 파일:**
  - 코드: `services/market_data/{storage,fake}.py`, `services/api/main.py`, `apps/desktop/src/App.tsx`
  - fixture: `services/market_data/fixtures/fake_aapl_1m_r1.json`(이동)
  - 스크립트와 버전 파일: `scripts/verify.sh`, `package.json`, `apps/desktop/package.json`, `tauri.conf.json`, `Cargo.toml`, `pyproject.toml`
  - 테스트: `tests/{api,market_data}/…`, `tests/test_versions.py`, `apps/desktop/src/App.test.tsx`
  - 문서: ADR 0006, ADR 0002 상태, `AGENTS.md`, `README.md`, `IMPLEMENTATION_HISTORY.md`
- **저장소:**
  - Parquet를 `America/New_York` 세션 날짜(`session_date=`)로 나눈다. 기존 `date=` part는 Coverage의 경로로 계속 읽는다.
  - 모든 SQLite 연결을 닫는다.
  - 복구에 실패한 journal은 자기 임시 파일만 지운다.
- **API:**
  - import 시 저장소를 열지 않는다. provider는 첫 사용 시 만들고, lifespan에서 미리 연다.
  - 합성 fixture는 `services/` 아래로 옮겼다.
  - `X-Request-ID`는 `[A-Za-z0-9._:-]{1,128}`일 때만 되돌려 주고, 아니면 새 UUID를 쓴다.
- **데스크톱:** 엔드포인트가 바뀌거나 unmount된 뒤 끝난 이전 health check는 화면이나 polling에 반영하지 않는다. 버전은 `package.json`에서 읽는다.
- **버전:** 제품 버전을 `0.4.0-dev`(PEP 440 `0.4.0.dev0`)로 통일했고, 여러 파일의 버전이 어긋나면 테스트가 실패한다.
- **환경:** `pnpm`이 PATH에 없으면 `verify.sh`가 임시 corepack shim을 쓴다.
- **AGENTS.md:** "Phase 0만 구현" 지시를 현재 상태 문서와 Gate를 따르는 범위 규칙으로 바꿨다.
- **검증:**
  - 새 테스트(Python 12개, 데스크톱 2개)는 이전 코드에서 실패하고 수정 후 통과한다.
  - `pnpm`이 PATH에 없는 셸에서 `./scripts/verify.sh` 통과(exit 0) — Ruff/mypy, pytest 110개, Prettier/ESLint/typecheck, Vitest 계약 34개/UI 6개, build, 생성 타입 drift 검사. `uv lock --check` 일치(버전 줄만 변경).
- **미검증:** Windows 잠금 경로, Tauri native(`0.4.0-dev` prerelease 버전의 Windows MSI 호환 포함), 원격 CI.

## Phase 1 내부 마무리(1E) — 2026-10-16

- **변경 파일:**
  - 코드: `services/market_data/{bars_service,storage,aggregation,calendar,fake}.py`, `services/collector/{backfill,cli}.py`, `services/api/main.py`
  - 계약: `packages/contracts/schemas/contracts.schema.json`, `generated/contracts.d.ts`
  - 테스트: `tests/conftest.py`, `tests/market_data/test_bars_service.py`, `tests/collector/test_backfill.py`, API·storage 테스트, 집계 응답 계약 fixture
  - 문서: ADR 0007, ADR 0002 상태, `README.md`, `EXECUTION_PROCESS.md`, `REPOSITORY_STRUCTURE.md`
- **공개 집계 API:**
  - `timeframe=5m|15m|30m|60m`. 요청마다 같은 cache·cutoff의 1분봉을 집계하고 저장하지 않는다.
  - 범위는 세션 bucket 경계에 맞아야 하며, 맞지 않으면 422다.
  - Coverage는 bar가 있는 bucket을 센다. 일부만 관측된 bucket은 `partial` bar로 표시한다.
  - 계약의 `CoverageManifest.timeframe`을 넓혔다(추가 변경이라 `schema_version` 0.1 유지).
- **cache 조합:** 같은 source·revision·cutoff를 가진, 결측 없는 cache 결과 여러 개가 요청 범위를 덮고 bar가 모두 final이면 upstream 없이 응답한다. 그렇지 않으면 upstream을 조회한다(결측 재수집).
- **Backfill:**
  - 범위 하나가 job 하나이고, chunk마다 checkpoint를 남긴다. 성공한 범위를 다시 실행하면 아무 작업도 하지 않는다.
  - 중단·실패한 job은 이어서 실행한다. 일시적 오류만 backoff로 재시도하고, 호출 간격을 둔다.
  - 종료 코드는 0/1/2/3이다.
- **검증:**
  - `./scripts/verify.sh` 통과(exit 0) — pytest 134개, Vitest 계약 35개·UI 6개, 생성 타입 drift 검사 포함.
  - 조합 테스트는 이전 storage 코드에서 실패한다. 새 모듈 테스트는 이전 코드에서 import 단계부터 실패한다.
  - 실제 런타임:
    - CLI backfill subprocess: 2 chunk 성공, 재실행 `already_succeeded`
    - uvicorn: 1m 200 complete, 5m 200 partial, 정렬 안 된 15m 422, 1990 422, 안전하지 않은 request ID 교체
    - 데이터 디렉터리: `session_date=` Parquet 1개
- **남은 범위:** 실제 Provider adapter·rate limit·entitlement(1D), 일봉(`1d`), manifest가 많을 때의 조합 성능, Windows·Tauri native·원격 CI 미검증.

## Phase 2A 차트 workspace — 2026-10-16

- **변경 파일:**
  - 백엔드: `services/market_data/{synthetic,fake,calendar,aggregation,storage,provider}.py`, `services/api/main.py`, `services/collector/backfill.py`, `services/market_data/fixtures/fake_aapl_1m_r1.json`
  - 계약: `packages/contracts/{schemas,generated,validator.py,src/validator.ts,package.json}`, 계약 fixture 6개
  - 데스크톱: `apps/desktop/{index.html,package.json}`, `src/{App,Workspace,ChartPanel,PriceChart}.tsx`, `src/{market-api,workspace-state,chart-data,chart-sync}.ts`, 테스트 5개, `styles.css`
  - 기타: `pnpm-lock.yaml`, `.claude/launch.json`(로컬 미리보기 서버 정의)
  - 문서: ADR 0008, README, EXECUTION_PROCESS, ROADMAP, REPOSITORY_STRUCTURE
- **기능:**
  - 차트 2개가 종목·timeframe·날짜·정규/연장장을 각자 설정한다.
  - 보이는 시간 범위를 동기화할 수 있고, 레이아웃을 저장·복원한다.
  - 불확실한 bar는 회색으로 그리고, Coverage·품질·세션·lineage·결측을 ET로 표시한다.
- **API:** `GET /v1/market/instruments`, `GET /v1/market/sessions/{date}`(읽기 전용, 공통 계약 `market_instruments_response`·`session_window`).
- **데이터:** 실존하지 않는 합성 `SYNTH`(2026-10-09 08:30–17:00 ET, 결측 3분, provisional 1개)를 결정적 생성기로 만들고, drift를 검사한다.
- **성능:** Python 검증기가 문서 kind별 정의만 검사한다(동치, 약 10배 빠름).
- **브라우저 검증에서 찾아 고친 결함:**
  - 연장장 집계 bucket이 세션 이름순으로 정렬되던 문제(응답 계약이 잡아 500)
  - 예기치 않은 500에 CORS 헤더가 빠져 UI가 "Failed to fetch"만 보이던 문제
  - `index.html`에 viewport·title이 없어 모바일 폭이 980 px이던 문제
  - 앞의 두 결함은 수정을 되돌리면 실패하는 회귀 테스트로 확인했다.
- **검증:** `./scripts/verify.sh` 통과(exit 0) — pytest 153개, Vitest 계약 41개·데스크톱 27개, lint·typecheck·build, 생성 타입 drift 검사. 실제 uvicorn·Vite와 in-app browser로 차트 렌더, 동기화, 종목·날짜 변경, 휴장일 안내, 새로고침 후 복원, 375 px 무가로스크롤을 확인했다.
- **미검증:** Tauri native(CSP에서의 차트·attribution 링크 포함), Windows, 원격 CI.

## Phase 2B·3A — 2026-10-16

- **변경 파일:**
  - 백엔드: `services/journal/{store,ledger,migrations/0001_journal.sql}`, `services/api/{main,journal_routes}.py`, `services/market_data/{storage,aggregation,bars_service,provider}.py`
  - 계약: `packages/contracts/{schemas,generated,validator.py,src/validator.ts}`, 계약 fixture
  - 데스크톱: `apps/desktop/src/{Workspace,ChartPanel,PriceChart,TradeEntry,JournalPanel}.tsx`, `src/{workspace-library,journal-markers,market-api}.ts`와 테스트
  - 문서: ADR 0009·0010, ADR 0008 상태, README, EXECUTION_PROCESS, ROADMAP, REPOSITORY_STRUCTURE, IMPLEMENTATION_HISTORY
- **2B:**
  - 이름 붙인 workspace(저장·전환·이름 변경·삭제, v1 이전)
  - Trading Terminal·Trade Review 템플릿의 명시적 적용과 독립 복사, 되돌리기(최대 20단계)
- **3A:**
  - 캔들 클릭으로 수동(실제, 미검증)·가상 기록을 남긴다. 캔들은 참고 정보이며, 가격·시각은 사용자가 입력한다.
  - 기록은 별도 `journal.sqlite3`에 append-only로 저장한다. 같은 요청의 재시도는 멱등하게 처리하고, 같은 ID에 다른 내용이면 409다.
  - ET 시각을 서버에서 UTC로 바꾸고, DST 공백·중복 시각은 거부한다.
  - 수동 원장은 FIFO P&L, 가상 원장은 시나리오별 P&L을 따로 계산해 따로 표시한다. 마커 모양·색도 원장마다 다르다.
- **성능:**
  - 브라우저에서 bars 요청이 약 4초 걸렸다. Parquet 재검증(내용 hash와 문서 검증이 대신 보장)과 provider가 검증한 집계 입력의 재검증을 없앴다.
  - 계약 `$ref`를 인라인해 서버 측 0.5~0.7초로 줄였다.
  - 계약 위반 cache part는 `CacheIntegrityError`로 거부한다(회귀 테스트 추가).
- **검증:** `./scripts/verify.sh` 통과(exit 0) — pytest 180개, Vitest 계약 45개·데스크톱 39개, lint·typecheck·build, 생성 타입 drift 검사. 실제 API(임시 `SIW_DATA_DIR`)와 in-app browser로 다음을 확인했다:
  - 템플릿 적용
  - 수동 매수 기록과 두 차트의 마커
  - 숏 시나리오(순 2.00 = 3.00 − 1.00)
  - 새로고침 후 유지
  - 12:47 ET → 16:47Z 저장
- **남은 범위:** 기록 정정·무효화, CSV/XLSX 가져오기, broker 확인 체결·대사, 계좌 구분, Tauri native·Windows·원격 CI 미검증.

## Phase 3B 거래내역 CSV 가져오기 — 2026-10-17

- **변경 파일:**
  - 백엔드: `services/journal/{csv_import,store,ledger}.py`, `services/journal/migrations/0002_statement_imports.sql`, `services/api/{journal_routes,main}.py`
  - 계약: `packages/contracts/{schemas,generated,validator.py,src/validator.ts}`, 계약 fixture, `tests/fixtures/statements/*.csv`
  - 데스크톱: `apps/desktop/src/{ImportPanel,JournalPanel,Workspace,ChartPanel}.tsx`, `src/{market-api,journal-markers}.ts`, 테스트, `styles.css`
  - 문서: ADR 0011, README, EXECUTION_PROCESS, ROADMAP, REPOSITORY_STRUCTURE, IMPLEMENTATION_HISTORY
- **계약:** `imported_execution`, `import_preview_response`, `import_batch`, `imported_ledger_response`를 추가했고, 포지션 항목은 공용 `LedgerPosition`으로 묶었다.
- **동작:**
  - 미리보기에서 열 매핑을 추천·편집하고 행을 새 행·이미 가져옴·중복 가능·오류로 분류한다.
  - 커밋할 때 서버가 미리보기를 다시 계산하고, 중복 가능한 행마다 사용자의 결정을 요구한 뒤 원자적으로 저장한다. 같은 파일(SHA-256)은 한 번만 가져온다.
- **원장:** 별도 statement import 원장과 FIFO P&L, 청록색 "I" 마커. 수동·가상 원장과 합산하지 않는다.
- **개인정보:** 원본 파일과 매핑하지 않은 열은 저장하지 않는다. 증권사 이름의 계좌번호형 숫자와 알 수 없는 요청 필드는 거부한다.
- **검증:**
  - `./scripts/verify.sh` 통과(exit 0) — pytest 222개, Vitest 계약 52개·데스크톱 40개, lint·typecheck·build, 생성 타입 drift 검사.
  - 실제 API와 브라우저로 다음을 확인했다:
    - 4행 미리보기: 새 행 2, 이미 가져옴 1, 오류 1
    - 커밋: 2행 가져옴, realized −0.30
    - 같은 파일 재미리보기 시 "already imported"
    - `journal.sqlite3`에 계좌번호가 없음
- **남은 범위:** 증권사별 preset·XLSX, batch 무효화·재가져오기, 수동 기록과의 대사, 계좌 구분, Tauri native·Windows·원격 CI 미검증.

## Phase 1D Alpaca 프로토타입 — 2026-10-17

- **사용자 결정:**
  - Alpaca로 프로토타입을 만들고, 이후 증권사 API 4곳을 대체 수단으로 연결한다.
  - Alpaca 무료 요금제는 최근 15분 제한이 있다. 증권사 API는 제한이 없어 보인다고 하셨으나 **미검증**이다.
  - 데이터는 우선 로컬에 저장하고, NAS·클라우드는 이후 검토한다.
- **변경 파일:**
  - 백엔드: `services/market_data/{alpaca,selection,provider,storage,aggregation,bars_service}.py`, `services/api/main.py`, `services/collector/{cli,backfill}.py`
  - 계약: `packages/contracts/{schemas,generated}`(결측 사유 확장)
  - 의존성: `pyproject.toml`·`uv.lock`(httpx를 런타임 의존성으로)
  - 설정·테스트: `.env.example`, `tests/market_data/test_alpaca.py`
  - 데스크톱: `apps/desktop/src/{ChartPanel,Workspace}.tsx`와 테스트
  - 문서: ADR 0012, AGENTS.md(공급업체 규칙 보완), decisions/README, README, EXECUTION_PROCESS, ROADMAP, REPOSITORY_STRUCTURE, IMPLEMENTATION_HISTORY
- **동작:**
  - `SIW_MARKET_DATA_PROVIDER=alpaca`일 때만 `GET /v2/stocks/bars`를 호출한다(읽기 전용, `adjustment=raw`, feed 명시, 페이지네이션).
  - 가격은 Decimal로 파싱한다. sip feed는 최근 15분을 요청하지 않는다(`outside_entitlement`).
  - 체결 없는 분은 `provider_no_bar`, 최근 분은 `not_yet_final`로 표시한다.
  - 오류는 502·503으로 분류해 반환한다. backfill은 인증·데이터 오류에서 재시도 없이 바로 실패한다.
- **보안:** 키는 `APCA_API_KEY_ID`·`APCA_API_SECRET_KEY` 환경 변수로만 받는다. 요청 헤더 외의 출력, `repr`, 오류 메시지에는 넣지 않는다(테스트로 확인).
- **검증:** `./scripts/verify.sh` 통과(exit 0) — pytest 247개(Alpaca 25개 포함), Vitest 계약 52개·데스크톱 41개, lint·typecheck·build. 키 없이 실행한 `alpaca check`는 설정 오류로 exit 2. Alpaca 테스트는 문서 형식의 응답을 쓰며 네트워크를 쓰지 않는다.
- **실계정 확인(2026-10-17):**
  - 사용자가 `alpaca check --symbol SPY`를 실행했다. 대상은 `alpaca-sip`, 2026-10-16 13:30–14:00Z이다.
  - 30/30분을 받았고 결측 사유는 없다. 가격은 응답 그대로 정확히 보존됐다(`772.5` 등).
  - 출력에 키는 없었다.
  - capabilities를 `integration_tested`로 올렸다. 요금제 권한(entitlement)은 feed·범위에 따라 달라 `unknown`으로 유지한다.
- **장시간 실계정 확인(2026-10-17):**
  - SPY 2026-10-16 04:00–20:00 ET를 sip feed, 페이지 크기 100으로 backfill했다. 3 chunk, HTTP 10회, 모두 200, 6.8초가 걸렸다.
  - 960분 중 921분에 bar가 있었고, 나머지 39분은 장전·장후의 `provider_no_bar`였다.
  - 하루 전체, chunk 경계를 넘는 14–15Z 범위, 정규장 5분봉 78개(일부 관측 0개)가 모두 upstream 요청 없이 cache에서 응답됐다.
  - IEX 거래량은 SIP의 2.40%였다.
  - 이 확인을 위해 cache 범위 조합이 체결 없는 분(settled gap)을 포함할 수 있게 했다(테스트 추가).
- **미검증:**
  - 장중 15분 경계와 호출 한도(주말이라 미실행)
  - 증권사 API의 제한·권리
  - NAS 저장: SQLite와 OS 잠금이 로컬 파일시스템을 전제하므로 별도 설계가 필요하다.

## Phase 1D 증권사 adapter — 2026-10-17

- **조사(공식 자료만 사용):**
  - 한국투자(해외주식분봉조회 HHDFS76950200), 토스(2026-08-13 공개 Open API, `/api/v1/candles`), 키움(REST `usa06011`)은 미국주식 1분봉을 제공한다.
  - 미래에셋은 미국주식 시세용 공식 API가 없다(해외선물 API만 있음). 근거와 미확인 항목은 ADR 0013에 정리했다.
- **변경 파일:**
  - 백엔드: `services/market_data/{broker_common,kis,toss,kiwoom,broker_check,selection,provider}.py`
  - 계약: `packages/contracts`(결측 사유 `session_not_covered`)
  - 설정·테스트: `.env.example`, `tests/market_data/test_brokers.py`
  - 문서: ADR 0013, README, EXECUTION_PROCESS, IMPLEMENTATION_HISTORY
- **안전:**
  - 허용 경로(토큰·차트) 외의 호출은 요청을 보내기 전에 `BlockedEndpointError`로 막는다. 주문 경로를 막는 테스트도 있다.
  - 키는 환경 변수로만 받고, 토큰은 메모리에만 보관한다. 오류 메시지와 `repr`에 비밀값이 없는 것을 확인했다.
- **보수적 기본값:**
  - 정규장만 다루고, 범위 밖은 `session_not_covered`로 표시한다.
  - 키움은 시각대를 명시하지 않으면 시작하지 않는다.
  - 같은 분이 중복되면 오류로 처리한다.
  - 수정주가 여부는 확인된 곳만 `raw`로, 한국투자는 `unknown`으로 표시한다.
- **검증:** `./scripts/verify.sh` 통과(exit 0) — pytest 266개(증권사 18개, Alpaca 26개 포함), Vitest 계약 52개·데스크톱 41개, 생성 타입 drift 검사. 키 없이 실행한 `broker_check`는 세 증권사 모두 설정 오류로 exit 2.
- **미검증:** 세 증권사 모두 실계정 미실행이다. 확인할 것은 시각대·bar 시작 의미, 장전·장후 포함 여부, 한국투자의 KEYB 첫 요청 동작, 키움의 `cntr_tm` 중복 여부다.

## 메리츠증권 채널 추가 — 2026-10-17

- **사용자 요청과 공식 확인:**
  - 공식 페이지에서 공개 베타(10/12–25), 정식 출시(10/26), 해외주식 지원, API 이용료 무료, 본인 투자 용도 한정·제3자 제공 금지를 확인했다.
  - 공식 명세 JSON에서 분봉 API(`/market/v1/overseas/candles/minutes`)를 확인했다. 최근 40개만 제공하고, 시각은 KST이며, 오류는 HTTP 500에 `rsp_cd`로 판정하고, `mac_address` 헤더가 필수다.
- **변경 파일:**
  - 백엔드: `services/market_data/{meritz,broker_common,selection,broker_check,provider}.py`
  - 계약: `packages/contracts`(결측 사유 `beyond_source_history`)
  - 설정·테스트: `.env.example`, `tests/market_data/test_brokers.py`
  - 문서: ADR 0013 보완, README, EXECUTION_PROCESS
- **설계:**
  - 다른 증권사와 같이 허용 경로만 호출하고, 토큰은 메모리에만 보관하며, 정규장만 기본으로 다룬다.
  - MAC 주소는 `SIW_MERITZ_MAC_ADDRESS`로만 받고 출력하지 않는다.
  - 최근 40개 밖의 분은 `beyond_source_history`로 표시한다.
- **검증:** `./scripts/verify.sh` 통과(exit 0) — pytest 272개(증권사 24개), Vitest 계약 52개·데스크톱 41개, 생성 타입 drift 검사.
- **미검증:** 실계정 확인이 필요하다(`broker_check --provider meritz`). 확인할 것은 `korea_hour`의 bar 시작·끝 의미, 장전·장후 포함, 토큰 오류 코드다.

## 시세 키 관리 — 2026-10-19

- **사용자 결정:**
  - 증권사 API는 계좌마다 키가 나오므로 화면에서 키를 관리한다.
  - secret은 OS 키체인에 저장하고, `.env`의 키는 쓰지 않는다.
  - 계좌는 별칭과 끝 4자리만 저장한다.
  - 호출 한도 오류가 나면 다음 키로 전환한다.
- **변경 파일:**
  - 백엔드: `services/credentials/{catalog,store,pool}.py`와 migration, `services/api/{credential_routes,main}.py`, `services/market_data/{selection,broker_check,alpaca}.py`
  - 계약: `credential_summary`, `credential_list_response`(secret 필드 없음, 계좌는 4자리만)와 fixture
  - 데스크톱: `apps/desktop/src/{KeysPanel,credential-api,market-api,App}.tsx`, `styles.css`와 테스트
  - 의존성: `keyring`
  - 문서: ADR 0014(0012·0013 보완), README, `.env.example`
- **동작:**
  - 화면 `Market data keys`에서 키를 추가·순서 변경·사용 여부·secret 교체·삭제(확인 후)할 수 있다. 등록한 secret은 다시 보여 주지 않는다.
  - 공급업체마다 키를 순서대로 쓴다. 한도 오류를 받은 키는 60초 동안 건너뛴다. 인증 실패 등 다른 오류는 다른 키로 넘기지 않는다.
  - 키 API는 루프백 Host와 허용된 Origin에서 온 요청만 받는다(그 외 403).
- **검증:**
  - `./scripts/verify.sh` 통과(exit 0) — pytest 297개(키 관리 22개 포함), Vitest 계약 55개·데스크톱 47개, 생성 타입 drift 검사.
  - 브라우저에서 패널이 표시되고 콘솔 오류가 없음을 확인했다. 실행 중인 API에서 외부 Origin 요청이 403으로 거부됨을 확인했다.
- **미검증:**
  - 실제 macOS 키체인 저장·읽기(테스트는 메모리 backend). 사용자가 화면에서 처음 등록할 때 확인한다.
  - Windows·Linux 키체인.
  - 실제 증권사 한도 오류에서의 전환.
- **이전 방식과의 차이:** 기존 `.env`의 키 변수는 더 이상 읽지 않는다. 화면에서 다시 등록해야 한다.
- **실계정 확인(2026-10-19, 화면에서 등록한 키 사용):**
  - **키체인:** 화면에서 Alpaca·토스·키움 키를 등록했다. 키체인 저장과 읽기가 실제 macOS에서 동작했다.
  - **Alpaca:** SPY 30/30분. 기존 `.env` 방식의 결과와 같다.
  - **토스:** SPY 30/30분. 첫 원본 레코드의 `timestamp`가 23:00 KST(=14:00Z)로 마지막 분의 끝이며, 문서의 bar 끝 의미와 맞는다.
  - **키움:**
    - **시각대:** AAPL 13:30–13:45Z를 뉴욕 시각으로 읽으면 15분 중 14분의 종가가 Alpaca SIP와 같다. 서울 시각으로 읽으면 범위 안에 bar가 없다. 그래서 `cntr_tm`은 뉴욕 시각의 bar 시작으로 확정하고 기본값으로 바꿨다.
    - **거래량:** SIP의 약 81%다.
    - **거래소 코드:** SPY(NYSE Arca)는 거래소 코드 `NA`로는 "종목 정보 없음"(1903) 오류가 나고, `NY`로는 30/30분을 받았다. ARCX를 `NY`로 매핑했다.
  - **capabilities:** 토스와 키움을 `integration_tested`로 올렸다.
  - **미확인:** 한국투자·메리츠(키 미등록). 키움의 정규장 이후 bar(20:28 ET)가 어떤 세션인지.

## 화면 언어 선택(영어·한국어) — 2026-10-19

- **사용자 요청:** 언어를 고를 수 있게 하고 영어와 한국어를 지원한다.
- **변경 파일:** `apps/desktop/src/{i18n.ts,I18nProvider.tsx,App,Workspace,ChartPanel,TradeEntry,JournalPanel,ImportPanel,KeysPanel}.tsx`, `chart-data.ts`, `styles.css`와 테스트
- **동작:**
  - 사이드 메뉴 아래 `언어/Language`에서 고른다.
  - 고른 언어는 이 기기의 localStorage(`siw.language`)에 저장한다. 처음에는 브라우저 언어를 따른다(`ko`로 시작하면 한국어, 그 외 영어).
  - 화면 문구, 결측 사유·세션·품질 표시, 오류 안내를 번역한다. 문구는 표시할 때 만들어서, 언어를 바꿔도 데이터를 다시 요청하지 않는다.
  - 번역하지 않는 것: API 서버가 보내는 오류 메시지, 종목 코드·출처 식별자 같은 데이터 값, 사용자가 붙인 워크스페이스 이름.
- **설계:** 외부 라이브러리 없이 React context와 사전 두 개를 쓴다. 한국어 사전은 TypeScript 타입으로 영어 키를 모두 갖도록 강제하고, 테스트로 두 언어의 `{변수}`가 같은지 확인한다.
- **검증:** `./scripts/verify.sh` 통과. 브라우저에서 한국어·영어 전환, AAPL 하루 차트와 요약 문구 번역을 확인했다.

## 터미널형 차트 화면 1단계 — 2026-10-20

- **사용자 요청:** TradingView와 비슷한 사용감의 화면을 단계적으로 만든다. 1단계는 화면 뼈대다. TradingView의 코드·아이콘·로고는 가져오지 않고 직접 구현했다(아이콘은 자체 SVG).
- **변경 파일:** `apps/desktop/src/{ChartPanel,PriceChart,Watchlist,Workspace}.tsx`, `market-api.ts`(`quote`), `i18n.ts`, `styles.css`와 테스트
- **동작:**
  - **차트 도구줄:** 종목 검색 팝업(입력 후 Enter로 첫 결과 선택, Esc로 닫기), 봉 단위 버튼, 정규장·장전후 버튼, 전날·다음 날 이동과 날짜 입력.
  - **왼쪽 도구 막대:** 자석(십자선을 시가·고가·저가·종가에 붙이기), 로그 눈금, 전체 보기. 그리기 도구는 3단계다. 자석·로그 설정은 아직 저장하지 않는다.
  - **범례:** 십자선 위치의 봉(없으면 마지막 봉)의 시가·고가·저가·종가, 봉 시가 대비 변화, 거래량을 보여 준다. 미확정 봉은 `미확정`으로 표시한다.
  - **관심종목:** 공급업체의 모든 종목을 보여 준다. 활성 차트 거래일의 정규장 60분봉으로 첫 시가와 마지막 종가, 시가 대비 등락률을 표시한다. 전일 종가 대비가 아니다(일봉 미구현). 행을 누르면 활성 차트의 종목이 바뀐다.
  - **활성 차트:** 차트를 누르면 활성 차트가 되고 테두리로 표시된다.
  - **상단 표시줄:** 한 줄로 줄여 차트 공간을 늘렸다.
- **검증:** `./scripts/verify.sh` 통과. 브라우저에서 Alpaca 데이터로 확인했다: 관심종목 SPY·AAPL·MSFT 등락률, 관심종목에서 차트 2로 MSFT 지정, 십자선을 따라 바뀌는 범례, 좁은 화면에서 관심종목이 아래로 내려가는 배치.
- **남은 단계:** 2단계 보조지표, 3단계 그리기 도구, 4단계 분할 레이아웃·리플레이.

## 전체 화면 터미널 레이아웃 — 2026-10-20

- **사용자 요청:** 사용자가 준 트레이딩 터미널 화면과 같은 구성으로 디자인한다. 배치와 밀도는 맞추되 아이콘·로고는 직접 그렸다(`icons.tsx`).
- **구성:**
  - **상단 도구줄:** 종목 검색, 봉 단위, 캔들, 보조지표(2단계 전까지 비활성), 차트 1개/2개 전환, 리플레이(4단계 전까지 비활성), 되돌리기, 워크스페이스 메뉴(저장·이름 바꾸기·삭제·템플릿·동기화)
  - **왼쪽 도구:** 십자선, 그리기 도구(3단계 전까지 비활성), 자석, 로그 눈금, 전체 보기
  - **차트 영역:** 기본은 차트 1개다. 2개 보기 설정은 `siw.chartSplit`에 저장한다. 데이터 정보는 차트 아래 접는 줄에 둔다.
  - **오른쪽 아이콘 줄과 패널:** 관심종목, 매매일지, CSV 가져오기, 시세 키 관리
  - **하단:** 거래일 이동·정규장/장전후, 미 동부 시계. 맨 아래 상태 줄에 연결 상태·버전·API 주소·언어 선택을 둔다. 왼쪽 사이드 메뉴는 없앴다.
- **검증:** 데스크톱 테스트 58개 통과(차트 1개/2개 전환, 비활성 도구, 패널 열고 닫기 추가). 브라우저에서 Alpaca 데이터로 차트 2개 보기와 매매일지 패널을 확인했다.

## 2단계 보조지표 — 2026-10-20

- **사용자 요청:** TradingView에서 제공하는 보조지표를 쓸 수 있게 한다. 표준 기술적 지표를 공개된 공식으로 직접 구현했다(TradingView의 코드는 쓰지 않음). 이번 묶음은 33개이고, 더 필요하면 추가한다.
- **목록:**
  - **추세:** SMA, EMA, WMA, VWMA, HMA, 엔벨로프, 파라볼릭 SAR, 슈퍼트렌드, 일목균형표, 아룬, DMI·ADX
  - **모멘텀:** RSI, MACD, 스토캐스틱, 스토캐스틱 RSI, CCI, 윌리엄스 %R, 모멘텀, ROC, 어썸 오실레이터, TRIX, 얼티밋 오실레이터
  - **변동성:** 볼린저 밴드, 켈트너 채널, 돈치안 채널, ATR, 볼린저 밴드폭, 볼린저 %B, 역사적 변동성
  - **거래량:** VWAP(거래일 기준), OBV, MFI, CMF, 거래량 이동평균
- **변경 파일:** `apps/desktop/src/{indicators.ts,IndicatorDialog.tsx,ChartPanel,PriceChart,Workspace}.tsx`, `workspace-state.ts`, `i18n.ts`, `styles.css`와 테스트
- **동작:**
  - 상단 `보조지표`에서 검색하거나 분류를 골라 누르면 활성 차트에 추가된다.
  - 가격 위 지표는 캔들 위에, 오실레이터는 아래의 별도 창에 그린다. RSI 70/30처럼 기준선이 있는 지표는 점선으로 표시한다.
  - 범례에 지표별 현재 값(십자선 위치 기준)이 나온다. 마우스를 올리면 설정·숨기기·삭제 버튼이 나타난다.
  - 지표 설정은 차트마다 따로 워크스페이스에 저장한다(AGENTS.md의 차트별 독립 규칙). 저장된 항목 중 모르는 지표는 버리고, 범위를 벗어난 값은 기본값으로 바꾼다.
- **데이터 안전성:**
  - 각 값은 그 봉과 이전 봉만으로 계산한다. 테스트에서 이력을 잘라도 이전 값이 바뀌지 않는 것으로 모든 지표의 미래 데이터 미사용을 확인했다.
  - 예외는 일목균형표의 후행스팬(오늘 종가를 과거 위치에 그리는 관례)이다. 리플레이(4단계)에서는 따로 막아야 한다.
  - 지표는 불러온 한 거래일의 캔들로만 계산한다. 그래서 기간이 긴 설정은 앞부분 값이 없다(전 거래일로 워밍업하는 것은 미구현).
  - 계산은 화면 표시용 부동소수점이며 회계 계산에는 쓰지 않는다.
- **검증:** 지표 단위 테스트 11개(교과서 값, 유한값, 미래 데이터 미사용)와 화면 테스트(추가·설정·숨기기·삭제·차트별 독립·분류 필터)를 추가했다. 데스크톱 테스트 73개 통과. 브라우저에서 Alpaca AAPL 5분봉에 볼린저 밴드·RSI·MACD를 그리고, 새로고침 후 유지되는 것을 확인했다.
- **관찰된 위험:** 관심종목 시세 요청(종목마다 거래일·60분봉 요청)과 차트 요청이 동시에 몰리면 API가 순서대로 처리해 차트 요청이 10초 클라이언트 제한을 넘을 수 있다. 관심종목 창을 두 개(패널과 떠 있는 창) 열었을 때 재현됐다. 아래 "관심종목 시세 요청과 차트 요청의 경합 수정"에서 해결했다.

## 관심종목 시세 요청과 차트 요청의 경합 수정 — 2026-10-20

- **증상:** Alpaca 공급업체에서 관심종목을 사이드 패널과 떠 있는 창에 함께 열면 AAPL 5분봉 장전후 차트 요청이 클라이언트 제한 시간 10초를 넘겨 "signal is aborted without reason"으로 실패했다.
- **원인:**
  - **서버:** 잠금은 없었다. bar 요청은 순수 Python CPU 작업이라 threadpool의 요청들이 GIL을 두고 다투며 사실상 하나씩 처리됐다. 캐시된 60분봉 요청 하나가 약 0.45초였고, 그중 절반 이상이 1분마다 `exchange_calendars`(pandas)로 같은 거래일 세션 창을 다시 계산하는 데 쓰였다.
  - **클라이언트:** 관심종목 화면마다 종목별로 세션 요청 1개와 60분봉 요청 1개를 차트 요청과 동시에 보냈다. 새로 고침 뒤 떠 있는 창이 다시 열리면 사이드 패널의 관심종목도 열려 요청이 두 배가 됐다.
- **변경:**
  - `services/market_data/calendar.py`: 세션 창을 (달력, 날짜)별로 프로세스 안에서 한 번만 계산한다. 달력 범위가 고정이라 값이 바뀌지 않는다.
  - `apps/desktop/src/session-quotes.ts`(`useSessionQuotes`): 관심종목 시세를 Workspace에서 한 번 요청해 두 화면이 함께 쓴다. 보이는 차트가 모두 로드된 뒤(성공·휴장·실패)에 요청한다.
  - 떠 있는 관심종목 창이 다시 열리면 사이드 패널은 닫힌 채로 시작한다.
  - 클라이언트 제한 시간 10초는 그대로 둔다. 경합이 없을 때 처음 받는 960분 범위 요청은 약 2.2초였다.
- **검증:**
  - 브라우저(작업 트리 API 8001, UI 5174, Alpaca): 차트 요청이 혼자 나간 뒤 관심종목 요청 3+3개가 이어졌다. 캐시 없는 2026-10-13은 차트 2.2초, 2026-10-19는 2.5초였다.
  - 같은 날의 캐시된 60분봉 요청 6개를 동시에 보냈을 때 가장 느린 응답이 2.59초(수정 전 API)에서 1.01초(수정 후)로 줄었다.
  - 데스크톱 테스트(차트 로드 후 시세 요청, 화면 전환 시 재요청 없음)와 `./scripts/verify.sh`를 실행했다.
- **남은 위험:** 캐시에서 읽은 bar의 JSON Schema 재검증이 캐시 요청 CPU의 대부분을 차지한다. 데이터 무결성 확인이라 이번에는 그대로 두었다. 차트 2개가 모두 처음 받는 긴 범위이면 서로 경합할 수 있다.

## 상태 변경 양식

- 날짜 / 작업 번호 / 변경 파일:
- 실제 검증 명령과 결과:
- 새로 확정된 사항과 근거:
- 변경한 가정 또는 남은 문제:
- 다음 작업:
