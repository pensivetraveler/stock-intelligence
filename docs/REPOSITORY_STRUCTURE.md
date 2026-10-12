# Repository Structure — 초기 제안 v1.5

이 디렉터리는 **Phase 0 구현이 완료된 실행 가능한 기반 구조**다. API·Collector·웹 개발 셸·계약 검증과 CI가 구성되어 있다. `packages/indicator-sdk`만 후속 Phase 4를 위한 빈 자리표시자로 유지한다.

```text
stock-intelligence-workstation/
├── AGENTS.md                        # 저장소 루트 Codex 운영 규칙
├── README.md                        # 시작 방법·현재 구현 상태
├── .gitignore
├── .env.example                     # 테스트용 비밀정보 없는 환경 변수 템플릿
├── pyproject.toml / uv.lock          # Python 의존성·품질 도구 잠금
├── package.json / pnpm-lock.yaml     # JS workspace·품질 도구 잠금
├── .github/workflows/ci.yml          # 로컬 verify 경로를 실행하는 CI
├── apps/
│   └── desktop/                     # Tauri 2 + React/TypeScript (Phase 0: 개발 셸)
├── services/
│   ├── api/                         # FastAPI, 계약 검증, health/version
│   ├── collector/                   # 별도 프로세스 시작점
│   └── market_data/                 # Provider protocol과 합성 fixture adapter
├── packages/
│   ├── contracts/                   # JSON Schema 2020-12, 버전, TS/Python 변환 검사
│   └── indicator-sdk/               # 미래 Python 지표 SDK 자리, 실행 엔진은 Phase 4
├── tests/
│   ├── fixtures/                    # 합성·비실거래 데이터만 허용
│   └── contracts/                   # JSON Schema validation / 교차 언어 계약 테스트
├── scripts/                         # 전체 검증 명령 진입점
└── docs/
    ├── SYSTEM_SPEC.md               # 통합 시스템 명세 v1.5
    ├── IMPLEMENTATION_ROADMAP.md   # 단계별 구현 로드맵 v1.5
    ├── DATA_PROVIDER_EVALUATION_v1.5.md
    ├── PROJECT_INSTRUCTIONS.md
    ├── PHASE_0_IMPLEMENTATION_SPEC.md
    ├── CODEX_PHASE_0_TASK.md
    ├── PROJECT_STATE.md            # 결정/진행/검증/문제의 동적 상태
    └── decisions/                   # ADR 기록
```

## 경계

| 계층                     | 담당                                       | 하지 않는 일                            |
| ------------------------ | ------------------------------------------ | --------------------------------------- |
| `apps/desktop`           | 데스크톱 화면/사용자 조작                  | DB 직접 접근, Provider별 API 키 저장    |
| `services/api`           | 공통 HTTP 계약·검증·도메인 서비스 진입점   | 신뢰하지 않은 Python 플러그인 직접 실행 |
| `services/collector`     | 수집 Job·외부 Provider Adapter (후속 단계) | 프런트 UI, 증권사 주문 실행             |
| `packages/contracts`     | 단일 버전의 언어 중립 데이터 계약          | 비즈니스 로직·네트워크 요청             |
| `packages/indicator-sdk` | 사용자 지표 계약 (후속 단계)               | Phase 0에서 외부 Python 실행 허용       |
| `tests`                  | 고정 Fixture·계약/통합 검증                | 고객 계좌·실거래 원본 자료 포함         |
| `docs`                   | 불변 요구사항/상태/결정/실험 증거 분리     | 불필요한 동일 규칙 복사                 |

## 기술 선택·의존성

- FastAPI/Python과 React/TypeScript는 현재 macOS 환경에서 실행·테스트했다. Tauri CLI와 구성은 존재하지만 Rust/Cargo가 없어 네이티브 dev/build는 `blocked: prerequisite`다.
- Python은 uv 단일 lock, JavaScript는 pnpm workspace 단일 lock을 사용한다. 선택 이유와 재검토 조건은 ADR 0001에 기록했다.
- SQLite·Parquet·DuckDB는 후속 단계의 제안 기술이며 Phase 0에는 데이터베이스·migration·ingestion을 추가하지 않았다.
- `.env.example`에는 비밀키를 쓰지 않는다. 외부 연동은 Phase 0에서 꺼둔다.
- 원격 Collector 단계에서는 별도 인증 API를 통해 통신하며 SQLite 파일의 직접 네트워크 공유는 설계하지 않는다.
