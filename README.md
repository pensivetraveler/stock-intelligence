# Stock Intelligence Workstation — Codex Starter v1.5

미국 주식 차트·매매 복기·백테스팅·전문 Agent 연구 환경을 위한 **로컬 개발 착수 패키지**.

**현재 상태:** Phase 0 기반 구현 완료. FastAPI API, React/TypeScript 개발 셸, 독립 Collector 진단 경로, 교차 언어 계약/fixture, 품질 게이트와 CI가 구성되어 있다. 현재 머신에는 Rust/Cargo가 없어 Tauri 2 네이티브 실행만 미검증이다.

## 시작 순서

1. Git 원격 저장소가 있으면 `git clone`으로 받는다. ZIP 배포물이라면 빈 디렉터리에 **전체 압축 해제**한 뒤 저장소 루트(`AGENTS.md`가 있는 위치)에서 `git init`을 실행한다. 원격·초기 커밋·CI 연결은 사용자가 사용할 호스팅을 정한 뒤 명시적으로 구성한다.
2. `git status`가 정상 동작하고 기존 파일이 모두 보이는지 확인한다. 기존 원격이나 사용자 변경이 있으면 덮어쓰지 않는다.
3. Codex를 **그 로컬 폴더에서** 시작한다. 필요한 문서와 현재 환경을 먼저 읽도록 한다.
4. `docs/CODEX_PHASE_0_TASK.md`의 실행 요청을 Codex에 보낸다.
5. Codex가 `docs/PHASE_0_IMPLEMENTATION_SPEC.md`의 P0-01~P0-07을 구현하고 테스트한 뒤 변경·검증 결과를 기록하게 한다.

## 참조 문서

- `AGENTS.md`: Codex가 따라야 할 루트 지침 (원본 협업 지침의 핵심 행동 규칙 포함)
- `docs/PROJECT_INSTRUCTIONS.md`: 지속적인 개발 협업·안전 지침
- `docs/SYSTEM_SPEC.md`: 전체 시스템 요구사항
- `docs/IMPLEMENTATION_ROADMAP.md`: 전 단계 개발 순서
- `docs/EXECUTION_PROCESS.md`: 현재 작업 단계와 사용자 승인 Gate
- `docs/DATA_PROVIDER_EVALUATION_v1.5.md`: 공식 문서 조사 (계정/라이선스 미검증)
- `docs/REPOSITORY_STRUCTURE.md`: 디렉터리 구조와 경계
- `docs/PHASE_0_IMPLEMENTATION_SPEC.md`: 지금 구현할 작업과 검증 기준
- `docs/CODEX_PHASE_0_TASK.md`: Codex 최초 실행 메시지
- `docs/PROJECT_STATE.md`: 실행·결정·차단사항 갱신 위치

## 실행 명령

### 전제조건

- Python 3.10.x, uv 0.12.x
- Node.js 22.x, pnpm 11.x
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

포트 `8000`이 사용 중이면 API와 데스크톱을 같은 대체 포트로 맞춘다.

```bash
uv run uvicorn services.api.main:app --host 127.0.0.1 --port 8123
VITE_API_BASE_URL=http://127.0.0.1:8123 pnpm dev
```

### Desktop 개발 셸

```bash
pnpm dev
```

기본 주소는 `http://127.0.0.1:5173`이다. Tauri 2 전제조건을 설치한 환경에서는 `pnpm desktop:tauri`를 사용한다.

### Collector

```bash
uv run python -m services.collector doctor
uv run python -m services.collector run
```

`doctor`는 외부 API를 호출하지 않으며 성공 시 JSON과 exit code `0`을 반환한다. `run`은 SIGINT/SIGTERM으로 정상 종료한다.

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
