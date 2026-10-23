# Project State — 실행 상태 및 미확정 결정

> 이 파일은 **진행 상황을 기록하는 동적 상태 파일**이다. AGENTS.md와 영구적 제품 명세에 임시 상태를 누적하지 않는다.

## 현재 상태 (2026-10-15 문서 대조 기준)

문서 탐색은 [문서 안내](README.md), 커밋별 누적 작업은 [구현 이력](IMPLEMENTATION_HISTORY.md)을 참고한다. 아래 기능 검증 기록은 별도 표시가 없으면 2026-10-12 실행 결과이며, 이번 문서 정리에서 재실행한 결과는 아니다.

- **Confirmed:** 미국 주식 중심 로컬 우선 워크스테이션. 차트별 독립 지표 및 선택 차트 프리셋 일괄 적용. 실제/수동/가상/백테스트 체결 분리. 1분봉 기본 저장과 소스 출처 무결성.
- **Decision:** 2026-10-22 사용자 결정으로 Alpaca를 미국 기본 공급업체로 선정했다([ADR 0035](decisions/0035-alpaca-default-us-provider.md)). Massive는 미검증 대체 후보이며, 증권사 API/공개 정보 수집원은 별도 출처로 병행한다. (이전: Alpaca와 Massive는 동등한 미선정 후보였다.)
- **Candidate register (2026-10-19):** 사용자가 지정한 13곳(Webull, Massive, Databento, Alpaca, 토스·키움·한국투자·메리츠·DB·LS·KB증권, ChartExchange, Unusual Whales)을 [공급업체 평가 8절](DATA_PROVIDER_EVALUATION_v1.5.md#8-후보-등록부-2026-10-19-사용자-등록)에 후보로 등록했다. 등록은 선정·결제·구현 승인이 아니며, 구현된 채널은 fake·Alpaca·KIS·토스·키움·메리츠뿐이다.
- **Runtime verified (2026-10-12):** macOS 15.7.9 x86_64, Python 3.10.19, uv 0.12.23, Node 22.23.2, pnpm 11.25.0, Tauri CLI 2.12.2. 2026-10-23 사용자 승인으로 rustup(minimal, `~/.cargo`, 셸 설정 파일 수정 없음)을 설치해 rustc·cargo 1.99.1을 쓴다. 이전에는 cargo·rustc가 없었다. 나머지 버전은 이번 정리에서 재검증하지 않았다.
- **Repository:** ZIP 배포 경로를 `git init -b main`으로 초기화하고 Phase 0 local baseline commit을 생성했다. 2026-10-19 원격 `origin`(https://github.com/pensivetraveler/stock-intelligence)을 연결하고 `main`을 푸시해 upstream으로 설정했다. 같은 날 GitHub에서 `main` 보호 규칙을 켰다: PR 필수(승인 0명), `verify` 검사 통과와 최신 상태 필수, 직선 이력, 강제 푸시·삭제 금지. 저장소는 공개 상태다.
- **Phase 0 status:** P0-01~P0-07의 코드·계약·로컬 품질 게이트 완료. Tauri native build는 2026-10-23 처음 성공했다(아래 "Tauri 네이티브 빌드"). `tauri dev`는 미실행이다.
- **Open questions:** 실제 공급업체 계정 권한/저장 라이선스, 향후 예산. Phase 0은 외부 계정 연결을 요구하거나 수행하지 않았다.
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
- **Tauri:** `tauri-cli 2.12.2` 확인. 2026-10-23 `tauri build`로 `.app`을 만들고 실행해 API·실시간 연결을 확인했다. `tauri dev`(`pnpm desktop:tauri`)는 미실행이다.
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

## 3단계 그리기 도구 — 2026-10-20

- **도구(왼쪽 도구 막대):**
  - 저장되는 그림: 추세선, 수평선(가격 표시), 피보나치 되돌림(0·0.236·0.382·0.5·0.618·0.786·1), 텍스트 메모
  - 임시 도구(저장하지 않음): 측정(가격 변화·%·캔들 수·시간), 확대(드래그한 시간 범위로)
  - 휴지통: 활성 차트의 현재 종목 그림을 지운다. 상단 되돌리기로 복구할 수 있다.
- **변경 파일:** `apps/desktop/src/{drawings.ts,DrawingLayer.tsx,PriceChart,ChartPanel,Workspace}.tsx`, `workspace-state.ts`, `i18n.ts`, `styles.css`와 테스트
- **동작:**
  - 그림은 (캔들 시각, 가격)에 고정되어 스크롤·확대·눈금 변경에도 같은 캔들에 남는다.
  - 자석을 켜면 클릭이 그 캔들의 시가·고가·저가·종가 중 가까운 값에 붙는다.
  - 도구를 쓰는 동안에는 차트가 끌려 움직이지 않는다. 한 번 그리면 십자선으로 돌아가고, Esc로 취소한다.
  - 십자선 상태에서 그림을 누르면 선택되고, ✕ 버튼이나 Delete 키로 지운다. 그림 위에서 휠을 굴려도 차트가 확대·축소된다.
- **저장:** 그림은 차트별 설정에 저장하며, 각 그림은 그린 종목을 기억해 그 종목을 볼 때만 보인다. 저장된 항목 중 잘못된 것은 불러올 때 버린다.
- **검증:**
  - 그림 계산 테스트 5개, 그리기 레이어 테스트 7개(선형 좌표 변환으로 클릭·생성·측정·확대·선택 삭제), 워크스페이스 테스트 2개(도구 전환·Esc, 지우기와 되돌리기)를 추가했다. 데스크톱 테스트 87개 통과.
  - 브라우저에서 Alpaca AAPL 5분봉에 추세선·수평선·피보나치·측정을 그렸다. 새로고침 후에도 그림이 유지되고, 확대해도 같은 캔들에 남으며, 그림 위 휠로 확대되는 것을 확인했다.

## 4단계 분할 레이아웃·리플레이 — 2026-10-20

- **분할 레이아웃:**
  - 상단 배치 버튼으로 차트 1개 / 2개(나란히) / 4개(2×2)를 고른다.
  - 워크스페이스는 항상 차트 4칸을 저장하고, 배치 모드(`layoutMode`)도 워크스페이스에 저장한다.
  - 이전에 저장한 2칸짜리 레이아웃은 불러올 때 3·4번 칸을 1번 차트의 종목·거래일로 채운다.
  - 예전 `siw.chartSplit` 값은 배치 모드가 저장되지 않은 레이아웃에서만 2분할로 읽는다.
  - 템플릿은 차트 두 개분 설정을 3·4번 칸에도 반복해 적용한다.
- **리플레이:**
  - 상단 `리플레이`를 누른 뒤 활성 차트에서 캔들을 클릭하면 그 캔들이 끝난 시각에서 시작한다.
  - 한 단계 앞·뒤로, 재생·일시정지, 속도 1x·2x·5x·10x를 쓸 수 있다. 한 단계는 활성 차트의 봉 하나이고, 불러온 마지막 캔들에서 멈춘다.
  - 리플레이 시각은 화면의 모든 차트에 같이 적용된다. 그 시각까지 끝난 캔들만 차트·보조지표·범례·매매 마커·캔들 클릭에 쓴다.
  - 하루 전체 기준인 데이터 정보 줄과 관심종목 가격은 리플레이 중 숨긴다.
  - 활성 차트의 거래일이나 종목을 바꾸면 리플레이가 끝난다.
  - 리플레이 시각은 시장 시각(캔들 종료 기준)이다. 공급업체의 공개 지연(예: Alpaca 무료 요금제 15분)은 반영하지 않는다.
  - 하루치 캔들은 이미 불러온 상태에서 화면과 계산에만 쓰지 않는다. 서버의 `as_of_utc` 기준 조회로 바꾸는 것은 후속 과제다.
- **차트 보기:** 캔들은 차트를 처음 불러올 때만 화면에 맞춘다. 보기가 최신 캔들까지 와 있으면 새 캔들을 따라가고, 과거로 옮긴 보기는 그대로 둔다.
- **변경 파일:** `apps/desktop/src/{replay.ts,Workspace,ChartPanel,PriceChart}.tsx`, `workspace-state.ts`, `workspace-library.ts`, `icons.tsx`, `i18n.ts`, `styles.css`와 테스트
- **검증:**
  - 테스트를 추가했다: 리플레이 계산 2개, 리플레이 흐름 2개, 레이아웃 저장 2개, 1·2·4분할 전환 1개. 데스크톱 테스트 94개가 통과했다.
  - 브라우저에서 Alpaca 데이터로 확인했다. 4분할(AAPL 5분·MSFT 1분·SPY 15분·MSFT 60분)에서 11:30 리플레이를 시작하고, 한 단계(12:30), 10배속 재생을 해 보았다. 재생은 마지막 캔들(16:00)에서 멈췄고, 차트가 새 캔들을 따라갔다.

## 보조지표 워밍업 — 2026-10-20

- **문제:** 지표를 그 거래일 캔들로만 계산해서, 기간이 긴 지표(예: RSI 14, MACD 26)는 장 초반 값이 비어 있었다.
- **변경:**
  - 차트에 지표가 있으면 같은 봉 단위·시간대로 이전 거래일 캔들을 필요한 만큼 더 불러온다(최대 10거래일, 달력으로 30일).
  - 필요한 수는 가장 긴 기간의 3배다(지수평균이 시작값 영향을 벗어나는 데 필요한 길이). 상한은 1,000개다.
  - 일목균형표는 선행스팬2 기간과 이동 간격의 합을 쓴다. 역사적 변동성의 연간 봉 수처럼 기간이 아닌 설정은 세지 않는다.
  - 워밍업 캔들은 계산에만 쓰고 그리지 않는다. 화면 거래일보다 앞선 캔들만 쓰므로 미래 데이터가 섞이지 않는다.
  - 지표가 없는 차트는 추가 요청을 하지 않는다. 불러오기에 실패하면 예전처럼 장중부터 값이 나온다.
  - VWAP은 거래일마다 새로 시작하므로 영향이 없다.
- **변경 파일:** `apps/desktop/src/{indicators.ts,ChartPanel.tsx}`와 테스트
- **검증:** 워밍업 길이·그리기 시작 위치 테스트와, 지표가 있을 때만 이전 거래일을 요청하는지 보는 화면 테스트를 추가했다. 데스크톱 테스트 97개 통과. 브라우저에서 Alpaca AAPL 5분봉의 볼린저 밴드·RSI·MACD가 04:00 첫 캔들부터 그려지는 것을 확인했다.

## 리플레이 시점 기준 조회 — 2026-10-20

- **변경:**
  - 리플레이 중에는 차트마다 그날 캔들을 API의 `as_of_utc`(리플레이 시각)로 다시 요청한다.
  - 그 시각에 이 계정이 실제로 받을 수 있었던 캔들만 쓴다. 공급업체 공개 지연(Alpaca 무료 요금제 15분)과 미확정 캔들이 반영된다.
  - 데이터 정보 줄은 하루 전체 요약 대신 그 시점 기준 요약을 보여 준다.
- **겹치는 단계:**
  - 서버 응답이 오기 전에는, 그리고 응답과 함께, 캔들 종료 시각 기준으로 한 번 더 자른다(기존 방식). 그래서 어떤 경우에도 리플레이 시각 이후 캔들은 그리지 않는다.
  - 요청이 진행 중이면 다음 요청은 가장 최근 리플레이 시각 하나로 합친다. 빠른 재생에서도 요청이 쌓이지 않는다.
- **함께 고친 문제:** 리플레이 시작 캔들을 고르는 동안 그림(추세선·피보나치 등) 위를 클릭하면 그림 선택으로 처리되어 리플레이가 시작되지 않았다. 고르는 동안에는 그림이 클릭을 받지 않게 했다.
- **변경 파일:** `apps/desktop/src/{market-api.ts,ChartPanel,PriceChart,DrawingLayer}.tsx`와 테스트
- **검증:**
  - API 클라이언트의 `as_of_utc` 쿼리, 리플레이 시작 시 시점 기준 요청, 고르는 동안 그림 클릭 비활성에 대한 테스트를 추가했다. 데스크톱 테스트 99개 통과.
  - 브라우저에서 Alpaca AAPL 5분봉으로 확인했다. 13:10 ET에 시작하면 `as_of_utc=17:10Z`로 요청해 107구간(12:55 ET까지, 15분 지연과 일치)을 받았다. 한 단계 진행하면 13:15 ET, 108구간이었다.

## 캔들 1,200개 기본값·커스텀 타임프레임·즐겨찾기 — 2026-10-20

- **사용자 요청:** 캔들은 기본 1,200개로 하고, 타임프레임을 직접 정할 수 있게 하며, 즐겨찾기한 타임프레임을 상단에 보여 준다. 일·주봉도 포함하기로 했으나 공급업체 일봉 연결이 필요해 다음 PR로 나눴다.
- **서버:**
  - 집계를 5·15·30·60분 고정에서 2~960분 사이 모든 정수 분(`Nm`)으로 넓혔다. 세션 경계 정렬과 출처 기록 방식은 그대로다.
  - 계약의 `timeframe`은 `Nm`와 다음 PR용 `1d`·`1w`를 받는다.
  - `GET /v1/market/sessions?end_date=&count=`(최대 120)를 추가했다. 그날을 포함한 최근 거래일 목록을 오래된 순으로 준다(`session_list_response`).
- **화면:**
  - 상단에 즐겨찾기 타임프레임 버튼을 둔다. 기본 즐겨찾기는 1m·5m·15m·30m·1h다. 60분 이상은 시간으로 표시한다(예: 120m → 2h).
  - ▾ 메뉴에서 프리셋(1·2·3·5·10·15·30·45분, 1·2·3·4시간)을 고르거나 별표로 즐겨찾기를 켜고 끈다. 직접 입력은 분 또는 시간 단위이고 16시간까지다. 즐겨찾기는 이 기기(`siw.timeframeFavorites`)에 저장한다.
  - 차트는 고른 거래일까지 약 1,200개 캔들을 불러온다. 인트라데이는 최대 60거래일이다(예: 5분 장전후 7거래일, 60분 정규장 60거래일 약 420개).
  - 그날 캔들을 먼저 보여 주고, 이전 거래일은 4개씩 동시에 받아 뒤에 채운다. 그날부터 끊김 없이 이어진 거래일까지만 표시한다.
  - 처음에는 최근 150개 캔들을 보이게 하고, 나머지는 스크롤로 본다. 이전 캔들이 앞에 추가돼도 보던 시간대를 유지한다.
  - 보조지표 워밍업은 이전 거래일 불러오기가 끝난 뒤, 가장 이른 표시일의 전날부터 한 번만 시작한다.
- **검증:**
  - pytest에 임의 분 집계(3m, 120m의 마감 직전 잘린 구간)와 지원하지 않는 값 거부 테스트, 거래일 목록 API 테스트를 추가했다.
  - 화면 테스트를 추가했다: 1,200개 기준 이전 거래일 요청, 즐겨찾기, 직접 입력(2시간·16시간 초과 거부), 워밍업 한 번만 실행. 데스크톱 테스트 108개 통과.
  - 브라우저에서 Alpaca AAPL로 확인했다. 5분봉은 7거래일(10/01–10/09)과 워밍업용 9/30을 불러왔고, 2시간봉 직접 입력도 동작했다.

## 일봉·주봉 — 2026-10-20

- **사용자 요청:** 위 타임프레임 작업의 2부. 일봉(1D)·주봉(1W)을 프리셋으로 추가한다.
- **서버:**
  - `services/market_data/daily.py`를 추가했다. 일봉 1개는 그 거래일 정규장 개장~마감 구간이다(조기 폐장은 일찍 끝난다). 공급업체 일봉이 없는 거래일은 `provider_no_bar`·`not_yet_final`·`outside_entitlement`로 누락 구간에 남긴다.
  - 주봉은 분봉이 아니라 일봉만으로 집계한다(월~금 거래소 주, lineage `aggregated_1d`, 리비전 `{일봉 리비전}:agg:1w:{정책}`). 거래일 일부가 빠진 주는 `partial`, 미확정 일봉이 있으면 `provisional`이다.
  - `as_of_utc`가 있으면 그 시각까지 공개된 일봉만 준다(진행 중인 거래일은 공개 지연을 더한 시각 기준).
  - 계약: lineage `kind`에 `aggregated_1d`를 추가했고 `source_timeframe`은 `1m`·`1d`다. 집계 lineage는 출처 리비전이 필요하고, 공급업체 lineage는 봉의 타임프레임과 같아야 한다(Python·TS 검증기 모두).
  - Alpaca adapter가 `1Day` 일봉을 페이지 단위로 받는다(최대 12페이지, 최대 40년 범위). 키 풀은 일봉도 한도 오류 시 다음 키로 넘긴다.
- **화면:**
  - 타임프레임 메뉴에 1D·1W가 생기고 즐겨찾기에 넣을 수 있다. 일봉은 약 1,200거래일(1,739일), 주봉은 약 23년을 한 번에 요청한다.
  - 일·주봉 차트의 시간 축은 날짜로 표시하고, 장전후 토글은 비활성화한다(정규장 기준).
  - 리플레이는 일·주봉에서도 리플레이 시점 기준으로 조회한다. 워밍업은 하지 않는다(요청 한 번에 전체 이력).
- **한계:**
  - 일봉은 처음에 로컬 캐시 없이 매번 공급업체에서 받았다. 아래 "일봉 캐시"에서 해결했다.
  - 증권사 adapter는 아직 일봉을 지원하지 않으며, 선택하면 명확한 오류를 보여 준다.
  - 직접 입력은 분·시간 단위만이다. 1D·1W는 프리셋으로만 고른다.
  - Alpaca 일봉은 2016년부터라 주봉 23년 요청은 `partial`로 표시된다.
- **검증:**
  - pytest에 `tests/market_data/test_daily.py`(2026년 추수감사절 주 조기 폐장·누락·as-of·주봉 집계 등 7개)와 일봉 미지원 공급업체 테스트를 추가했다.
  - 데스크톱 테스트에 일·주봉 범위·라벨·정렬, 장전후 토글 비활성화를 추가했다. 112개 통과.
  - 브라우저에서 Alpaca AAPL로 확인했다. 일봉 1,196개(2022-01-04~2026-10-09, complete), 주봉은 23년 요청 중 562/1,201주(partial). 시간 축은 날짜로 나온다.

## 일봉 캐시 — 2026-10-20

- **사용자 요청:** 일봉을 로컬 캐시에 저장한다.
- **구조:**
  - 결정 기록은 [ADR 0015](decisions/0015-daily-bar-cache-by-year.md)에 있다.
  - 종목·연도마다 coverage 기록 1개를 둔다. 일봉 Parquet은 `year=YYYY` 단위로 묶는다(분봉은 그대로 거래일 단위).
  - 저장하는 것은 확정된 연도뿐이다. 모든 봉이 확정이고 누락 사유가 모두 확정된 사실이어야 한다. 올해는 저장하지 않고 매번 새로 받는다.
  - 필요한 연도들은 공급업체 한 번 호출로 받는다. 출처·피드·수정주가·리비전이 하나라도 다르면 섞지 않고 공급업체 데이터만 쓴다.
  - as-of 요청은 그 시각까지 모두 공개됐던 연도만 캐시에서 쓴다. 컷오프로 가려진 봉이 없는 연도만 컷오프 없이 저장한다.
  - 주봉은 캐시된 일봉에서 그때그때 집계한다.
  - `sessions_in_range`는 거래소 달력 범위(2000~2030) 밖 날짜를 건너뛴다.
- **검증:**
  - `tests/market_data/test_daily_cache.py` 4개: 확정 연도 재사용, 리비전 혼합 금지, as-of 재사용, 일봉 미지원 공급업체.
  - verify.sh 통과: pytest 320, contracts 55, desktop 112.
  - Alpaca AAPL로 API를 확인했다.
    - 1D는 첫 호출 2.7초, 두 번째 1.0초였다. 두 번째는 올해만 다시 받았다.
    - 1W(23년)는 5.7초에서 2.2초가 됐다.
    - 캐시는 2016~2025년 10개 파일, 176KB다.
    - 결과가 partial인 것은 오늘(10/20) 장이 열리기 전이라 `outside_entitlement`이기 때문이다.

## 계좌별 실전/모의 구분 — 2026-10-20

- **사용자 요청:** 키움을 포함한 모든 증권사 계좌에 실전·모의 계좌 구분을 입력받는다.
- **변경:** 결정 기록은 [ADR 0014 개정](decisions/0014-provider-key-registry.md#amendment-2026-10-20--real-and-demo-accounts)에 있다.
  - 키 화면에 `계좌 구분`(모의 기본, 실전)을 두었다. 목록에서도 바꿀 수 있다.
  - 기존 증권사 키는 모두 실전으로 이전했다(실제로 실전 도메인에서 쓰였음).
  - 키움 모의 키는 `mockapi.kiwoom.com`, 한국투자 모의 키는 `openapivts…:29443`으로 보낸다.
  - 토스·메리츠는 공식 문서에 모의 도메인이 없어서 모의 키를 거부한다. 거부 사유는 키 상태에 표시된다.
  - 모의 서버 데이터는 별도 feed scope/revision이라 실전 데이터와 섞이지도, 함께 캐시되지도 않는다.
  - 키움 MCP(`kiwoom-exec`)는 `scripts/kiwoom_mcp_launcher.py`로 시작한다.
    - 키 화면의 첫 번째 사용 중인 키움 키를 키체인에서 읽는다.
    - `KIWOOM_MODE`는 그 키의 계좌 구분으로 정한다.
    - `~/.claude.json`에는 키를 두지 않는다.
- **검증:**
  - `tests/credentials/test_account_environment.py` 5개: 저장·검증, 기존 키 이전, 설정 전달, 도메인 분기·거부, 풀 재생성.
  - 키 화면 테스트에 입력·변경을 추가했다.
  - API 재시작 후 기존 키 4개의 구분을 확인했다(Alpaca 없음, 키움·토스 실전). `claude mcp list`에서 kiwoom-spec과 kiwoom-exec 모두 Connected였다.
- **미검증:** 모의 키로 실제 모의 서버에 요청한 적이 없다. 모의 서버가 미국주식 분·일봉을 주는지도 모른다.

## 티커 검색 — 2026-10-20

- **사용자 요청:** 티커 검색 기능. 검색 범위는 미국 상장 전체 종목으로 정했다.
- **변경:** 결정 기록은 [ADR 0016](decisions/0016-ticker-search-symbol-directory.md)에 있다.
  - Alpaca 자산 목록(`GET /v2/assets`, 읽기 전용)을 하루 한 번 받아 `SIW_DATA_DIR/symbols/`에 둔다. 갱신에 실패하면 이전 사본을 `stale`로 표시해 쓴다.
  - `GET /v1/market/symbols?q=&limit=`(새 계약 `symbol_search_response`)는 티커 일치, 티커 접두, 회사명 단어 순으로 결과를 준다.
  - Alpaca adapter는 설정 목록 밖이라도 상장 종목 ID(`us:<symbol>:<MIC>:2026`)면 차트를 받는다. 매매일지와 CSV 가져오기도 검색으로 알게 된 종목을 받는다.
  - 상단 종목 검색창은 설정 종목과 전체 종목 검색 결과(회사명 포함)를 함께 보여 준다. 고른 종목은 관심종목에 추가되고 ×로 뺄 수 있으며, 이 기기에 기억된다.
- **검증:**
  - `tests/market_data/test_symbol_directory.py` 6개: 자산 목록 호스트 전환, 상장 종목 ID, 검색 순위, 하루 보관과 실패 시 stale, 목록 없음, API.
  - 화면 테스트에 검색, 차트, 관심종목, 새로고침 후 유지, 빼기를 추가했다. 데스크톱 테스트 114개 통과.
  - Alpaca 실호출로 확인했다. 종목 13,228개를 첫 검색 때 받는 데 2.8초, 이후 검색은 약 0.03초였다.
    - nvda·apple·brk.b 검색 결과가 맞았다.
    - NVDA 일봉은 34개 complete였다.
    - 브라우저에서 "nvidia"를 검색한 뒤 Enter로 NVDA 5분봉 차트와 관심종목 표시를 확인했다.
- **한계:**
  - 증권사 adapter는 아직 설정 종목만 받는다.
  - ID 안의 MIC는 Alpaca가 검증하지 않는다.
  - 티커 변경·상장폐지 이력은 다루지 않는다.

## 토스 직접 연동 1·2단계 — 2026-10-20

- **사용자 결정:**
  - MCP(tossinvest-mcp) 대신 직접 구현한다. 토스 키를 하나만 쓸 수 있어 토큰이 충돌하기 때문이다.
  - 토스 조회 데이터는 모두 붙인다. 주문 쓰기는 제외한다.
  - 국내 주식은 차트까지 지원하되 별도 단계로 한다.
  - 계좌는 체결만 원장에 저장한다.
- **변경:** 결정 기록은 [ADR 0017](decisions/0017-toss-direct-integration.md)에 있다.
  - **1단계:** `TossSession`을 키당 하나만 두고 프로세스 전체가 공유한다.
    - 공식 조회 API 34개만 허용한다. 주문·조건주문 쓰기는 차단한다.
    - 공식 그룹별 초당 한도를 지키고, 계좌 API에는 헤더를 강제한다.
  - **2단계 일봉:** 토스 일봉(`1d`, 비수정)은 연도 캐시·주봉과 같은 경로를 쓴다.
  - **2단계 상장 종목:** 토스도 검색으로 찾은 상장 종목 ID를 받는다.
  - **2단계 실시간 시세:** `GET /v1/market/quote`(새 계약 `market_quote`)와 화면의 `실시간 시세` 패널을 추가했다.
    - 현재가·호가·최근 체결 30건·가격 제한을 보여 준다.
    - 5초마다 갱신하고, 리플레이 중에는 멈춘다. 저장하지 않는다.
- **검증:**
  - `tests/market_data/test_toss_session.py` 6개: 허용목록·계좌 헤더, 그룹 대기, 토큰 공유, 일봉 페이징, 시세 계약, 시세 API.
  - 화면 테스트에 시세 패널과 토스 키가 없을 때를 추가했다. 데스크톱 115개 통과.
  - 등록된 실전 토스 키로 실제 조회해 확인했다.
    - AAPL 일봉 34개 complete. 미국 일봉 시각은 뉴욕 자정을 KST로 적은 값이다.
    - 10/15·10/16·10/19 시가·고가·저가·종가가 Alpaca와 같았다. 거래량은 약 0.4% 적었다(일부 거래소 기준).
    - 브라우저에서 NVDA 실시간 시세 패널을 확인했다(장전).
- **남은 단계:** 3 종목·시장 정보, 4 리서치, 5 계좌·체결 원장, 6 WebSocket, 국내 차트 완전 지원.

## 토스 직접 연동 3단계: 종목·시장 정보 — 2026-10-20

- **변경:** 결정 기록은 [ADR 0017](decisions/0017-toss-direct-integration.md) 3단계에 있다.
  - **종목 정보:** `GET /v1/market/stock-info`(`stock_info`)는 한글·영문명, ISIN, 시장, 유형, 상태, 상장일, 상장 주식 수, 활성 매수 유의사항을 준다.
  - **환율:** `GET /v1/market/exchange-rate`(`exchange_rate`)는 USD/KRW 참고 환율을 준다.
  - **장 운영 시간:** `GET /v1/market/broker-calendar`(`broker_market_calendar`)는 전일·당일·익일 토스 장 운영 시간을 주고, 미국은 차트 캘린더와 대조한다.
  - **한글 검색:** 티커 검색이 토스 한글 종목명으로도 찾는다(`local_name`). 한글명이 정확히 일치하면 맨 위에 온다.
  - **화면:** `종목 정보` 패널을 추가했다.
- **발견(실조회):**
  - 토스의 미국 시장 구분은 상장 거래소가 아니다. SPY(NYSE Arca)가 "AMEX"로, OTC가 `US_ETC`로 분류된다. 그래서 종목 ID의 거래소는 계속 Alpaca 기준이다.
  - 토스 애프터마켓은 19:50 ET에 끝나서 차트 캘린더(20:00)와 다르다. 이 차이는 화면에 그대로 표시하고 어느 쪽도 고치지 않는다.
- **검증:**
  - `tests/market_data/test_toss_info.py` 6개: 종목 정보·유의사항, 환율, 장 시간 대조, 한글명 검색·저장, API, 한 번만 재조회.
  - 화면 테스트에 종목 정보 패널을 추가했다. 데스크톱 116개 통과.
  - 실조회 결과:
    - "애플" 검색 시 AAPL이 맨 위였고, "엔비디아"는 NVDA였다.
    - NVDA 종목 정보는 ISIN US67066G1040, 유의사항 없음이었다.
    - 환율은 1331.5였다.
    - 장 시간은 프리·정규장 일치, 애프터 불일치(23:50Z 대 00:00Z)였다.
    - 브라우저에서 패널과 한글 검색을 확인했다.
- **남은 단계:** 4 리서치, 5 계좌·체결 원장, 6 WebSocket, 국내 차트 완전 지원.

## 토스 직접 연동 4단계: 리서치 — 2026-10-20

- **변경** ([ADR 0017](decisions/0017-toss-direct-integration.md) 4단계):
  - **API:** `GET /v1/research/{dataset}`에서 토스 리서치 데이터 14종을 공통 계약 `research_table`로 준다.
    - 주식·섹터 랭킹
    - 국내 지수·국채 금리, 지표 캔들, 코스피·코스닥 투자자별 매매대금
    - 섹터 목록·상세·구성 종목·관련 ETF
    - 국내 종목 투자자별·프로그램·공매도·신용·대차 동향
  - **값 처리:** 값은 토스가 준 문자열 그대로 두고, 시각은 UTC로 바꾼다.
  - **요청 제한:** 데이터셋별로 문서에 있는 파라미터만 전달한다.
  - **화면:** `리서치` 패널에서 표를 고르고, 문서에 정의된 선택지로 조회한다. 이전 데이터 버튼으로 과거 행을 더 볼 수 있다.
- **검증:**
  - `tests/market_data/test_toss_research.py` 9개: 랭킹 평탄화, 섹터 기간 열, 커서·UTC, 허용 데이터셋·파라미터, API.
  - 화면 테스트에 리서치 패널을 추가했다. 데스크톱 117개 통과.
  - 실조회:
    - 7종을 직접 확인하고 섹터 상세·구성 종목·ETF도 확인했다.
    - 섹터는 정확히 300개였다.
    - 브라우저에서 미국 거래대금 랭킹 100행을 확인했다.
- **남은 단계:** 5 계좌·체결 원장, 6 WebSocket, 국내 차트 완전 지원.

## 토스 직접 연동 5단계: 계좌·체결 원장 — 2026-10-20

- **변경:** 결정 기록은 [ADR 0018](decisions/0018-broker-api-execution-ledger.md)에 있다.
  - **체결 원장:** 매매일지에 네 번째 원장 `증권사 API 체결`을 추가했다.
    - 토스는 개별 체결을 주지 않는다. 그래서 체결이 있는 종료 주문 1건을 1행으로 저장한다(총 수량, 평균가, 수수료+세금, 토스가 알려준 마지막 체결 시각).
    - `order_aggregate`/`reported_last_fill`로 표시한다.
    - 추가만 하고, 다시 동기화해도 중복이 생기지 않는다. 저장 후 값이 바뀐 주문은 덮어쓰지 않고 `conflicts`로 알린다.
    - 미국 주문만 저장한다. 원화 주문은 국내 단계로 미룬다.
  - **계좌 조회(저장 안 함):** 계좌(번호 끝 4자리만), 보유 종목·요약, 매수 가능 금액, 수수료율, 미체결 주문, 진행 중인 조건주문, 판매 가능 수량.
  - **화면:** `계좌` 패널(조회 전용 안내, `체결을 매매일지로 동기화` 버튼)을 추가했다. 매매일지에 토스 원장 섹션을 넣었다.
- **검증:**
  - `tests/market_data/test_toss_account.py` 3개: 계좌 마스킹과 부분 실패 표시, 동기화(1회 저장, 재동기화, 변경 감지, 국내·미체결·미상 제외), API·원장·GET 외 요청 없음.
  - 화면 테스트에 계좌 패널과 동기화 후 매매일지를 추가했다. 데스크톱 118개 통과.
  - 실계좌로 확인했다(임시 일지에 저장한 뒤 삭제).
    - 계좌 1개, 오류 없음.
    - 종료 주문 3,862건 중 체결된 미국 주문 2,021건을 저장했다. 미체결 520, 원화 1,112, 미상 209건은 건너뛰었다.
    - 재동기화 시 새로 저장된 건 0, 이미 저장 2,021건이었다.
    - 브라우저에서 계좌 패널이 끝 4자리로 표시되는 것을 확인했다.
  - 사용자의 실제 매매일지에는 아직 동기화하지 않았다(사용자가 버튼으로 실행).
- **남은 단계:** 6 WebSocket, 국내 차트 완전 지원(원화 체결 저장 포함).

## 토스 직접 연동 6단계: 실시간 — 2026-10-20

- **사용자 결정:** 체결 틱은 별도 틱 저장소에 저장한다. 주문이 체결되면 원장을 자동으로 동기화한다.
- **변경:** 결정 기록은 [ADR 0019](decisions/0019-toss-realtime-relay-and-tick-store.md)에 있다.
  - **중계:** API가 토스 WebSocket을 하나만 열고 `WS /v1/realtime`으로 화면에 중계한다(앱이 열려 있을 때만).
    - 화면에 보이는 차트 종목의 체결·호가와 내 주문을 구독한다.
    - 60초마다 PING을 보내고, 끊기면 지수 백오프로 재연결한다.
    - 다른 사이트의 접속은 Origin 검사로 막는다.
  - **이벤트:** 새 계약 `realtime_event`(체결·호가·주문·원장 저장·상태)를 추가했다.
  - **원장 자동 동기화:** 연결·재연결할 때와 주문이 종료될 때, 최근 4일 주문을 REST로 다시 읽어 원장에 저장한다.
  - **틱 저장소:** `data/ticks/…` Parquet에 저장하며, 모든 틱에 `lossy_stream`을 표시한다.
    - 거래소 시각과 수신 시각을 함께 기록한다.
    - 봉·차트·백테스트에는 쓰지 않는다.
    - `GET /v1/market/ticks`로 조회한다.
  - **화면:**
    - 실시간 시세 패널이 받은 체결·호가를 바로 반영하고, `실시간`/`5초마다 조회`를 표시한다.
    - 주문 변경과 원장 저장을 알림으로 보여 준다.
    - 리플레이 중에는 구독하지 않는다.
- **검증:**
  - `tests/market_data/test_toss_stream.py` 3개: 틱 저장(유실 표시, 뉴욕 날짜, 경로), 허브(선언, 중계·종목별 필터, 틱 저장, 원장 재동기화, 계약), Origin 차단·키 없음.
  - 화면 테스트에 실시간 체결 반영과 주문 알림을 추가했다. 데스크톱 119개 통과.
  - 실연결 20초(임시 폴더, 원장 동기화 비활성화):
    - 7개 토픽을 구독했고 거부는 없었다.
    - 체결 50건, 호가 21건을 받았고 모두 계약에 맞았다.
    - 틱 50건을 저장했다.
- **남은 단계:** 국내 차트 완전 지원(KRX 캘린더·KRW·국내 세션, 원화 체결 원장·국내 틱).

## 국내 주식 K1: 기반 — 2026-10-20

- **사용자 결정:** 세션은 정규(KRX 09:00–15:30)와 확장(NXT 08:00–09:00, 15:30–20:00)으로 나눈다. 작업은 3단계 PR(K1 기반, K2 화면, K3 원장·실시간)로 진행한다.
- **변경:** 결정 기록은 [ADR 0020](decisions/0020-korean-market-foundation.md)에 있다.
  - 캘린더를 `ExchangeCalendar`로 일반화했다(미국은 버전 문자열까지 그대로). 국내는 XKRX/XKOS이며, KST 08:00·20:00에 확장 시간을 둔다.
  - 국내 종목 ID는 `kr:<6자리>:<xkrx|xkos>:2026`이다. 계약에 `kr_equity`·`KRW`·세션 `all_day`를 추가했다.
  - 봉 출처에 실제 쓴 캘린더를 기록하고, 집계는 종목별 캘린더로 한다. 저장 파티션은 거래소 날짜(KST) 기준이다.
  - `MarketRouter`가 국내 종목은 토스(`toss-kr`, KRX+NXT 합산 표시)로, 나머지는 기존 공급업체로 보낸다.
  - 토스 국내 일봉·주봉은 08:00–20:00 하루 전체이므로 `all_day`로 표시한다. 범위에 봉 전체가 들어가는 세션만 포함한다.
- **검증:**
  - `tests/market_data/test_korean_market.py` 6개: ID, 국내 분봉·일봉, KST 파티션, 라우팅, API 집계·세션.
  - 캘린더 테스트에 국내 시간·휴장·늦은 개장·XKOS를 추가했다.
  - 실조회(005930, 10/20):
    - 분봉 720/720 complete(프리 60, 정규 390, 애프터 270), 30분봉 24, 일봉 31, 주봉 8.
    - 일봉 시고저종이 08:00–20:00 분봉과 일치했다(정규장 종가 211,000과 다름).
- **남은 단계:** K2 화면(국내 종목 검색, KST 시간축, 원화 표시), K3 원장·실시간(원화 체결, 국내 틱).

## 국내 주식 K2: 화면 — 2026-10-20

- **변경:** 결정 기록은 [ADR 0020](decisions/0020-korean-market-foundation.md) K2에 있다.
  - **국내 종목 디렉터리:** 토스 코스피·코스닥 목록(약 4,300개, 한글명)을 하루 단위로 보관한다.
  - **검색:** 미국과 국내 종목을 함께 검색하고 한 순위로 정렬한다. 티커·종목코드·영문명·한글명으로 찾는다.
  - **시세·종목 정보:** 국내 종목도 지원하고, 출처를 `toss-kr`로 표시한다.
  - **화면:** 종목에 따라 시간대(ET/KST), 캘린더(XNAS/XKRX), 가격 자릿수(센트/원)를 정한다(`market-zone.ts`).
    - 차트 시간축·십자선·가격축, VWAP 일 기준, 시계·리플레이 시각, 시세·종목 정보 패널(국내 장 시간 포함)에 적용한다.
  - **보안 로그:** 실시간 중계가 다른 출처의 접속을 거부하면 그 출처를 로그에 남긴다.
- **검증:**
  - 국내 테스트에 디렉터리·통합 검색·국내 시세 API를 추가했다.
  - 화면 테스트에 국내 종목 선택 시 XKRX 세션 조회와 KST 시계를 추가했다. 데스크톱 120개 통과.
  - 브라우저에서 "삼성전자" 검색 → 005930 → 10/20 5분봉을 확인했다.
    - 144/144 complete, 원 단위, KST 축·시계.
    - 관심종목 정규장 종가 211,000(−3.87%).
- **남은 단계:** K3 원장·실시간(원화 체결 원장, 국내 실시간 구독·틱, 수동 기록·CSV의 KRW).

## 국내 주식 K3: 원장·실시간 — 2026-10-20

- **변경:** 결정 기록은 [ADR 0020](decisions/0020-korean-market-foundation.md) K3에 있다.
  - **원화 원장:** 수동·가상·가져오기·증권사 API·증권사 확정 체결 계약에 KRW를 추가했다. 종목 시장과 통화가 맞는지 두 검증기가 모두 검사한다(국내=KRW, 미국=USD).
  - **시간:** 수동 기록과 가상 청산 시각은 거래소 현지 시각으로 받는다(국내는 KST). CSV는 KST로도 읽는다. 매매일지가 국내 종목을 받는다.
  - **증권사 동기화:** 원화 주문을 국내 종목 ID로 저장한다. 건너뛴 사유 `not_us`를 `unsupported_currency`로 바꿨다.
  - **실시간:** 국내 종목코드를 `trade:kr`/`orderbook:kr`로 구독한다(코드는 숫자로, 티커는 영문으로 시작해 구분된다). 국내 틱은 KST 날짜로 저장한다.
  - **화면:**
    - 체결 시각 라벨이 시장 시간대를 따른다.
    - 가져오기 화면에 KST를 추가했다.
    - 손익 요약에 통화를 표시한다.
- **검증:**
  - 국내 테스트 10개: 원화 계약 검사, KST 수동 기록, 국내 실시간·틱 등.
  - 데스크톱 120개 통과.
  - 실연결(임시 일지, 확인 후 삭제):
    - 국내 구독 응답에서 거부가 없었다.
    - 전체 동기화에서 원화 1,668건(kr: ID)과 달러 4,042건을 저장했고 충돌은 없었다.
- **국내 주식 차트 완전 지원(K1–K3) 완료.**

## Phase 5B B1: 백테스트 엔진 — 2026-10-21

- **변경:** 결정 기록은 [ADR 0021](decisions/0021-backtest-engine.md)에 있다.
  - **전략:** 규칙 전략(조건 조합·지표·손절/익절·수량 방식)과 Python 전략(`on_bar(ctx)`)을 버전별로 저장한다. 버전은 수정하지 않고 SHA-256을 남긴다.
  - **Python 격리:** 별도 프로세스, 빈 환경 변수, 임시 작업 폴더, CPU·파일 한도, 응답 시간 제한. 감사 훅이 네트워크·하위 프로세스·ctypes·작업 폴더 밖 쓰기를 막는다. 사고 방지용이며 악의적 코드에 대한 보안 경계는 아니다.
  - **지표:** SMA·EMA·WMA·RSI·MACD·볼린저·ATR·스토캐스틱·VWAP·OBV를 차트 공식 그대로 Python으로 옮겼다. 공용 fixture를 Python과 TypeScript가 모두 재현한다(오차 1e-9).
  - **엔진:** 다종목·단일 통화(USD/KRW)·매수 전용. 확정 봉 종가 신호 → 다음 봉 시가 체결, 수수료·슬리피지 반영, Decimal 회계. 한 봉에서 손절·익절이 함께 닿으면 손절로 보고 표시한다. 거부 사유, 지표 정의, 동일 비중 보유 벤치마크를 결과에 남긴다.
  - **입력 고정:** 종목별 출처·피드·수정 여부·리비전·누락 구간·미확정 봉 수·봉 SHA-256. 미수정 주가에서 분할로 보이는 급변은 날짜를 경고한다.
  - **실행:** `backtests.sqlite3`의 대기열을 백그라운드 스레드가 하나씩 실행하고 취소할 수 있다. 백테스트 체결은 매매일지·원장에 쓰지 않는다.
  - **API·계약:** `/v1/backtests/strategies`, `/v1/backtests/runs`(제출 202, 조회, 결과, 취소). 계약 5종 추가, `backtest_fill`·`backtest_run_config`에 KRW 추가.
- **검증:**
  - 백테스트 테스트 44개(파리티·인과성·체결 시점·비용·손절 모호성·거부·결정성·미래 봉 불변·샌드박스·취소·원장 분리·KRW). TypeScript 파리티 10개.
  - 실데이터(임시 저장소, 확인 후 삭제; 봉은 앱의 `/v1/market/bars`로 조회):
    - 미국 3종목 일봉 701개씩(alpaca-sip), Python RSI 전략 2종목, 국내 005930 일봉 434개(toss-kr, KRW), AAPL 5분봉 9세션 702개. 4건 모두 성공, 거부 0건.
    - NVDA 10:1 분할(2024-06-10)이 미수정 주가에서 −86.6% 손절로 나타났다. 이후 해당 날짜를 경고하도록 보강했고 실데이터에서 NVDA만 정확히 표시됨을 확인했다.
- **남은 위험:** Alpaca 일봉은 미수정(`raw`)이라 분할·배당 구간 결과가 왜곡될 수 있다(경고만 하고 보정하지 않음). 상장폐지 종목은 유니버스에 없다(생존 편향 경고). Python 격리는 같은 사용자 권한의 악의적 코드를 막지 못한다.
- **다음:** B2 백테스트 화면(전략 편집, 실행·취소, 자산·낙폭 곡선, 지표, 거래 표, 차트 표시, 실제 체결과 비교).

## Phase 5B B2: 백테스트 화면 — 2026-10-21

- **변경:** 결정 기록은 [ADR 0021](decisions/0021-backtest-engine.md) "Screen (B2)"에 있다.
  - **진입:** 오른쪽 레일의 "백테스트" 버튼으로 차트 위 큰 창을 연다.
  - **전략 편집:** 규칙은 폼으로 만든다(진입·청산 조건, 엔진 지표 10종과 매개변수, 손절·익절, 크기, 최대 보유 종목). Python은 코드와 JSON 매개변수로 편집한다. 유효성은 API가 판단하고 그 메시지를 보여준다.
  - **실행:** 관심종목에서 종목을 고른다(미국·국내 혼합 불가). "저장 후 실행"은 바뀐 경우에만 새 버전을 만들고 대기열에 넣는다. 실행 중에는 1.5초마다 상태를 확인하고 취소할 수 있다.
  - **결과:** 경고, 지표 카드(정의 툴팁), 자산 곡선과 단순 보유 비교, 연동된 낙폭 곡선, 종목별 표, 거래 표, 미체결 주문과 사유, 고정 입력 데이터, 전략 출력.
  - **차트 표시:** 선택한 실행의 체결을 차트에 분홍색 `BT` 표시로 그린다. 차트 위 칩이 가상 체결임을 알리고 숨길 수 있다. 매매일지에는 기록하지 않는다.
  - **실제 체결 비교:** 같은 종목, 실행 기간(앞뒤 약 1일)의 실제 체결(수동·가져오기·증권사 API)과 매수·매도 건수와 평균가를 나란히 보여준다(참고용, 거래별 대응 아님).
- **검증:**
  - 데스크톱 142개 통과(신규: 모델 5, 표시 1, 화면 5, workspace 1).
  - 브라우저 확인(실제 API·Alpaca):
    - AAPL·MSFT 일봉, EMA10/SMA30 실행이 완료됐다(순수익률 10.57%, 단순 보유 36.16%).
    - 지표·곡선·표가 표시됐다. AAPL 일봉 차트에 `BT buy/sell` 표시와 칩이 나타났다.
    - 확인 중 고친 점:
      - 종목 체크박스가 아래 칸과 겹치던 배치;
      - 전역 `dl` 스타일 때문에 지표 값이 한 글자씩 줄바꿈되던 문제;
      - 가로로 넘치던 열;
      - 스크롤에 가려지던 실행 보기 버튼(행 클릭으로도 선택);
      - 차트가 마우스 휠을 가로채던 문제;
      - 포커스가 창 밖에 있으면 Esc로 닫히지 않던 문제.
    - 미재현: 확인 도중 수동 체결 입력 폼이 한 번 열려 있었다. 취소했고 기록되지 않았다. 같은 순서로 다시 해도 재현되지 않았다.
  - 확인용 전략 1개와 실행 1건이 실제 `SIW_DATA_DIR/backtests.sqlite3`에 남아 있다(매매일지와 무관).
- **남은 위험:** API가 주는 경고 문구는 영어다. 분봉 실행은 정규장만 사용한다.
- **Phase 5B(B1·B2) 완료.**

## 백테스트 후속: 수정주가·경고 한국어화 — 2026-10-21

- **변경:** 결정 기록은 [ADR 0021](decisions/0021-backtest-engine.md) 후속 절에 있다.
  - **수정주가:** 실행 설정에 `adjustment`(raw·split_adjusted·fully_adjusted)를 추가했다.
    - 미국 일봉·주봉은 Alpaca 수정 일봉을 쓴다(`adjustment=split|all`).
    - 수정 일봉은 캐시하지 않고, 받은 날짜를 리비전에 남긴다(`asof-날짜`).
    - 분봉과 국내(토스) 실행은 미수정만 쓴다.
    - 화면 기본값은 분할 수정이다. API 기본값은 raw로 유지했다.
  - **경고 한국어화:** 결과에 `warning_details`(코드·종목·개수·날짜)를 추가했고, 화면이 언어에 맞게 표시한다. "1 bars" 같은 복수형 문장도 고쳤다.
  - **정리:** 브라우저 확인용으로 실제 저장소에 남아 있던 전략·실행을 삭제했다.
- **검증:**
  - Python 백테스트·일봉 테스트 57개(수정 일봉 라벨·리비전, 캐시 우회, 국내 거부, 주봉 집계, 실행 고정·경고 코드, 분봉 거부) 통과.
  - 데스크톱 화면 테스트 6개(국내 종목은 미수정 고정) 통과.
  - 실연결(실제 API, 확인 후 행 삭제), NVDA 2024-01~2026-10:
    - 미수정: 단순 보유 −56.5%, 최악 거래 −86.6%(분할 착시).
    - 분할 수정: 단순 보유 +334.9%, 최악 거래 −13.9%.
    - 분할·배당 수정: +336.1%.
- **남은 위험:** 수정주가는 이벤트 이전 가격 수준이 실제와 다르다(절대 가격 조건에 영향, 경고로 표시). 토스 국내 수정 캔들의 의미는 아래 국내 수정주가 절에서 확인했다.

## 백테스트 후속: 국내 수정주가(토스) — 2026-10-21

- **확인(실연결, 읽기 전용, 개수·비율만 출력):** 토스 국내 일봉 `adjusted=true`와 `false`를 2017-01~2026-10 구간에서 비교했다. 자세한 근거는 [ADR 0021](decisions/0021-backtest-engine.md) 후속 절에 있다.
  - 삼성전자 50:1 분할(2018-05-04): 계수가 정확히 50이었다. 배당(2020 특별배당 포함)에서는 계수 변화가 없었다.
  - KT&G: 9년간 계수 1이었다. 현금배당은 반영하지 않는다.
  - 대한항공: 유상증자 권리락 3회가 반영됐다.
  - 에코프로비엠: 무상증자 계수가 0.2507로, 명목 비율과 달랐다.
  - 카카오: 5:1 분할 계수가 4.98256이었다. 코스피 이전일(2017-07-10)에 원인 불명의 0.4% 단계가 있었다.
  - **판단:** KRX식 수정주가다. 분할·무상·유상증자를 반영하고, 계수는 거래소 기준가를 따르며, 배당은 빠진다.
- **변경:**
  - **어댑터:** 토스 국내 어댑터에 `get_adjusted_daily_bars`를 추가했다(`split_adjusted`만 허용).
    - 수정 일봉은 캐시하지 않는다. 리비전은 `toss-kr-adjusted-r1:1d:split:asof-날짜`다.
    - `fully_adjusted`와 토스 미국 수정 캔들(미확인)은 거부한다.
    - 라우터·캐시 래퍼·실행기 연결은 기존 경로를 그대로 쓴다.
  - **실행 검증:** 원화 실행의 `fully_adjusted`를 거부한다.
  - **화면:** 국내 일봉·주봉 실행에서 분할 수정(기본값)과 미수정을 고를 수 있다. 반영 범위(배당 제외)를 안내한다.
- **검증:**
  - Python 국내·백테스트·브로커 테스트 통과(신규: 수정 라벨·리비전·`adjusted=true` 요청, 배당·미국 거부, 라우팅·캐시 미기록, 원화 `fully_adjusted` 거부).
  - 데스크톱 화면 테스트: 국내 종목은 분할 수정·미수정만 선택 가능하다.
  - 실연결(백테스트 입력 로더, 삼성전자 2018):
    - 미수정: 처음 대비 마지막 종가 비율 0.0152, 분할 의심 2018-05-03.
    - 분할 수정: 0.7585, 분할 의심 없음, `adjusted_prices` 경고만 남았다.
    - 주봉도 같았다.
  - `./scripts/verify.sh` 통과: Python 452개, 데스크톱 153개, mypy·ruff·빌드.
- **남은 위험:**
  - 국내 `split_adjusted`는 유상·무상증자까지 포함한다.
  - 계수가 명목 비율과 최대 약 0.5% 다르고, 원인 불명 단계가 드물게 있다.
  - 배당 수정 국내 데이터 출처는 없다.
  - 미수정 주봉에서는 분할 의심 감지가 동작하지 않는다(기존 동작).

## Phase 5: 차트·매매 복기 Agent — 2026-10-21

- **사용자 결정:** 로컬 근거 + Claude 해설, 복기 대상은 차트 시점·실제 체결·백테스트 모두. 결정 기록은 [ADR 0022](decisions/0022-chart-trade-review-agent.md)에 있다.
- **변경:**
  - **근거(로컬·결정적):** 사실마다 코드·값·단위·영문 문장을 두고, 사후 정보(hindsight)는 따로 표시한다.
    - 차트 시점: `as_of`와 봉 종료 시각으로 시점 이후 데이터를 막는다. SMA·RSI·ATR·거래량·VWAP를 쓴다.
    - 실제 체결: FIFO 손익, 일중 범위 내 체결 위치, 진입 전 지표, 보유 중 MFE/MAE와 청산 후 움직임(사후).
    - 백테스트: 단순 보유 대비, 거래 수 신뢰도(30건 미만 경고), 최고 거래 비중, 비용 비중, 거부 사유, 원 결과의 경고.
  - **Claude 해설(요청 시):**
    - 키 화면의 새 `anthropic` 키(키체인 보관)가 있을 때만 쓴다.
    - 근거 사실만 보내고, 계좌·키·기록 ID는 보내지 않는다. 화면에서 보낼 내용을 먼저 볼 수 있다.
    - JSON 응답(요약·추론·가설·질문·불확실성)을 근거와 분리해 쌓는다.
    - 매매 권고는 금지하고, 투자 조언이 아님을 표시한다.
  - **저장·API:**
    - `agents.sqlite3`에 둔다. 근거는 불변이고 해설은 추가만 한다.
    - 엔드포인트: `/v1/agents/review/{chart,trades,backtest}`, `/v1/agents/analyses`(목록·상세·보낼 내용·해설).
    - 계약 `agent_analysis`에는 공통 `agent_evidence`가 포함된다.
  - **화면:**
    - 오른쪽 레일 "복기 Agent" 패널: 현재 차트, 리플레이 중에는 리플레이 시점 기준.
    - 실제 체결 선택 복기.
    - 캔들 팝업의 "Agent에게 묻기": 그 캔들의 종료 시각 기준.
    - 백테스트 결과의 "Agent 비평".
    - 키 화면 이름을 "API 키 관리"로 바꿨다.
- **검증:**
  - Python 433개 통과(신규 Agent 6: 시점 차단·결정성, FIFO·사후 표시·원장 미기록, 백테스트 근거, 키 없음 409·전송 내용 일치, Claude 응답 형식 거부·401 처리).
  - 데스크톱 148개 통과(신규 5: 패널·전송 내용·해설·키 없음·캔들 질문).
  - 실연결(실제 API, 확인 후 분석·백테스트 행 삭제):
    - 차트 시점(미국 일봉·국내 일봉·미국 5분봉)과 백테스트 비평이 정상 생성됐다.
    - 보낼 내용에 ID가 없음을 확인했다. 키가 없을 때 해설 요청은 409와 키 등록 안내로 끝난다.
    - 브라우저에서 캔들 → "Agent에게 묻기" → 근거 표시를 확인했다.
  - **미검증:** Claude 해설 실호출(Anthropic 키 미등록). 실제 체결 복기 실데이터(실제 매매일지 원장이 비어 있음, 테스트로만 검증).
- **남은 위험:** 장중 분석은 해당 세션 봉만 사용한다(긴 지표 누락). 체결 복기는 매수 포지션 FIFO만 다룬다(공매도 미지원).

## Phase 4: Python 사용자 지표 — 2026-10-21

- **사용자 결정:** Claude 해설(ADR 0022) 실사용은 후순위로 미룬다. 기능은 구현돼 있으며 키 등록 후 쓸 수 있다. 다음 작업으로 Python 사용자 지표를 골랐다. 결정 기록은 [ADR 0023](decisions/0023-python-custom-indicators.md)에 있다.
- **변경:**
  - **작성 방식:** `NAME`·`INPUTS`·`OUTPUTS`·`PANE`과 `update(ctx)`를 쓴다.
    - `ctx`는 현재 봉, 그때까지의 봉(이후 봉을 읽으면 오류), `params`, `state`, 내장 지표 값(`ctx.indicator`, 차트와 같은 공식)을 준다.
  - **미래 차단:** 봉마다 `update`를 호출하는 구조라 이후 봉을 볼 수 없다. 내장 지표는 인과성이 테스트로 보장된 계열만 쓴다.
  - **실행:** 전략과 같은 샌드박스(빈 환경, 임시 폴더, 자원 한도, 감사 훅)에서 계산당 30초, 최대 5,000봉까지 실행한다. 출력은 유한한 숫자 또는 None인지, 이름·길이가 맞는지 검사한다.
  - **버전:** 저장할 때 샌드박스에서 3봉으로 시험 실행해 설명을 검사하고, 바뀐 경우에만 새 버전을 만든다(SHA-256).
    - 차트 인스턴스는 `py:<id>@<버전>`으로 고정되어, 새 버전을 저장해도 기존 차트는 그대로다.
    - 저장소는 `indicators.sqlite3`다.
  - **API:** `GET/POST /v1/indicators/custom`, `POST /v1/indicators/custom/{id}/compute`. 계약 `custom_indicator`, 목록, 결과를 추가했다.
  - **화면:**
    - 보조지표 창에 "Python" 분류를 추가했다(새 지표, 편집 ✎).
    - 템플릿이 들어 있는 편집기에서 "저장 후 차트에 추가"할 수 있다.
    - 값은 차트의 봉(워밍업 포함, 리플레이 중에는 그 시점까지)으로 API가 계산한다. 같은 봉·설정이면 캐시를 재사용한다.
    - 실패하면 차트에 오류 문장을 표시하고, 0으로 그리지 않는다.
    - 저장된 레이아웃은 정의를 불러오기 전에도 Python 지표를 버리지 않는다.
- **검증:**
  - Python 450개 통과(신규 14: 저장·버전, 잘못된 코드 6종 거부, 엔진 RSI와 일치·뒤 봉 추가 시 앞 값 불변, 다음 봉 읽기·NaN·미선언 출력·문자열·무한 루프 거부, 설정·봉 검사).
  - 데스크톱 153개 통과(신규 5).
  - 브라우저(실제 API·Alpaca): AAPL 일봉에서 템플릿 지표를 저장하고 추가하자 별도 창에 그려지고 범례에 값이 표시됐다. 확인 후 차트에서 지우고 저장소 행도 삭제했다.
  - 확인 중 고친 것: 같은 코드를 다시 저장할 때 저장소 잠금이 교착되던 문제, 에포크 시각이 값 한도에 걸리던 문제.
- **남은 범위:** 백테스트 규칙에서 Python 지표 사용(`py:id@버전` 고정), Agent·타인 작성 코드의 승인 흐름, 더 강한 격리.

## Phase 4 후속: 백테스트 규칙의 Python 지표 — 2026-10-21

- **범위:** ADR 0023 "Revisit when"의 첫 항목. 브랜치 `feat/backtest-python-indicators`(미커밋).
- **변경:**
  - **피연산자:** 규칙 전략에서 `{"indicator": "py:<id>@<버전>", "params": {...}, "output": "<이름>"}`을 쓸 수 있다. 저장 시 `indicators.sqlite3`에서 그 버전이 있는지, 설정 이름·숫자, 출력 이름을 검사한다. 지표 저장소가 없으면 거부한다.
  - **계산:** 실행 전에 종목별 확정 봉으로 `runner.compute`를 (버전·설정마다) 한 번 호출한다. 같은 샌드박스에서 봉마다 `update(ctx)`가 실행되어 미래 봉을 볼 수 없다. 종목당 확정 봉은 5,000개까지다(지표 한도).
  - **고정:** 결과에 `custom_indicators`(type·ID·버전·이름·코드 SHA-256)를 추가했다(계약 선택 필드). 실행 전에 저장된 코드의 해시를 다시 확인한다.
  - **실패:** 지표 오류는 지표 type·종목·사유를 담아 실행을 실패로 끝낸다.
  - **화면:** 규칙 폼의 지표 목록에 저장된 Python 지표의 최신 버전을 보여준다(이전 버전에 고정된 규칙은 그 버전을 유지). 결과 화면에 "고정된 Python 지표"(버전·해시)를 표시한다.
  - **문서:** ADR 0023에 "In backtest rules" 절을 추가하고 "Revisit when"을 갱신했다. ADR 0021 폼 설명을 고쳤다.
- **검증:**
  - `./scripts/verify.sh`: Ruff·mypy·pytest·계약 생성까지 통과했고, `prettier --check .`가 다른 세션의 `.claude/worktrees/` 안 파일(이 작업과 무관, git 무시 경로) 때문에 멈췄다. 그 경로만 빼고 같은 단계(Prettier, ESLint, typecheck, Vitest, build)를 실행해 통과했다.
  - Python 456개 통과(신규 6: 저장 검사, 내장 SMA와 같은 체결·고정 정보, 재실행 동일·뒤 봉 추가 시 앞 신호 불변, 지표 오류 사유, API 실행·고정, API 실패 사유).
  - 계약 66개, 데스크톱 155개 통과(신규 2: 목록·라벨·왕복 JSON, 화면에서 Python 지표 선택 후 저장).
  - **미실행:** 실제 API·브라우저에서의 수동 확인.
- **남은 위험:** 종목당 확정 봉이 5,000개를 넘는 분봉 백테스트는 Python 지표를 쓰면 실패한다. 화면이 지표 목록을 불러오기 전에 Python 지표를 쓴 전략을 열면 폼 대신 JSON으로 보인다. Python 전략(`on_bar`)에서 저장된 Python 지표 호출은 아직 없다.

## Phase 6: 증권사 체결 대사 — 2026-10-21

- **범위:** 증권사 API 체결 원장(토스)을 수동 입력·거래내역서 가져오기와 비교하는 조회 전용 대사. 결정 기록은 [ADR 0025](decisions/0025-broker-reconciliation.md)에 있다.
- **변경:**
  - **모듈:** `services/journal/reconcile.py`.
    - 종목·방향별로 수량 일치, 가격 허용 오차(기본 10bp, Decimal), 시각 창이 겹치는지를 본다.
    - 시각 창은 정밀도별로 다르다. 정확=그 시각, 분=1분, 대략=±3시간, 미상=기록 시각 이전 전체. 증권사 주문은 주문 시각부터 마지막 체결까지다.
    - 단계: 주문번호 연결(거래내역서 행의 체결 ID = 토스 주문번호) → 1:1 일치(가까운 순 + 증가 경로로 최대 개수) → 한 주문의 여러 체결 합산 → 같은 시각대의 수량·가격 불일치.
    - 시각이 대략·미상인 기록이 여러 주문에 맞으면 배정하지 않는다(`ambiguous_candidates`). 미상 기록은 불일치로 짝짓지 않는다.
    - 분류: 일치, 불일치, 증권사에만, 수동에만, 거래내역서에만. 사유 코드와 상대 기록별 수량 차이·가격 차이(bp)를 함께 준다.
    - **원장은 바꾸지 않는다.** 결과는 요청마다 계산하는 보기이고 저장하지 않는다. 수동 기록의 `evidence_status`도 그대로다.
  - **API:** `GET /v1/trades/reconciliation?instrument_id=&since=&price_tolerance_bps=`. 계약 `trade_reconciliation_response`(`view: "computed"`)를 추가했다. `since`는 결과만 거르고, 경계 직전 기록도 상대로는 계속 쓴다.
  - **화면:**
    - 매매일지 패널의 원장 아래에 "대사" 구역을 추가했다(영어·한국어).
    - 상태별 개수와 확인이 필요한 기록을 보여 주며, "전체 기록"도 고를 수 있다. 상대 기록과 차이, 사유를 표시하고 "다시 계산" 버튼이 있다.
    - 원장이 다시 로드되면(동기화·가져오기 후) 함께 다시 계산한다.
- **검증:**
  - `COREPACK_ENABLE_DOWNLOAD_PROMPT=0 UV_CACHE_DIR=.uv-cache ./scripts/verify.sh` 통과.
    - Python: 대사 단위 13개와 API 6개를 추가했다. API 테스트는 대사 전후 journal 파일 바이트가 같은지 확인한다.
    - 데스크톱: 신규 4개, 매매일지 패널 확인 1개.
  - **실연결:** 실제 API의 `create_app()`과 임시 매매일지(사용자 원장 아님)를 썼고, 개수만 출력했다.
    - 토스 동기화로 주문 5,710건을 저장했다(미체결 1,417, 종목 미확인 506 제외).
    - 그중 4건을 본뜬 수동 기록 4건(1건은 수량 +1)과 시각 미상 기록 1건을 넣고 대사했다.
    - 결과는 일치 6, 불일치 2, 증권사에만 5,706, 수동에만 1이고, 대사 전후 journal 파일이 같았다.
    - 계산 시간은 0.07초였다.
    - 부하 확인으로 주문 5,710건 전체를 분 단위 수동 기록으로 본떴더니 11,420건이 모두 일치했다. 처음 구현(탐욕 배정)에서는 20쌍이 남아 증가 경로 단계를 추가했다.
  - **미검증:** 실제 사용자 수동·거래내역서 기록과의 대사(실제 원장이 비어 있음). 토스 주문번호가 들어 있는 거래내역서 CSV.
- **남은 위험:**
  - 여러 체결 합산은 주문 창 안의 같은 방향 기록 전부를 쓰므로, 무관한 체결이 섞이면 그 주문은 불일치로 보인다.
  - 겹치는 동일 주문 사이의 짝은 최대 개수지만 시간 거리 최소는 보장하지 않는다.
  - 일치를 사용자가 확정하는 기능(추가 전용 확인 원장)은 아직 없다.

## Phase 6A: 공시·다크풀 근거 Agent — 2026-10-21

- **사용자 결정:** SEC EDGAR(티커 맵·Submissions·CompanyFacts)와 FINRA 주간 ATS 집계를 공식 무료 출처로 승인했다. FINRA는 무료 Public 자격 증명을 키 화면에 등록해 쓴다. 뉴스 크롤링·유료 피드·스크래핑은 쓰지 않는다. 결정 기록은 [ADR 0024](decisions/0024-filings-and-dark-pool-agents.md)에 있다.
- **변경:**
  - **수집기(`services/public_sources`):** `SIW_DATA_DIR/public_sources.sqlite3`에 레코드별 리비전을 쌓는다. 게시 시각과 최초 관측 시각을 따로 두고, 바뀐 레코드는 덮어쓰지 않고 새 리비전으로 저장한다. `source_record` 계약으로 내보낼 수 있다.
    - SEC: `SIW_SEC_USER_AGENT`가 없으면 아무것도 보내지 않는다. 초당 5회 이하, 6시간 캐시, 실패 시 캐시 사용과 그 사실 표시.
    - FINRA: 키 화면의 새 `finra` 키(Client ID·Secret, 키체인). 토큰은 메모리에만 둔다.
  - **공시 Agent(`filings_review`):** 기준 시각 이전에 EDGAR가 접수한 공시만 쓴다(접수 시각, 초 단위). 기간 내 공시와 8-K 항목명, 양식별 수, 최근 10-Q·10-K, XBRL 분기 매출·희석 EPS와 전년 동기 대비. 기준 이후 공시는 요청할 때만 사후 정보로 표시한다.
  - **다크풀 Agent(`dark_pool_review`):** 기준일 **전날까지** 게시된 FINRA 주만 쓴다(게시일만 있어 같은 날은 가정하지 않음). ATS 거래량·건수, 장외 거래량 중 ATS 비중, 과거 평균 대비·백분위, 평균 거래 규모. 전체 거래량 대비 비중은 통합 피드(`alpaca-sip`) 봉일 때만 계산한다. "지연된 주간 집계이며 방향·가격·장중 시각이 없다"는 문장을 항상 붙인다.
  - **저장·API:** 기존 `agents.sqlite3`·`agent_analysis` 계약을 쓴다(대상 `filings`·`dark_pool` 추가). `POST /v1/agents/review/filings`, `POST /v1/agents/review/dark-pool`. 설정 누락은 409(`sec_user_agent_missing`, `finra_credential_missing`), 출처 장애는 502, 국내 종목은 422.
  - **화면:** 복기 Agent 패널에 공시·다크풀 절(영어·한국어), 사후 공시 포함 선택, 설정 누락 안내.
- **검증:**
  - `./scripts/verify.sh` 통과: Python 465개(병합 전 기준, 신규 15: EDGAR 접수 시각 보정, 기준 시각 차단·사후 표시·XBRL 시점·User-Agent 없으면 미전송·캐시 재사용과 장애 시 캐시·리비전·호출 간격·FINRA 게시일 차단·통합 피드 조건·자격 증명 없으면 미전송·API 저장·오류 코드), 계약 67개, 데스크톱 156개(신규 3).
  - 테스트는 모두 mock HTTP이며 네트워크를 쓰지 않는다.
  - **SEC 실연결(2026-10-21, 사용자 승인, 개수만 출력):** AAPL 기준 요청 3건(티커 맵·Submissions·CompanyFacts), 공시 1,001건 저장, 사실 44개(최근 90일 공시 33건, 10-Q·10-K, 매출·EPS와 전년 대비), 누락 0. User-Agent는 환경 변수로만 전달했다.
    - 발견·수정: Submissions JSON의 `acceptanceDateTime`은 `Z` 표기지만 공시 헤더(동부시간)보다 동부 오프셋만큼(여름 4시간·겨울 5시간) 늦다. 헤더 4건으로 확인하고 파서가 오프셋을 뺀다. 보정 후 1,001건 모두 EDGAR 운영시간(06–22시 ET) 안이다.
  - **FINRA 실연결:** 미완료. 사용자가 FINRA Public 자격 증명을 발급해 키 화면에 등록했고 Client ID는 콘솔과 일치하지만, 토큰 요청이 `400 invalid_client`로 거부됐다(Secret 두 번 재설정 후에도 동일). Secret 재설정 후 다시 확인한다. FINRA 데이터는 받지 못했다.
- **남은 위험:** EDGAR가 접수 시각 표기를 고치면 보정이 틀어진다(테스트에 실제 쌍 고정, 운영시간 밖 비율로 감지 가능). FINRA 게시 시각은 날짜만 있어 보수적으로 하루 늦게 본다. 뉴스·News Cycle Agent와 국내 공시(DART)는 범위 밖이다.

## Phase 4: 수식 지표 — 2026-10-21

- **사용자 결정:** 지표 후속 작업 네 가지(수식 지표, 백테스트 지표 확장, Python 편집기 개선, 다른 시간대 지표)를 모두 진행한다. 첫 번째로 수식 지표를 한다. 결정 기록은 [ADR 0026](decisions/0026-formula-indicators.md)에 있다.
- **변경:**
  - **수식 언어:** 가격 필드, `+ - * /`, 괄호, 허용 목록 함수 20개를 쓴다.
    - 창 길이는 1~500의 정수 상수이고, `shift`는 과거만 본다.
    - 손으로 쓴 파서로 트리를 만들어 계산한다(`eval` 없음).
    - 오류는 글자 위치와 함께 알려 준다.
  - **차트:**
    - 인스턴스 `{type: "formula", formula, label, pane}`를 차트별로 저장한다.
    - 같은 이름의 내장 지표와 같은 공식을 쓴다.
    - 수식이 읽는 과거 봉 수만큼 워밍업한다.
    - 잘못된 수식은 그리지 않는다.
  - **화면:** 보조지표 창의 "수식" 분류에서 새 수식을 만들고(입력하면서 검증), 범례의 ⚙로 이름·수식·표시 위치를 고친다.
- **검증:**
  - 데스크톱 179개 통과. 신규 17개는 다음을 확인한다.
    - SMA·EMA·RSI·ATR이 내장 값과 일치한다.
    - 연산 우선순위가 맞고, 0으로 나누기·log 음수는 값 없음이 된다.
    - 뒤 봉을 추가해도 앞 값이 바뀌지 않는다.
    - 오류 9종을 설명한다.
    - 워밍업 길이가 맞다.
    - 화면에서 추가·거부·편집이 동작한다.
  - 브라우저(실제 API): AAPL 일봉에 예시 수식 `(ema(close, 10) - sma(close, 30)) / atr(14)`가 별도 창에 그려졌다. 확인 후 차트에서 지웠다.
- **남은 범위:** 백테스트 규칙에서 수식을 쓰려면 Python 평가기와 파리티 fixture가 필요하다. 조건식과 다중 출력은 아직 없다.

## 백테스트 지표 확장 — 2026-10-21

- **변경:** 백테스트 엔진이 쓰는 지표를 10개에서 차트 내장 지표 34개 전체로 늘렸다. Python 엔진, 규칙 빌더, Python 전략·지표의 `ctx.indicator`, Agent가 모두 같은 지표를 쓴다.
  - **새로 옮긴 지표:** VWMA, HMA, KC, DC, ENV, SAR, Supertrend, 일목, StochRSI, CCI, %R, MOM, ROC, AO, TRIX, UO, Aroon, DMI, BBW, %B, HV, MFI, CMF, 거래량 MA.
  - **미래 데이터 차단:** 일목 후행스팬은 나중 종가를 과거에 그리는 것이라 백테스트에 제공하지 않는다(엔진 출력에서 제외, 화면 선택지에서 제외).
  - **설정 검사:** 소수 설정(배수, SAR 단계, Supertrend 배수, 연간 봉 수)은 범위를 검사한다.
- **검증:**
  - 공용 파리티 fixture를 34개 전체(일부는 기본값이 아닌 설정)로 재생성했고, TypeScript 34개가 1e-9로 일치했다.
  - Python 테스트(34개 지표 인과성 포함) 통과. 데스크톱 204개 통과.

## Python 지표 편집기 개선 — 2026-10-21

- **변경:** [ADR 0023](decisions/0023-python-custom-indicators.md) 편집기 후속.
  - **미리보기:** 저장하지 않은 코드를 활성 차트의 봉(워밍업 포함, 리플레이 중에는 그 시점까지)으로 샌드박스에서 계산하고, 출력별 값 개수와 마지막 5개 값을 보여 준다. 아무것도 저장하지 않는다.
    - API는 `POST /v1/indicators/custom/preview`, 계약은 `custom_indicator_preview`다.
  - **오류 줄:** 샌드박스 오류의 `(line N)`을 읽어 해당 코드 줄을 함께 보여 준다.
  - **새 버전으로 올리기:** 차트에 이전 버전이 있으면 범례에 "↑vN" 버튼이 나타난다. 같은 이름의 설정은 유지하고 새 설정은 기본값으로 채운다.
- **검증:**
  - Python 미리보기 테스트(엔진 RSI와 일치, 저장 안 됨, 오류 줄) 통과. 데스크톱 206개 통과(신규 2: 미리보기·오류 줄, 버전 올리기).
  - 브라우저(실제 API): AAPL 일봉 봉 1,196개로 템플릿 미리보기 1,177개 값을 확인했고, 저장하지 않고 닫았다.

## 큰 시간대 지표 — 2026-10-21

- **변경:** 결정 기록은 [ADR 0027](decisions/0027-higher-timeframe-indicators.md)에 있다.
  - **설정:** 보조지표 설정의 "시간대"에서 분봉 차트는 1D·1W, 일봉 차트는 1W를 고를 수 있다. 내장·수식 지표에 적용하며, Python 지표는 차트 봉만 쓴다.
  - **미래 차단:** 확정된 큰 봉만 쓴다(오늘 진행 중인 일봉 제외). 각 차트 봉에는 그 봉이 끝날 때까지 공개된(`max(구간 끝, available_at)`) 마지막 큰 봉의 값만 반영한다. 그래서 값은 하루·한 주가 마감·공개된 뒤에 계단식으로 바뀐다.
  - **데이터:** 큰 봉은 워밍업(가장 긴 기간의 3배)까지 포함해 받고 요청별로 캐시한다. 범례에 "· 1D"처럼 표시하고, 실패하면 차트에 오류를 표시한다.
- **검증:**
  - 데스크톱 212개 통과. 신규 6개는 다음을 확인한다.
    - 확정 봉만 쓰는지, 공개 전 값이 숨겨지는지, 정렬이 맞는지.
    - Python 지표가 제외되는지, 워밍업 범위가 맞는지.
    - 화면에서 1D 지정 후 실제 값이 그려지는지. 이 테스트는 수정 전 코드에서 실패하는 것을 확인했다.
  - 브라우저(실제 API): AAPL 5분봉에 일봉 수식 지표가 그려졌다(10/9 봉에는 10/8 일봉 값 0.7531, 10/8 봉에는 10/7 값). 확인 후 지표를 지우고 차트를 1D로 되돌렸다.
  - 확인 중 고친 것: 큰 봉 응답이 다시 그리기를 일으키지 않던 문제(메모 의존성 누락)와, 요청 중 응답이 버려지던 경쟁 상태.

## 피보나치 도구 보강(TradingView 방식) — 2026-10-21

- **사용자 요청:** TradingView처럼 피보나치 되돌림과 확장을 지원한다.
- **변경:**
  - **되돌림:**
    - 기본 레벨을 TradingView와 같게 했다: 0~1과 확장 레벨 1.618·2.618·3.618·4.236.
    - 레벨 사이를 색 띠로 칠하고, 레벨별 색과 "비율 (가격)" 라벨을 붙인다.
    - 선은 두 기준점 사이에 그린다.
  - **추세 기반 피보나치 확장(새 도구, 3번 클릭):**
    - A→B 움직임을 C에서 투영한다(가격 = C + (B−A)×비율). A→B, B→C를 점선으로 그린다.
    - 그리는 동안 미리보기를 보여 준다.
  - **설정(그림 선택 시 패널):**
    - 레벨: 0.65, 1.272, 1.414, 2 등 15개 선택지와 사용자 레벨(쉼표 구분, −10~10).
    - 왼쪽·오른쪽 연장, 반전, 가격 표시, 배경. 바꾸면 바로 저장된다.
  - **호환:** 옵션이 생기기 전에 저장된 되돌림은 예전 모양(7개 레벨, 오른쪽 끝까지, 배경 없음)을 유지한다.
- **검증:**
  - 데스크톱 218개 통과. 신규 6개: 확장 레벨, 반전, 투영 계산, 예전 그림 호환, 저장값 정리, 3번 클릭 생성·설정 변경.
  - 브라우저(실제 API, AAPL 일봉): 확장과 되돌림이 그려지고 설정이 바로 반영됐다. 확인 후 그림을 지웠다.
  - 확인 중 고친 것: 설정 패널 클릭이 아래 차트로 통과해 실제 체결 입력 창이 열리던 문제. 그림 레이어가 클릭을 통과시키는데 패널이 그 속성을 물려받았기 때문이다. 앞서 원인을 찾지 못했던 체결 입력 창도 같은 원인으로 보인다. 입력 창은 기록 없이 닫았다.

## Electron 패키징 — 2026-10-22

- **사용자 요청:** Electron으로 패키징한다. 결정 기록은 [ADR 0028](decisions/0028-electron-desktop-shell.md)에 있다.
- **변경:**
  - `apps/electron`(pnpm 작업공간에서 제외, npm 별도 잠금 파일)을 추가했다. 화면은 웹과 같은 빌드를 `siw://app` 출처로 띄운다.
  - **로컬 API:** 실행 중이면 그대로 쓰고, 아니면 패키징한 저장소에서 `uv run uvicorn`으로 띄운다. 앱을 끌 때는 앱이 띄운 API만 끈다. 로그는 `~/Library/Logs/siw-electron/api.log`에 남긴다.
  - **CORS:** API가 `siw://app`을 항상 허용한다. `.env`의 `SIW_CORS_ORIGINS`가 기본값을 대체해도 허용하며, 웹 페이지는 이 출처를 보낼 수 없다.
  - **빌드 명령(`apps/electron`에서):**
    - `npm run package`는 `.app`을 만든다.
    - `npm run dist`는 `.dmg`를 만든다(x64, 서명 없음).
- **검증:**
  - `release/mac/Stock Intelligence Workstation.app`(257 MB)과 `release/Stock Intelligence Workstation-0.4.0-dev.dmg`(약 124 MB)를 만들었다.
  - API를 끈 상태에서 앱을 실행하자 7초 안에 API를 직접 띄웠다. 화면이 종목·봉·원장·지표를 불러왔고 실시간 연결도 수락됐다. 앱을 끄자 앱이 띄운 API도 꺼졌다.
  - Python 테스트 신규 2개(Electron 출처 허용, 다른 출처 거부)가 통과했다.
  - 확인 중 고친 것: `.env`가 허용 출처를 대체해 `siw://app`이 막히던 문제.
- **남은 위험:**
  - 이 저장소 위치에 의존한다(옮기면 다시 패키징하거나 `SIW_REPO_DIR`를 지정한다).
  - 서명·공증이 없어 Gatekeeper 경고가 뜬다.
  - 기본 아이콘을 쓴다.
  - Tauri 네이티브 빌드는 여전히 미검증이다.

## 키움 미국주식 체결 동기화 — 2026-10-22

- **사용자 요청:** 키움증권(계좌 끝자리 2859) 또는 토스증권의 거래내역을 가져온다. 결정 기록은 [ADR 0029](decisions/0029-kiwoom-order-history-sync.md)에 있다.
- **변경:**
  - `services/market_data/kiwoom_account.py`를 추가했다. 미국 영업일마다 ust21150(일별 주문체결내역)을 읽어 체결된 주문을 브로커 원장에 주문 단위로 한 번씩 저장한다. 토큰 발급과 이 api-id만 호출할 수 있다.
  - **API:** `POST /v1/brokers/kiwoom/sync?since=&until=`(기본 최근 1년, 한 번에 최대 400일, 최대 5년 전까지).
  - **화면:** 계좌 패널에 "키움 미국주식 체결 동기화" 버튼과 시작일 입력을 추가했다.
  - **계약:**
    - `broker_order_execution`의 `broker_id`에 kiwoom을, `time_meaning`에 reported_fill_time을 추가했다.
    - `fees`/`commission`/`tax`는 null을 허용한다(보고되지 않음, 0으로 간주하지 않음).
    - `broker_sync_result.skipped.time_unresolved`와 `ledger_position.fees_unreported_count`(처음엔 ID 목록이었으나 응답이 커져 건수로 바꿈)를 추가했다.
  - **시각:** 키움 시각은 서울 시계이고 `ord_dt`는 미국 거래일이다(실데이터로 확인). 미국 04:00–20:00 안에 놓을 수 없는 주문(주간거래 등)은 추정하지 않고 건너뛴다.
- **검증:**
  - `tests/market_data/test_kiwoom_account.py` 신규 5개(가짜 키움 서버, 네트워크 없음)와 기존 토스·원장 테스트가 통과했다.
  - **실계좌 동기화(건수만 기록):** 최근 1년(2025-10-23~2026-10-22) 동안 체결 주문 28,530건을 읽어 23,844건을 저장했다.
    - 종목 디렉터리에 없는 종목 3,667건과 시각 미확정 1,019건은 저장하지 않았다.
    - 충돌은 없었다. 소요 시간은 3분 37초다.
  - **이전 연도(구간별 저장 건수):**
    - 2024-10-24~2025-10-22: 30,903건(디렉터리에 없는 종목 17,233건)
    - 2023-10-25~2024-10-23: 0건
    - 2022-10-26~2023-10-24: 11,666건(디렉터리에 없는 종목 12,854건, 시각 미확정 100건)
    - 2021-10-24~2022-10-25: 0건
  - **5년 합계:** 저장 66,413건, 디렉터리에 없는 종목 33,754건, 시각 미확정 1,119건, 충돌 0건이다.
    - 디렉터리에 없는 종목은 상장폐지·종목명 변경으로 보이나 **미검증**이다. 이 종목들의 보관 방식은 후속 결정이 필요하다.
- **남은 위험:**
  - 수수료·세금은 키움 주문 단위로 제공되지 않아 비어 있다.
  - 브로커 원장 포지션이 토스·키움 계좌를 종목별로 합쳐 FIFO 손익을 계산한다(계좌별 보기는 후속 작업).
  - 주간거래 주문과 디렉터리에 없는 종목은 저장되지 않았다.
  - 국내(KRW) 거래내역(kt00007/kt00015)은 아직 동기화하지 않는다.
- **Alpaca:** 사용자가 유료 요금제라고 알렸다. SIP 최신 구간(end=now) 조회가 성공하는 것을 확인했다. 앱은 `SIW_ALPACA_DELAY_MINUTES`가 없어 여전히 15분 지연을 적용한다. `.env`에 0을 넣을지는 사용자 승인 대기 중이다.

## 백테스트 시작일 + 봉 개수 — 2026-10-22

- **사용자 요청:** 특정 시점부터 5,000봉을 불러와 테스트한다. 오늘 기준 이전 데이터만으로 제한할 필요는 없다. 결정 기록은 [ADR 0021](decisions/0021-backtest-engine.md)의 2026-10-22 후속 절에 있다.
- **변경:**
  - **실행 설정:** `bar_count`(1~5,000)를 추가했다. 시작일부터 앞으로 세션을 읽어 처음 N봉을 쓴다. 분봉은 날짜 범위의 60세션 제한 대신 최대 1,000세션을 적용한다.
  - **입력 고정값:** `bar_count_requested`를 추가했다. 봉이 부족하면 `fewer_bars` 경고를 낸다.
  - **화면:** 기간 방식(시작일~종료일 / 시작일부터 봉 개수)을 고를 수 있다.
- **검증:** `tests/backtest/test_bar_count.py` 신규 4개와 화면 테스트 1개가 통과했다.
  - 분봉: 필요한 세션만 읽는다(200봉에 3세션). 5,000봉은 60세션을 넘겨도 된다.
  - 일봉: 2015년 시작 실행이 120봉에서 멈춘다.
  - 부족 경고가 나오고, 잘못된 값은 거부한다.
- **남은 위험:**
  - 1,000세션 상한 때문에 60분봉 5,000개(약 715세션)보다 긴 봉은 다 채우지 못할 수 있다(경고로 표시).
  - 첫 실행은 원본에서 세션마다 데이터를 받아 오래 걸릴 수 있다.

## 미국 데이마켓(Blue Ocean, Alpaca BOATS) — 2026-10-22

- **사용자 요청:** Alpaca의 데이마켓 시세 제공 여부를 확인하고 ADR을 먼저 쓴 뒤 구현한다. 결정 기록은 [ADR 0030](decisions/0030-day-market-session.md)에 있다.
- **확인(읽기 전용, 사용자 유료 키, AAPL):**
  - `feed=boats`는 200으로 응답했고 20:00–04:00 뉴욕 시각 봉을 실시간으로 준다.
  - `feed=overnight`는 400(invalid feed)이다.
  - SIP에는 이 시간대 봉이 없다.
- **변경:**
  - **세션 정책:** `services/market_data/overnight.py`를 추가했다. 거래일 D의 데이마켓은 전날 20:00부터 D 04:00(뉴욕 시각)까지이고, D가 휴장일이면 없다. 정책 버전은 `us-overnight-boats-v1-2026-10-22`다.
  - **Alpaca:** `get_overnight_bars`를 추가했다(`feed=boats`, 1m 또는 세션 시작 기준 Nm). SIP 봉·캐시와 섞지 않는다.
  - **API:** `GET /v1/market/overnight-bars`(최대 14거래일)를 추가했다. 계약의 `price_bar.session`에 `overnight`를 추가했다.
  - **화면:** 미국 차트에 "+ 데이마켓"을 추가했다. 본 봉은 장전·장후 포함으로 받고, 데이마켓은 파란·보라색 별도 시리즈로 그린다. 지표·마커·백테스트에는 쓰지 않는다.
  - **키움:** 미국 04:00–20:00에 놓이지 않는 주문을 `ord_dt` 거래일의 데이마켓에 배치한다(실데이터 근거는 ADR 0030). 미국 휴장일로 표시된 주문은 시각 미확정으로 남긴다.
- **검증:**
  - **신규 테스트:** `tests/market_data/test_overnight.py` 6개와 화면 테스트 1개를 추가했다. 키움 테스트는 데이마켓 배치와 휴장일 거부를 반영해 고쳤다.
  - **실 API(2026-10-22 06:21Z):** AAPL 3세션에서 1m 288봉, 15m 75봉(기대 96)을 받았다. 마지막 봉은 요청 1분 전이었다.
  - 키움 재동기화(실계좌, 건수만 기록): 이전에 시각 미확정이던 1,119건 중 977건을 데이마켓 시각으로 새로 저장했다(2025-10~2026-10 923건, 2022-10~2023-10 54건). 나머지 142건은 디렉터리에 없는 종목이다. 시각 미확정은 0건, 충돌은 0건이며 기존 기록은 바뀌지 않았다.
- **남은 위험:**
  - 휴장일·조기폐장 전후의 Blue Ocean 일정은 가정이다.
  - 데이마켓은 거래가 드물어 빈 봉이 많다.
  - 차트에는 최근 14거래일의 데이마켓만 그린다.

## 브로커 원장 포지션 계좌별 분리 — 2026-10-22

- **사용자 요청:** 남은 작업을 순서대로 진행한다(1번).
- **변경:** 브로커 원장 포지션을 계좌·종목별 FIFO로 계산한다(`account_positions`).
  - 한 계좌의 매도가 다른 계좌의 매수를 닫지 않는다.
  - 계약 `ledger_position`에 `account_ref`를 추가했다.
  - 화면은 "[토스 계좌 N]" 또는 "[키움]"으로 표시한다.
- **검증:**
  - 계좌 분리 테스트 1개를 추가했다.
  - **실데이터(건수만):** 체결 73,100건으로 포지션 1,765개를 0.4초 만에 계산했다.
    - 키움은 1,303종목 모두 보유 0이고 음수가 없다.
    - 토스는 462종목 중 12개를 보유 중이다. 8개는 매도가 매수보다 많은데, 데이터 이전 매수나 입고로 보인다(**미검증**).
    - 두 증권사에서 함께 거래한 종목은 112개다. 이전에는 이 종목들이 합산되어 손익이 틀렸다.
- **남은 위험:** Agent 매매 복기는 사용자가 고른 체결을 계좌 구분 없이 FIFO로 맞춘다(Agent 작업 보류 중).

## 실데이터 매매 복기·대사 점검 — 2026-10-22

- **사용자 요청:** 남은 작업을 순서대로 진행한다(2번). 브라우저(`localhost:5173`)와 실제 API로 점검했다.
- **발견하고 고친 것:**
  - **브로커 원장 응답 과대:** 응답이 53 MB, 26초였다. 작업공간이 시작할 때 전체 원장(73,100건)을 불러왔다.
    - `GET /v1/trades/broker`에 `limit`을, 응답에 `execution_count`를 추가했다. 매매일지는 최근 500건만 받는다(0.7 MB, 1.8초). 포지션은 여전히 전체 기준이다.
    - 차트·백테스트·Agent에 쓰는 실제 체결은 필요한 종목만 `instrument_id`로 받는다. 차트 종목과 백테스트 결과 종목이 대상이다.
  - **대사 응답 과대:** 25 MB, 12.5초였다. `limit`과 `record_count`를 추가했고, 화면은 최근 500건만 받는다(2.2초). 요약은 전체 기준이다.
  - **수수료 미제공 표시:** `fees_unreported_execution_ids`가 키움 체결 ID를 전부 담아 응답을 키웠다. `fees_unreported_count`(건수)로 바꿨다.
  - **분봉 집계 500 오류(WORX 등 거래 적은 종목):** 집계봉의 마지막 분에 체결이 없으면 `available_at`이 구간 종료보다 앞서 계약 검사에 걸렸다. Alpaca 지연을 0으로 바꾼 뒤 드러났다.
    - 완결 집계봉은 구간 종료에 원천 지연을 더한 시각보다 먼저 공개되지 않도록 고쳤다.
    - 부분 봉은 기존처럼 도착한 분봉의 시각을 따른다(as-of 의미 유지).
  - **차트 표시:** 증권사 API 체결을 주황색 "B" 마커로 그린다. 시각을 모르는 주문은 그리지 않는다.
  - **매매일지 표시:**
    - 제목·설명을 토스·키움 기준으로 고쳤다.
    - 디렉터리에 없는 종목도 ID 대신 티커로 보인다.
    - 수수료 미제공은 "수수료 차감 전"으로 표시한다.
    - 청산 종목은 접어 둔다.
    - 평균가는 소수 4자리까지만 보인다.
- **실데이터 결과(건수만):**
  - 대사 결과는 전부 "증권사 API만"(73,100건)이다. 수동 입력·파일 가져오기 체결이 없어 맞출 대상이 없기 때문이며, 정상 동작이다.
  - WORX 5분봉·데이마켓·체결 마커가 함께 표시되는 것을 화면에서 확인했다.
- **남은 위험:**
  - 한 봉에 주문이 많으면 마커 글자가 겹친다.
  - 대사는 수동·파일 체결이 생겨야 의미 있는 결과가 나온다.

## 백테스트 지정가·스톱 주문 — 2026-10-22

- **사용자 요청:** 남은 작업을 순서대로 진행한다(3번의 앞부분). 결정 기록은 [ADR 0031](decisions/0031-backtest-order-types.md)에 있다.
- **변경:**
  - **주문 형식:** 주문 의도에 `order_type`(market/limit/stop), 가격(`limit_price`/`stop_price` 또는 신호 종가 대비 `offset_pct`), `expire_bars`를 받는다.
  - **체결 규칙:** 지정가는 가격을 뚫고 거래돼야 체결되며(닿기만 하면 안 됨) 슬리피지가 없다. 스톱은 스톱가 또는 갭 시가에 슬리피지를 적용한다. 기간 안에 체결되지 않으면 `expired`다.
  - **룰 전략:** `entry_order`/`exit_order`를 설정할 수 있다. 값이 없으면 시장가이고 기존 지문은 그대로다.
  - **화면:** 룰 편집기에서 진입·청산 주문 유형, 오프셋 %, 유효 봉 수를 고를 수 있다.
  - **봉 안 진입:** 봉 안에서 진입한 포지션이 같은 봉에서 손절되면 `same_bar_as_entry`로 표시한다.
- **검증:** `tests/backtest/test_order_types.py` 8개와 화면 모델 테스트 1개를 추가했다. 기존 백테스트 테스트는 그대로 통과한다.
- **남은 작업:** 워크포워드(다음 PR).

## 백테스트 워크포워드 — 2026-10-22

- **사용자 요청:** 남은 작업을 순서대로 진행한다(3번의 뒷부분). 결정 기록은 [ADR 0032](decisions/0032-walk-forward.md)에 있다.
- **변경:**
  - **설정:** 실행 설정에 `walk_forward`를 추가했다. 표본 내·외 봉 수, 선택 기준, 바꿔 볼 값(최대 3개, 각 20개 값)을 지정한다.
  - **구간 선택:** 구간마다 표본 내 봉에서 모든 조합을 시험한다. 가장 좋은 조합(동점이면 먼저 나온 조합)으로 표본 외 봉을 거래한다.
  - **결과:** 엔진 한 번 실행으로 표본 외 봉만 계산하고, 구간별 선택값과 점수를 `walk_forward`에 담는다.
  - **엔진:** `trade_window`를 추가했다. 그 앞의 봉은 지표 준비에만 쓴다.
  - **범위:** 룰 전략만 지원한다. 최대 50구간, 백테스트 400회까지다.
  - **화면:** 실행 설정에 워크포워드 칸(룰 전략의 숫자 목록에서 선택)을 추가했고, 결과에 구간 표를 보여 준다.
- **검증:**
  - `tests/backtest/test_walk_forward.py` 5개(API 실행 포함)와 화면 모델 테스트 1개를 추가했다.
  - 1,200일봉, 16구간 × 9조합이 1.7초에 끝났다.
- **남은 위험:** Python 전략은 지원하지 않는다. 구간 경계에서 포지션은 유지되고 다음 구간의 청산 규칙을 따른다.

## 티커 변경·상장폐지 추적 — 2026-10-22

- **사용자 요청:** 티커 변경과 상장폐지를 추적한다. 티커가 바뀌면 바뀐 티커의 차트에 체결을 붙이고, 상장폐지 종목은 지우지 않고 상장폐지 전까지의 차트 데이터를 볼 수 있게 한다. 결정 기록은 [ADR 0033](decisions/0033-ticker-changes-and-delistings.md)에 있다.
- **변경:**
  - `services/market_data/symbol_history.py`를 추가했다. Alpaca 기업 행위(`name_change`, 상장폐지성 행위)와 비활성 종목 목록을 하루 한 번 받는다.
  - **키움 체결 해석:**
    - 체결일 이후의 티커 변경을 따라가서, 현재 상장 티커가 있으면 그 차트에 붙인다(`instrument_resolution: renamed`).
    - 없으면 상장폐지 종목으로 남기고(`delisted`) `delisted-instruments.json`에 등록한다. `asof`는 마지막 체결일이다.
  - **차트:** 상장폐지 종목은 Alpaca `asof`로 그 회사의 봉을 읽고, 데이터는 거래소 상장 기간에서 끝난다. 바뀐 티커는 이전 티커 시절의 봉까지 포함한다(Alpaca 매핑).
  - **검색:** 상장폐지 종목을 "상장폐지 · 마지막 거래 날짜"로, 옛 티커 검색 시 새 티커를 "구 티커"로 보여 준다. 상장폐지 종목을 고르면 마지막 거래일로 연다.
  - **동기화 결과:** `unknown_symbols`를 추가했다. 종목 해석은 분당 한 번 만든 표로 한다(기존에는 체결마다 디렉터리 전체를 훑었다).
- **실데이터(건수만):**
  - 미등록 728종목(33,896건) 중 33,894건을 새로 저장했다. 미해결은 2건이다.
  - 상장폐지로 등록된 종목은 545개다. 거래소 미검증(XNAS로 둠)이 539개, OTC 등 다른 티커로 옮긴 종목이 261개다.
  - 차트 확인: DEVS는 2026-06-23까지, WINT는 2026-08-19까지 일봉이 있다. DXF는 2023년 당시 회사의 봉, AIFC는 JAN 시절(2023-03) 봉이 나온다.
  - **충돌 595건:** 이전에 저장된 체결 중 지금 다른 회사가 쓰는 티커로 붙어 있던 것이다(JAN→AIFC 220건, DXF→DXFFY 375건). 저장된 기록은 자동으로 바꾸지 않아 사용자 결정을 기다린다.
- **남은 위험:**
  - 키움이 거래소를 알려 주지 않아 상장폐지 종목 대부분의 거래소는 미검증이다.
  - 토스 동기화에는 아직 적용하지 않았다(토스는 현재 티커를 보고하는 것으로 보이나 **미검증**).
- **후속 수정(같은 날):**
  - 상장폐지 종목의 티커를 지금 다른 회사가 쓰는 경우(예: DXF), 상장폐지 종목에 연도 접미사가 붙은 별도 ID(`us:dxf.d23:xnas:2026`)를 준다.
  - `asof`와 Alpaca 티커는 종목 ID별로 찾는다. 첫 버전에서는 오늘 DXF 차트가 2023년 회사의 `asof`를 받는 문제가 있었다.
  - 이미 만든 등록부에서 재사용 티커 23개를 지우고 다시 동기화했다.
- **충돌 595건 정정(사용자 승인, 2026-10-22):**
  - 백업: `SIW_DATA_DIR/backups/journal-before-rekey-20261022T121638Z.sqlite3`(106,994행).
  - 충돌한 키움 기록 595건을 지우고, 해당 주문일 59일만 다시 동기화해 새 해석으로 저장했다. 결과는 새로 저장 595건, 충돌 0건, 미해결 0건이며 전체 행 수는 106,994로 그대로다.
  - 종목 분포: 상장폐지 별도 ID(예: QH.D26 116건, MF.D23 76건, DXF.D24 52건)와 바뀐 티커(예: PWM→AURE 71건, SEV→SSM 55건, JAN→AIFC 30건) 등이다.
  - 체결 ID는 계좌와 주문번호에서 만들어 그대로이므로, 다른 곳의 참조도 유지된다.
  - 상장폐지 이후 OTC 시세는 없다.

## 분봉 차트의 더 큰 분봉 지표 — 2026-10-22

- **사용자 요청:** 기능 개발 항목을 모두 진행한다(1/5). 결정 기록은 [ADR 0034](decisions/0034-intraday-higher-timeframes.md)에 있다.
- **변경:**
  - N분봉 차트의 지표 설정에서 N보다 큰 15·30·60·120·240분봉을 고를 수 있다(일봉·주봉과 함께).
  - 더 큰 분봉은 세션 단위로 하나씩 받는다. 차트의 세션에 워밍업(룩백 × 3) 세션을 더해 최대 120세션까지 받는다.
  - 확정되어 공개된 봉만 차트 봉 끝 시각 기준으로 쓴다(ADR 0027과 같은 규칙).
- **검증:** 선택지·세션 수 모델 테스트와, 5분봉 차트에서 60분 SMA를 고르면 세션별로 60분봉을 요청하는 화면 테스트를 추가했다.

## 봉 개수 백테스트의 세션 상한 — 2026-10-22

- **사용자 요청:** 기능 개발 항목을 모두 진행한다(2/5).
- **변경:** 봉 개수 모드의 세션 상한을 고정 1,000에서 타임프레임 기준으로 바꿨다. 정규장에서 요청한 봉 수를 채울 세션에 25% 여유를 더하고 최대 3,000세션까지다. 이제 60분봉 이상도 5,000봉을 채울 수 있다.
- **검증:** 상한 계산 테스트를 추가했다.

## 워크포워드: Python 전략과 고정 시작 구간 — 2026-10-22

- **사용자 요청:** 기능 개발 항목을 모두 진행한다(3/5). 결정 기록은 [ADR 0032](decisions/0032-walk-forward.md)를 갱신했다.
- **변경:**
  - **Python 전략:** `params`(`/params/<이름>`)를 바꿔 가며 워크포워드할 수 있다. 실행마다 새 샌드박스 프로세스를 쓰고, 최대 100회다.
  - **워밍업:** 첫 봉보다 늦게 시작하는 실행은 앞선 봉을 history로만 먼저 받는다(`on_bar` 호출 없음).
  - **고정 시작 구간:** `anchored` 옵션을 추가했다. 표본 내 구간이 항상 첫 봉부터 시작해 늘어난다.
  - **화면:** Python 전략에도 워크포워드 칸이 나오고, 고정 시작 체크박스를 추가했다.
- **검증:** 고정 시작 구간 테스트와, Python 워크포워드 테스트(새 프로세스에서도 history가 이어지는지 확인)를 추가했다.

## 키움 국내주식 거래내역 — 2026-10-22

- **사용자 요청:** 기능 개발 항목을 모두 진행한다(4/5). [ADR 0029](decisions/0029-kiwoom-order-history-sync.md)에 후속 절을 추가했다.
- **변경:**
  - kt00015(위탁종합거래내역) 정산 라인을 브로커 원장에 저장한다. `settlement_line`, 체결·주문 시각 없음, `trade_date`, 수수료·세금을 포함한다.
  - **API:** `POST /v1/brokers/kiwoom/sync-domestic`(한 번에 최대 1년)를 추가했다.
  - **화면:** 계좌 패널에 "키움 국내주식 거래 동기화" 버튼을 추가했고, 매매일지에 날짜만 있는 체결은 "날짜(시각 미제공)"로 표시한다.
  - **토큰:** 다른 클라이언트가 토큰을 새로 받아 기존 토큰이 무효(8005)가 되면 한 번 다시 발급한다.
- **실데이터(건수만):** 국내 거래는 2023년 3월에만 있었다. 1,509라인 중 1,423건을 저장했다. 디렉터리에 없는 7개 코드(86라인)는 건너뛰었고, 충돌은 없다.
- **남은 위험:**
  - 체결 시각이 없어 FIFO 손익 계산에서는 제외된다(미정렬로 표시).
  - 상장폐지된 국내 종목은 추적하지 않는다.

## 키움 미국주식 수수료(정산 라인) — 2026-10-22

- **사용자 요청:** 기능 개발 항목을 모두 진행한다(5/5). [ADR 0029](decisions/0029-kiwoom-order-history-sync.md)에 후속 절을 추가했다.
- **변경:**
  - ust21100(미국주식 거래내역)의 정산 라인별 수수료·세금을 `broker_fee_lines` 테이블(새 마이그레이션 0004)에 저장한다. 주문번호가 없어 주문에는 붙이지 않는다.
  - 포지션은 계좌·종목별 수수료를 합쳐 실현 손익에서 뺀다(`settlement_fee_lines`).
  - **API:** `POST /v1/brokers/kiwoom/sync-fees`를 추가했다.
  - **화면:** 계좌 패널에 "키움 미국주식 수수료 동기화" 버튼을 추가했다.
- **실데이터(건수만):**
  - 수수료가 있는 라인 58,499건을 저장했다(중복으로 온 586건은 한 번만 저장, 미등록 종목 2건).
  - 키움 포지션 2,095개 중 1,898개에 수수료가 반영됐다.
- **남은 위험:**
  - 수수료는 종목·계좌 합계로만 반영한다(주문별 수수료 아님).
  - 브로커 원장 응답이 4.3초로 늘었다(수수료 라인을 모두 읽음).
  - 사양서에는 선택으로 표시된 `krw_repl_skip_yn`이 실제로는 필수다.

## API 기본 포트 8765로 변경 — 2026-10-22

- **사용자 요청:** 포트를 바꾼다. 다른 앱(`localhost:3000`)이 기존 포트 8000의 API에 실시간 연결을 반복 시도해 거절 로그가 쌓였다.
- **변경:** API 기본 주소를 `127.0.0.1:8765`로 바꿨다. 반영한 곳:
  - 화면 기본값(`App.tsx`), `.env.example`, `.claude/launch.json`, README;
  - Electron(`api-process.cjs`, `main.cjs`, `write-config.cjs`), ADR 0028;
  - 사용자 `.env`의 `VITE_API_BASE_URL` 한 줄(승인된 요청, 값은 출력하지 않음).
- **검증:** API를 8765에서 띄웠다. 화면 하단이 "연결됨 · API 주소 http://127.0.0.1:8765"로 표시된다. 8000은 비어 있다.
- **남은 작업:** 이미 만든 Electron `.app`/`.dmg`는 예전 주소(8000)를 담고 있어 다시 패키징해야 한다.

## 데스크톱 배포 개선 — 2026-10-22

- **사용자 요청:** Electron 앱의 아이콘·저장소 위치 의존·서명 준비를 개선하고 Tauri 네이티브 빌드 상태를 확인한다. 결정 기록은 [ADR 0028](decisions/0028-electron-desktop-shell.md)에 추가했다.
- **변경(`apps/electron`):**
  - **아이콘:** `assets/icon.png`(1024×1024, 독자 도안: 어두운 둥근 사각형·캔들 4개·상승선, 브랜드 요소 없음). `scripts/make-icon.cjs`(`npm run icon`)가 의존성 없이 그린다. 빌드에서 `icon.icns`로 바뀌어 `Info.plist`의 `CFBundleIconFile`에 들어간다.
  - **저장소 위치:** `repo-location.cjs`가 `SIW_REPO_DIR` → 사용자가 고른 폴더(`~/Library/Application Support/siw-electron/settings.json`, 경로만) → 패키징 때 기록한 폴더 순서로 찾는다. 모두 유효하지 않으면 첫 실행 때 폴더 선택 창을 띄우고 고른 위치를 기억한다. 앱 메뉴에 "저장소 폴더 변경…"(다음 실행부터 적용)을 추가했다. 틀린 `SIW_REPO_DIR`는 다른 폴더로 바꾸지 않고 오류로 보인다. API가 이미 응답하면 저장소를 찾지 않는다.
  - **API 포트:** 앱이 띄우는 API 포트를 `SIW_API_URL`의 포트에서 정한다(기본 8765).
  - **서명·공증(준비만, 미수행):** 빌드 설정을 `electron-builder.config.cjs`로 옮겼다. `CSC_NAME` 또는 `CSC_LINK`가 있으면 hardened runtime으로 서명하고(`assets/entitlements.mac.plist`: allow-jit만), 그때 `APPLE_*` 자격 증명이 갖춰지면 공증한다. 없으면 이전처럼 서명하지 않는다. 사용자 절차는 `apps/electron/SIGNING.md`.
- **변경(`apps/desktop/src-tauri`):** 같은 도안으로 `tauri icon`이 만든 데스크톱 아이콘 5종(`icons/`)을 넣고 `bundle.icon`에 연결했다. 컴파일은 미검증이다.
- **검증:**
  - `npm test`(`node --test`, 신규 4개: 우선순위, 틀린 env, 옮긴 저장소, 설정 파일 보존) 통과. ESLint·Prettier 통과.
  - `npm run dist` 성공(exit 0): `release/Stock Intelligence Workstation-0.4.0-dev.dmg`(약 124 MB), `.app` 265 MB, 서명 없음("identity explicitly is set to null").
  - 실행 확인(사용자의 8765 API는 건드리지 않고 8799에서):
    - 패키징한 앱: 9초 안에 API를 띄웠고, 앱을 끄자 API도 꺼졌다. asar에 `repo-location.cjs`가 들어 있다.
    - 폴더 선택: 대화 상자만 바꾼 테스트 하네스(별도 사용자 데이터)로, 잘못된 폴더는 경고 후 다시 묻고, 맞는 폴더는 `settings.json`에 저장한 뒤 API를 띄웠다. 메뉴에 "저장소 폴더 변경…"이 있다.
    - 서명 설정: 환경 변수 조합 4가지(없음 / 서명만 / 서명+공증 / 공증 변수만)에서 identity·notarize 값이 의도대로 나왔다.
  - **Tauri:** `tauri build --no-bundle`는 `cargo metadata` 실행 파일이 없어 exit 1. `tauri info`: Xcode 26.3과 Command Line Tools는 있고 rustc·cargo·rustup이 없다. 시스템 소프트웨어 설치는 사용자 승인이 필요해 하지 않았다.
- **미검증:**
  - 실제 폴더 선택 창 화면과 클릭(접근성 권한이 없어 자동 조작 불가, 하네스로 논리만 확인).
  - 실제 서명·공증(사용자 Apple Developer ID 필요), Gatekeeper 통과.
  - Tauri 컴파일·실행 전부. 코드만 보고 찾은 위험: CSP `connect-src`에 `ws://`가 없어 실시간 연결이 막힐 수 있다.
- **남은 작업:** 사용자가 Rust(rustup) 설치를 승인하면 Tauri 빌드를 다시 시도한다. 서명은 `SIGNING.md` 절차대로 사용자가 진행한다.

## 데이터 정확성 점검 — 2026-10-22

- **사용자 요청:** 상장폐지 종목의 거래소, 기록 없는 티커 재사용, 상장폐지 후 OTC 시세, 토스의 티커 변경, 데이마켓 일정을 점검한다(ADR [0030](decisions/0030-day-market-session.md), [0033](decisions/0033-ticker-changes-and-delistings.md)).
- **상장폐지 종목의 거래소(읽기 전용 확인, 건수만):**
  - 방법: Alpaca SIP 체결을 등록부의 `asof`로 조회하면 주 상장 거래소의 테이프(`z`)가 나온다(A=NYSE, B=NYSE American·Arca·Cboe 등, C=Nasdaq). 이미 검증된 7개는 모두 일치했다.
  - 미검증 534개 중 C 435개(XNAS 맞음), A 56개(NYSE), B 38개(Nasdaq 아님, B 안의 거래소는 구분 불가), 데이터 없음 5개.
  - Nasdaq이 아닌 94개는 종목 ID가 바뀌어야 한다. 저장된 키움 체결 3,093건과 수수료 라인 2,230건이 이 ID를 쓴다.
- **NYSE 56개 재키잉(사용자 승인, 2026-10-22):**
  - 백업: `SIW_DATA_DIR/backups/journal-before-venue-rekey-20261022T150820Z.sqlite3`(체결 108,417행, 수수료 라인 59,085행)와 `delisted-instruments-before-venue-rekey-20261022T150820Z.json`.
  - 등록부에서 56개를 XNYS·검증됨으로 바꿨다. 해당 키움 체결 1,375건과 수수료 라인 1,140건을 지우고, 주문일 85일과 정산일 83일만 다시 동기화했다.
  - 결과: 체결 새로 저장 1,375건, 수수료 라인 1,140건, 충돌 0건, 미해결 0건. 전체 행 수는 그대로이고 옛 ID(XNAS)를 쓰는 행은 0건이다.
  - **코드:** 등록부에 검증된 거래소가 있으면 XNAS 기본값보다 우선한다(`symbol_history.py`). 그래서 다시 동기화해도 같은 ID가 나온다.
  - tape B 38개와 데이터 없는 5개는 XNAS·미검증으로 그대로 둔다.
  - **주의:** 사용자 API(8765)는 변경 전 코드와 등록부를 메모리에 들고 있다. 이 PR을 병합한 뒤 API를 다시 시작해야 한다.
- **상장폐지 후 OTC 시세:** 읽기 전용 요청 한 번(`feed=otc`)이 403 "subscription does not permit querying OTC data"로 거절됐다. 현재 구독으로는 쓸 수 없다.
- **토스와 티커 변경:** 저장된 토스 체결 5,710건 중 회사의 티커 변경 전에 체결된 177건(18종목)이 모두 새 티커로 보고됐고, 옛 티커로 보고된 체결은 없다. 토스는 현재 티커를 보고하므로 ADR 0033 해석을 토스에 적용하지 않았다. 토스의 상장폐지 종목 체결은 **미검증**이다.
- **데이마켓 일정:** 토스 시장 달력의 `dayMarket`이 2025–2026년 휴장일·조기 폐장일·서머타임 전환 전후 86개 날짜에서 정책과 모두 일치했다. 정책은 바꾸지 않았다(`us-overnight-boats-v1-2026-10-22` 유지). 이 날짜들을 회귀 테스트로 고정했다. Blue Ocean 자체 달력과의 비교는 **미검증**이다.
- **키움 미해결 2건:** 데이터 사본으로 동기화를 돌려 찾으려 했으나 권한 거부로 멈췄다. **미확인**.
- **기록 없는 티커 재사용 탐지:** 구현하지 않았다. 체결가와 그 시각의 SIP 봉을 비교하는 방식은 아직 평가하지 않았다(**미실행**).
- **검증:** `uv run pytest tests/market_data/test_overnight.py tests/market_data/test_symbol_history.py`, `./scripts/verify.sh`.

## 차트 체결 마커 묶기 · 대사 화면 개선 — 2026-10-23

- **사용자 요청:** 한 봉에 체결이 많으면(WORX 5분봉 등) 마커 글자가 겹쳐 읽을 수 없다. 대사 화면은 수동 기록·거래내역서가 없어 전부 "증권사에만"이라 쓸모가 적다.
- **차트 마커:**
  - 원장(수동·가져오기·증권사 API·백테스트)별로 같은 봉·같은 방향의 마커를 하나로 묶는다. 예: `B buy ×12 (340)`(건수와 총수량, 수량은 문자열 십진 합산). 원장별 색·모양은 그대로이고, 원장끼리는 섞지 않는다. 시각 미상 수동 기록은 따로 묶는다.
  - 화면 확대 정도(봉 간격)에 맞춰, 같은 쪽에서 약 90px 안에 겹치는 라벨은 지운다(마커는 남김). 묶음이 큰 마커가 라벨을 가진다.
  - 세부는 그대로 볼 수 있다: 커서를 올린 봉의 범례에 그 봉의 마커 글자가 나오고, 캔들을 클릭하면 입력 창에 "이 캔들의 기록 N건" 목록(수량·가격·거래소 시각, 시각 순)이 나온다.
- **대사:**
  - API `GET /v1/trades/reconciliation`에 `until`, `from_date`/`to_date`(각 종목 거래소 날짜 기준), `status` 필터를 추가했다. 응답에 `ledger_counts`(필터 전 원장별 건수)와 `breakdown`(종목별·일별 상태 건수)을 추가했다. 여전히 읽기 전용이다(저널 파일 바이트 불변 테스트 유지). 계약·생성 타입·fixture를 갱신했다. [ADR 0025](decisions/0025-broker-reconciliation.md)에 후속 절을 추가했다.
  - 화면: 수동·거래내역서 원장이 모두 비면 그 이유를 안내하고, 종목별 건수 표로 연다. "확인 필요"에서는 "증권사에만" 행을 숨긴다. 형태(종목별/일별/기록 목록), 종목, 시작일·종료일, 상태 필터를 추가했다. 표의 "목록"을 누르면 그 종목/날짜의 기록으로 들어간다. 값이 0인 상태 열은 숨긴다.
- **검증:**
  - `pnpm vitest`(데스크톱) 23개 파일 231개 테스트 통과. 마커 묶기·라벨 솎기·십진 합산 테스트, 대사 화면의 빈 원장 안내·드릴다운 테스트를 추가했다.
  - `pytest tests/journal tests/contracts` 통과(`until`·`status`·`ledger_counts`·`breakdown`·거래소 날짜 필터 테스트 추가).
  - 브라우저: 저널 사본(스크래치 폴더)으로 워크트리 API를 8766에, Vite를 5174에 띄워 확인했다(사용자 API 8765는 건드리지 않음). WORX 2026-10-19 5분봉·1시간봉에서 묶인 마커(`B buy ×9` 등)와 라벨 솎기, 캔들 클릭 시 "이 캔들의 기록 15건" 목록을 확인했다. 대사는 증권사 API 108,417건·종목 2,399개·일 608개로 집계되고, 안내 문구·종목별 표·드릴다운·"증권사에만" 필터가 동작했다.
- **남은 위험:**
  - 대사 응답이 전체 기준 약 4.2초에서 5.0초로 늘었다(집계 추가).
  - 라벨 솎기는 봉 간격으로만 계산하며 라벨 실제 폭은 재지 않는다.
  - 사용자가 말한 "WORX 2026-10-20" 체결은 미국 동부 기준으로는 대부분 2026-10-19 거래일이다(10-20 거래일에는 1건).

## Tauri 네이티브 빌드 — 2026-10-23

- **사용자 요청:** Rust 설치를 승인하고 Tauri 빌드를 진행한다.
- **설치:** 공식 rustup 스크립트로 사용자 영역에만 설치했다(`--profile minimal --no-modify-path`, `~/.rustup`·`~/.cargo`). 셸 설정 파일은 바꾸지 않았으므로 쓰려면 `PATH="$HOME/.cargo/bin:$PATH"`를 붙인다. rustc·cargo 1.99.1, Xcode 26.3.
- **고친 것(`apps/desktop/src-tauri/tauri.conf.json`):**
  - `beforeBuildCommand`/`beforeDevCommand`가 맨 `pnpm`을 불러 exit 127로 실패했다. 저장소 다른 곳처럼 `corepack pnpm`으로 바꿨다.
  - CSP `connect-src`에 `ws://127.0.0.1:*`·`ws://localhost:*`를 더했다. 화면의 실시간 연결(`src/realtime.ts`)이 WebSocket을 쓴다.
  - `bundle.active`를 켜고 대상은 `app`만 둔다(dmg는 Finder 자동화가 필요해 제외).
- **기타:** `Cargo.lock`을 커밋한다(앱 crate). `.gitignore`의 `src-tauri/target/`는 저장소 최상위에만 맞아 `apps/desktop/src-tauri/target`을 무시하지 못했다. `**/src-tauri/target/`로 고치고 생성 파일 `**/src-tauri/gen/`도 무시한다.
- **빌드(`apps/desktop`에서):** `PATH="$HOME/.cargo/bin:$PATH" corepack pnpm exec tauri build` → `src-tauri/target/release/bundle/macos/Stock Intelligence Workstation.app`(10 MB, 서명 없음). 첫 컴파일 1분 53초, 이후 약 30초.
- **검증:**
  - 첫 시도는 `pnpm: command not found`(exit 1), 수정 후 exit 0.
  - 실행 확인: 화면을 8799 API로 향하게 한 임시 빌드로 앱을 띄웠다. 종목·세션·봉 요청이 200으로 응답했고 `WebSocket /v1/realtime`이 수락됐다(Tauri 출처 `tauri://localhost`는 API가 이미 허용). 원장 계열 503은 이 worktree에 저장소 설정(`.env`)이 없기 때문이다. 확인 후 기본 주소(8765)로 다시 빌드했다.
- **Electron과의 차이:** Tauri 앱은 로컬 API를 띄우지 않는다. API가 먼저 실행 중이어야 한다.
- **미검증:** `tauri dev`, 창 화면 자체(스크린샷 없음, API 로그로만 확인), 외부 링크 열기, 서명·공증, Windows.

## 데스크톱 앱 역할 분리 — 2026-10-23

- **사용자 결정:** Tauri는 정해진 전략에 따라 매수·매도를 실행하는 Agent용, Electron은 사용자와 AI가 함께 전략을 연구하고 백테스트하는 용도로 나눈다. 결정 기록은 [ADR 0036](decisions/0036-desktop-app-roles.md)에 있다.
  - 첫 주문 범위는 모의투자만, 첫 주문 증권사는 한국투자(KIS)다.
  - Electron의 주문은 지금은 없고, 나중에 Gate에서 다시 검토할 수 있다.
  - 이번 작업은 결정 기록과 앱 구분까지다. 주문 코드는 없다.
- **변경:**
  - **Tauri:** 이름 `SIW Agent`, 번들 ID `com.pensivetraveler.siw.agent`(이전 `local.siw.desktop`), 창 제목 "SIW Agent — 전략 실행".
  - **Electron:** 이름 `SIW Research`, 번들 ID `com.pensivetraveler.siw.research`, 창 제목 "SIW Research — 전략 연구·백테스트". 공용 화면의 `<title>`이 창 제목을 덮지 않게 했다. 사용자 데이터 폴더는 `siw-electron` 그대로라 기억한 저장소 폴더와 로그 위치가 유지된다.
  - **문서:** AGENTS.md, SYSTEM_SPEC, PROJECT_INSTRUCTIONS의 "자동 주문 범위 밖" 문장에 ADR 0036의 재범위 지정을 반영했다. ADR 0028에 SIW Research 개정을 표시했다.
- **검증:**
  - `npm run dist` exit 0 → `release/SIW Research-0.4.0-dev.dmg`. asar의 `package.json`에 `productName`이 없어 사용자 데이터 폴더가 바뀌지 않음을 확인했다.
  - `tauri build` exit 0 → `SIW Agent.app`.
  - 두 앱의 `Info.plist` 이름·번들 ID를 확인했다. 두 앱을 함께 실행해도 각자 떴고, 실행 중인 8765 API를 그대로 썼다.
- **미검증:** 창 제목이 화면에 실제로 보이는 모습(접근성 권한 없음).
- **남은 작업:**
  - 화면은 아직 두 앱이 같다. 앱별 화면 분리는 단계 1 설계에서 정한다.
  - 단계 1(KIS 모의투자 주문)은 사용자가 별도로 승인해야 착수한다.
  - 기존에 설치한 `/Applications/Stock Intelligence Workstation.app`(이전 이름의 Tauri 빌드)은 사용자가 지운다.

## 상태 변경 양식

- 날짜 / 작업 번호 / 변경 파일:
- 실제 검증 명령과 결과:
- 새로 확정된 사항과 근거:
- 변경한 가정 또는 남은 문제:
- 다음 작업:

## Agent 매매 복기 계좌별 FIFO — 2026-10-22

- **사용자 요청:** 보류 항목을 정리한다. 단순 버그는 고치고, 결정이 필요한 것은 사실을 모아 묻는다. 결정 기록은 [ADR 0022](decisions/0022-chart-trade-review-agent.md)의 Real trades 절에 있다.
- **변경:**
  - **계좌별 FIFO:** 매매 복기 Agent가 고른 체결을 계좌마다 따로 FIFO로 맞춘다. 브로커 원장(`account_positions`)과 같은 규칙이다.
    - 증권사 API 체결은 `broker_account_ref`별, 가져온 체결은 명세서 라벨별, 수동 기록은 한 묶음이다.
    - 계좌가 둘 이상이면 계좌 수와 계좌별 실현 손익을 사실로 추가한다. 이름은 "account 1/2"(첫 체결 순)이다.
    - 계좌 참조는 로컬 `source_refs`(`account:broker:<ref>`)에만 남고 Claude에 보내는 내용에는 없다.
  - **수수료 이중 계산 수정:** 증권사 체결의 `fees`(합계)에 `commission`·`tax`(구성)를 다시 더하던 버그를 고쳤다. 수수료 미제공(키움) 체결은 건수를 불확실성에 적는다.
  - **화면:** Agent 체결 목록의 증권사 체결에 "[토스 계좌 N]"/"[키움]"을 붙인다.
  - Agent 버전을 `chart-trade-review-2`로 올렸다.
- **검증:** 계좌 분리 테스트(Python 1개, 화면 1개)를 추가했다. 한 FIFO였다면 토스 매수를 키움 매도가 닫았을 사례에서 계좌별 결과를 확인한다.
- **남은 위험:** 키움 미국 정산 행 수수료(`broker_fee_lines`)는 Agent 손익에 아직 넣지 않는다(불확실성으로 표시). 통화가 다른 체결을 섞어 고르는 경우는 막지 않는다.
- **보류 유지(사용자 결정):** News·News Cycle·DART Agent와 Claude 코멘터리 확장은 구현하지 않았다.
- **포트 확인:** API(8765)로 "WORX" 검색이 SCWorx 1건을 돌려줬다. 8000·3000 포트에는 수신 중인 프로세스가 없었다.
- **Alpaca 기본 공급업체 선정(사용자 결정):** 근거(유료 SIP·BOATS·기업 행위 사용·실패 사례)를 보고 사용자가 선정을 승인했다. [ADR 0035](decisions/0035-alpaca-default-us-provider.md)를 쓰고 AGENTS.md·PROJECT_INSTRUCTIONS·SYSTEM_SPEC의 "미선정" 문구를 고쳤다. 코드 기본값은 바꾸지 않았다.
