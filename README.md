# Stock Intelligence Workstation — Codex Starter v1.5

미국 주식 차트·매매 복기·백테스팅·전문 Agent 연구 환경을 위한 **로컬 개발 착수 패키지**.

**현재 상태:** Phase 0 기반, Phase 1A~1C·1E 시장 데이터 기반, Phase 2A·2B 차트 workspace(차트 2개, 이름 붙인 workspace·레이아웃 템플릿), Phase 3A 수동·가상 거래 원장 구현 완료. FastAPI API, React/TypeScript 차트 workspace, 독립 Collector 진단 경로, 합성 Provider, SQLite/Parquet cache, XNAS/XNYS 거래 캘린더, 공개 API의 session-aligned 5m/15m/30m/60m 집계, 재개 가능한 합성 데이터 backfill이 구성되어 있다. 실제 공급업체는 아직 연결하지 않았으며, 현재 머신에는 Rust/Cargo가 없어 Tauri 2 네이티브 실행만 미검증이다.

## 시작 순서

1. Git 원격 저장소가 있으면 `git clone`으로 받는다. ZIP 배포물이라면 빈 디렉터리에 **전체 압축 해제**한 뒤 저장소 루트(`AGENTS.md`가 있는 위치)에서 `git init`을 실행한다. 원격·초기 커밋·CI 연결은 사용자가 사용할 호스팅을 정한 뒤 명시적으로 구성한다.
2. `git status`가 정상 동작하고 기존 파일이 모두 보이는지 확인한다. 기존 원격이나 사용자 변경이 있으면 덮어쓰지 않는다.
3. Codex를 **그 로컬 폴더에서** 시작한다. 필요한 문서와 현재 환경을 먼저 읽도록 한다.
4. 현재 체크아웃에서는 [docs/README.md](docs/README.md), `PROJECT_STATE.md`, `IMPLEMENTATION_HISTORY.md`를 읽어 완료 범위와 남은 작업을 확인한다.
5. `docs/EXECUTION_PROCESS.md`에서 다음 범위를 정하고 작업한다. `CODEX_PHASE_0_TASK.md`와 `PHASE_0_IMPLEMENTATION_SPEC.md`는 최초 구축 기록·수락 기준이며 완료한 Phase 0을 다시 구현하는 지시가 아니다.

## 참조 문서

- [docs/README.md](docs/README.md): 문서 탐색 시작점
- [docs/IMPLEMENTATION_HISTORY.md](docs/IMPLEMENTATION_HISTORY.md): 현재까지 작업·커밋·검증·남은 제약

- `AGENTS.md`: Codex가 따라야 할 루트 지침 (원본 협업 지침의 핵심 행동 규칙 포함)
- `docs/PROJECT_INSTRUCTIONS.md`: 지속적인 개발 협업·안전 지침
- `docs/SYSTEM_SPEC.md`: 전체 시스템 요구사항
- `docs/IMPLEMENTATION_ROADMAP.md`: 전 단계 개발 순서
- `docs/EXECUTION_PROCESS.md`: 현재 작업 단계와 사용자 승인 Gate
- `docs/DATA_PROVIDER_EVALUATION_v1.5.md`: 공식 문서 조사 (계정/라이선스 미검증)
- `docs/REPOSITORY_STRUCTURE.md`: 디렉터리 구조와 경계
- `docs/PHASE_0_IMPLEMENTATION_SPEC.md`: 완료한 Phase 0의 작업 범위와 검증 기준
- `docs/CODEX_PHASE_0_TASK.md`: Codex 최초 실행 메시지
- `docs/PROJECT_STATE.md`: 실행·결정·차단사항 갱신 위치

## 실행 명령

### 전제조건

- Python 3.10.x, uv 0.12.x
- Node.js 22.x, pnpm 11.x (`pnpm`이 PATH에 없으면 `scripts/verify.sh`가 Node에 포함된 corepack으로 실행한다)
- Tauri 네이티브 실행에는 별도로 Rust/Cargo와 Tauri 2의 macOS 전제조건이 필요하다. 웹 개발 셸은 Rust 없이 실행 가능하다.

### 설치

```bash
uv sync --all-groups
pnpm install --frozen-lockfile
```

### API

```bash
uv run uvicorn services.api.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/version
```

합성 AAPL 1분봉과 Coverage 계약은 다음처럼 확인한다. 이 경로는 로컬 fixture만 읽고 외부 네트워크를 호출하지 않는다.

```bash
curl --get http://127.0.0.1:8000/v1/market/bars \
  --data-urlencode 'instrument_id=us:aapl:xnas:2026' \
  --data-urlencode 'start_utc=2026-10-09T13:30:00Z' \
  --data-urlencode 'end_utc=2026-10-09T13:34:00Z' \
  --data-urlencode 'timeframe=1m'
```

영속 cache를 사용하려면 쓰기 가능한 로컬 디렉터리를 명시한다. 설정하지 않으면 API는 파일을 생성하지 않고 합성 Provider를 직접 사용한다.

```bash
SIW_DATA_DIR=data uv run uvicorn services.api.main:app --host 127.0.0.1 --port 8000
```

`data/metadata.sqlite3`에는 migration·Coverage·수집 작업·revision pin·복구 journal이 저장되고, bar는 `data/bars/` 아래 source/instrument/timeframe/거래소 세션 날짜(`session_date`, America/New_York 기준)/revision별 Parquet part로 저장된다. 여러 프로세스가 같은 디렉터리를 열 수 있으며 `data/owners/`의 프로세스별 잠금으로 살아 있는 프로세스의 작업은 복구 대상에서 제외한다([ADR 0004](docs/decisions/0004-multi-process-store-and-point-in-time-contract.md)). 이 디렉터리는 Git에서 제외되며 네트워크 파일 공유용이 아니다.

### 거래 세션과 집계

- 정규장·휴장·반장은 `exchange-calendars`의 XNAS→XNYS 일정으로 계산한다. 지원 범위는 2000-01-03~2030-12-31로 고정되어 있고, 범위 밖 요청은 `422 invalid_market_data_query`로 거부한다.
- pre/regular/post는 `America/New_York` 기준의 버전된 정책이며 공급업체 entitlement를 의미하지 않는다.
- 5m/15m/30m/60m는 세션 시작에 맞춰 집계하고 세션을 넘겨 합치지 않는다.
- 파생 bar의 `quality`와 `lineage`에는 결측·미확정 상태, source revision, 기대/관측 1분봉 수와 calendar version이 남는다. calendar version은 라이브러리 버전이 아니라 고정 범위 일정의 SHA-256 fingerprint다([ADR 0005](docs/decisions/0005-calendar-fingerprint-query-boundary-and-job-lifecycle.md)).

`GET /v1/market/bars`는 `timeframe=1m|5m|15m|30m|60m`을 지원한다. 큰 timeframe은 같은 cache·cutoff의 1분봉을 요청마다 세션 기준으로 집계한다. 요청 범위는 세션 bucket 경계에 맞아야 하며, 맞지 않으면 422를 반환한다. 일부 1분봉만 있는 bucket은 `quality: partial`로 표시된다([ADR 0007](docs/decisions/0007-aggregated-api-cache-composition-and-backfill.md)).

```bash
curl --get http://127.0.0.1:8000/v1/market/bars \
  --data-urlencode 'instrument_id=us:aapl:xnas:2026' \
  --data-urlencode 'start_utc=2026-10-09T13:30:00Z' \
  --data-urlencode 'end_utc=2026-10-09T13:45:00Z' \
  --data-urlencode 'timeframe=15m'
```

포트 `8000`이 사용 중이면 API와 데스크톱을 같은 대체 포트로 맞춘다.

```bash
uv run uvicorn services.api.main:app --host 127.0.0.1 --port 8123
VITE_API_BASE_URL=http://127.0.0.1:8123 pnpm dev
```

### Desktop 개발 셸

```bash
pnpm dev
```

기본 주소는 `http://127.0.0.1:5173`이다. API(`uv run uvicorn services.api.main:app --port 8000`)에 연결되면 차트 2개가 있는 workspace가 열린다([ADR 0008](docs/decisions/0008-chart-workspace-slice.md)).

- 각 차트는 종목(합성 `SYNTH`, AAPL 표본), timeframe(1m~60m), 세션 날짜, 정규장/연장장을 따로 설정한다.
- 시간 축과 결측 구간은 미국 동부시간(ET)으로 표시한다. 세션 경계는 API의 `GET /v1/market/sessions/{date}`에서 받는다.
- 일부만 관측된 bar와 미확정(provisional) bar는 회색으로 그린다. 각 차트 아래에 Coverage, 품질, 세션, source·revision lineage, 결측 구간이 표시된다.
- `Sync visible time range`를 켜면 두 차트의 보이는 시간 범위가 함께 움직인다.
- workspace는 이름을 붙여 저장·전환·이름 변경·삭제할 수 있다. 이 기기의 브라우저 저장소에 저장되고 새로고침 후 복원된다.
- `Trading Terminal`, `Trade Review` 템플릿은 `Apply to both charts`를 눌러야만 적용된다. 각 차트의 종목·날짜는 유지한 채 설정을 복사하고, `Undo`로 되돌릴 수 있다([ADR 0010](docs/decisions/0010-named-workspaces-and-layout-templates.md)).
- 종목 목록은 `GET /v1/market/instruments`에서 받는다. `SYNTH`는 `uv run python -m services.market_data.synthetic && pnpm format`으로 다시 생성하는, 실존하지 않는 합성 데이터다.

Tauri 2 전제조건을 설치한 환경에서는 `pnpm desktop:tauri`를 사용한다.

### 거래 기록(수동·가상)

API를 `SIW_DATA_DIR=data`로 실행하면 차트의 캔들을 클릭해 두 종류의 기록을 남길 수 있다([ADR 0009](docs/decisions/0009-manual-and-hypothetical-trade-ledgers.md)). 이 기록은 `data/journal.sqlite3`에 시장 데이터와 분리해 저장된다. `SIW_DATA_DIR` 없이 실행하면 기록 API가 503을 반환하고, 화면에도 기록 기능이 꺼져 있다고 표시된다.

- **Actual execution(수동, 미검증)**
  - 사용자가 실제로 체결했다고 주장하는 기록이다. 매수·매도, 수량, 체결가, 수수료, 시간 정밀도, ET 체결 시각을 입력한다.
  - 클릭한 캔들은 참고 정보로만 표시되며, 가격이나 시각을 미리 채우지 않는다. 서머타임 전환 때 존재하지 않거나 모호한 ET 시각은 거부한다.
- **Hypothetical scenario(가상)**
  - 롱/숏 방향, 진입·청산가, 수량, 총수수료, 주당 슬리피지를 입력한다. 진입 시점은 클릭한 캔들이고, 청산 시점은 ET로 입력한다.
- **표시와 P&L**
  - 두 원장은 별도 표와 별도 P&L로 표시하며 합산하지 않는다. 수동 원장은 FIFO 실현손익을 계산하고, 시각을 모르는 체결은 계산에서 뺀다.
  - 차트 마커는 수동 기록이 파란 화살표, 가상 기록이 보라색 원(진입)·사각형(청산)이다.
- **API:** `POST/GET /v1/trades/manual`, `POST/GET /v1/scenarios`. 같은 ID로 같은 요청을 다시 보내면 저장된 기록을 돌려주고, 다른 내용이면 409로 거부한다.
- **계좌:** 계좌 정보는 받지 않는다. 메모에도 계좌번호나 인증 정보를 쓰지 않는다.

### Collector

```bash
uv run python -m services.collector doctor
uv run python -m services.collector run
uv run python -m services.collector backfill --data-dir data \
  --instrument-id us:aapl:xnas:2026 \
  --start-utc 2026-10-09T13:30:00Z --end-utc 2026-10-09T13:40:00Z \
  --chunk-minutes 5
```

`doctor`는 외부 API를 호출하지 않으며 성공 시 JSON과 exit code `0`을 반환한다. `run`은 SIGINT/SIGTERM으로 정상 종료한다.

`backfill`은 합성 Provider만 사용하며, 범위 하나를 재개 가능한 수집 job 하나로 실행한다.

- **chunk 처리:** chunk마다 cache를 먼저 확인하고 checkpoint를 남긴다. 결측이 있는 chunk는 다시 조회한다.
- **재실행:** 이미 성공한 범위는 아무 작업도 하지 않는다. 중단되거나 실패한 job은 checkpoint부터 이어서 실행한다.
- **재시도와 간격:** 일시적 upstream 오류만 지수 backoff로 재시도한다(`--max-attempts`). upstream 호출 간격은 `--min-request-interval-seconds`로 둔다.
- **데이터 경로:** `--data-dir`가 없으면 `SIW_DATA_DIR`를 쓴다. API를 같은 디렉터리로 띄우면, 여러 chunk에 걸친 요청 범위도 upstream 호출 없이 cache에서 조합해 응답한다.
- **종료 코드:** `0` 성공·이미 성공·중단, `1` 실패, `2` 잘못된 인자·설정, `3` 다른 프로세스가 같은 job을 실행 중.

### 전체 검증

```bash
./scripts/verify.sh
```

이 명령은 Python 포맷·린트·타입·테스트, JSON Schema/semantic contract tests, TypeScript 포맷·린트·타입·테스트·빌드와 생성 타입 drift를 검사한다.

## 변하지 않는 기본 방향

- v1.5 확정 요구사항인 Alpaca/Massive **동등 미선정** 정책 유지.
- 증권사·유료 API는 권한/라이선스 별도 검증, 자동 결제·주문 기능은 없음.
- 백테스팅과 Agent는 후속 단계이며 Phase 0에서는 계약 정의까지만.
- 모든 거래 기록의 종류·출처·시간/세션/데이터 리비전 구분.
