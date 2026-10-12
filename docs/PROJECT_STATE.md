# Project State — 실행 상태 및 미확정 결정

> 이 파일은 **진행 상황을 기록하는 동적 상태 파일**이다. AGENTS.md와 영구적 제품 명세에 임시 상태를 누적하지 않는다.

## 현재 상태 (2026-10-12 기준)

- **Confirmed:** 미국 주식 중심 로컬 우선 워크스테이션. 차트별 독립 지표 및 선택 차트 프리셋 일괄 적용. 실제/수동/가상/백테스트 체결 분리. 1분봉 기본 저장과 소스 출처 무결성.
- **Decision:** Alpaca와 Massive는 동등한 _미선정_ 유료 후보이며 증권사 API/공개 정보 수집원과 병행 검증한다.
- **Runtime verified:** macOS 15.7.9 x86_64, Python 3.10.19, uv 0.12.23, Node 22.23.2, pnpm 11.25.0, Tauri CLI 2.12.2. Rust/Cargo는 설치되지 않았다.
- **Repository:** ZIP 배포 경로를 `git init -b main`으로 초기화하고 Phase 0 local baseline commit을 생성했다. 원격 저장소는 설정되지 않았고 CI 호스팅 실행은 아직 없다.
- **Phase 0 status:** P0-01~P0-07의 코드·계약·로컬 품질 게이트 완료. Tauri native dev/build만 `blocked: prerequisite (cargo missing)`이며 웹 셸은 실행 검증했다.
- **Open questions:** 원격 Git/CI 플랫폼, Rust toolchain 설치 시점, 실제 공급업체 계정 권한/저장 라이선스, 향후 예산. Phase 0은 외부 계정 연결을 요구하거나 수행하지 않았다.
- **Phase 1A status:** `FakeMarketDataProvider`가 합성 AAPL 1분봉을 canonical `MarketBarsResponse`와 `CoverageManifest`로 `GET /v1/market/bars`에 반환한다. 동일 요청은 `fake-aapl-20261009-r1`을 결정적으로 반환하며, 누락은 보간하지 않고 `partial/empty`와 명시적 누락 구간으로 노출한다.
- **Phase 1B status:** 명시적 `SIW_DATA_DIR`에서 SQLite migration·Coverage·수집 작업·revision pin·복구 journal과 content-addressed immutable Parquet cache가 동작한다. cache hit는 upstream을 다시 호출하지 않고, 중복 요청은 파일·Coverage를 늘리지 않는다.
- **Phase 1C status:** XNAS/XNYS 정규장 calendar와 `America/New_York` DST, 2026 휴장·반장, 버전된 extended-hours 정책, session-aligned 5m/15m/30m/60m 집계가 동작한다. 모든 bar는 quality와 source/calendar lineage를 보존한다.
- **Next action:** 사용자 승인 후 실제 Provider PoC 준비 또는 Phase 2 차트 수직 슬라이스 중 우선 경로를 정한다. 실제 Provider adapter·백테스트는 아직 시작하지 않는다.
- **Execution gate:** `docs/EXECUTION_PROCESS.md`에 단계별 완료 조건을 기록했다. 각 단계 결과를 대화창에 보고하고 사용자 승인을 받은 뒤에만 다음 단계로 진행한다.

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

- **Contracts:** `CoverageManifest`와 `MarketBarsResponse`를 JSON Schema 2020-12에 추가하고 Python/TypeScript semantic validator와 공통 valid/invalid fixture를 갱신했다.
- **Provider:** `MarketDataProvider` protocol 및 파일 기반 `FakeMarketDataProvider`를 추가했다. 1분봉·UTC 정렬·단일 pinned revision만 허용하며 중복 canonical identity를 fixture 로딩 시 거부한다.
- **API:** `GET /v1/market/bars`가 종목, UTC 범위, `1m`, 선택적 point-in-time cutoff를 검증한다. 알 수 없는 종목, 역전/비정렬/과도한 범위, 지원하지 않는 timeframe, 미래 cutoff는 구조화 오류로 거부한다.
- **Coverage:** fixture 결측과 cutoff 당시 미입수 bar를 서로 다른 reason으로 기록하며, OHLCV를 발명하거나 다른 source를 혼합하지 않는다.
- **Safety:** 네트워크 호출 차단 상태의 provider 단위 테스트를 통과했다. DB, Parquet, 외부 API, 계정, credential은 사용하지 않았다.
- **Actual API:** `uv run uvicorn services.api.main:app --host 127.0.0.1 --port 8124`로 기동해 합성 범위 요청 HTTP 200/4 bars/complete coverage와 미지원 종목 HTTP 404를 확인하고 정상 종료했다. 샌드박스 내부 최초 포트 bind는 권한 제한으로 실패해 승인된 로컬 실행으로 재검증했다.
- **Quality command:** `UV_CACHE_DIR=/private/tmp/siw-uv-cache ./scripts/verify.sh` 최종 통과 — Ruff/mypy, pytest 44개, Prettier/ESLint/TypeScript typecheck, Vitest 계약 20개/UI 4개, production build, 생성 타입 drift 검사 통과.
- **Remaining limits:** 현재 fixture는 AAPL 4분 합성 표본뿐이고 영속 저장·재시작 복구는 없다. 요청 분 수는 단순 1분 경계로 계산하며 거래소 휴장·DST·반장·세션별 예상 interval 계산은 Phase 1C 범위다. 실제 공급업체 품질·권한·저장권은 계속 `unverified`다.

## Phase 1B acceptance evidence — 2026-10-12

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

## 상태 변경 양식

- 날짜 / 작업 번호 / 변경 파일:
- 실제 검증 명령과 결과:
- 새로 확정된 사항과 근거:
- 변경한 가정 또는 남은 문제:
- 다음 작업:
