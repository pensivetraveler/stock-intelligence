# Codex에게 전달할 첫 작업 — Phase 0

아래 요청을 **Codex 로컬 작업공간의 채팅에 그대로 입력**해도 된다. 먼저 이 패키지 전체를 프로젝트 루트에 풀어둔다.

---

이 저장소의 `AGENTS.md`와 `docs/PROJECT_INSTRUCTIONS.md`, `docs/SYSTEM_SPEC.md`, `docs/IMPLEMENTATION_ROADMAP.md`, `docs/PHASE_0_IMPLEMENTATION_SPEC.md`를 실제로 읽고 Phase 0 구현을 시작해 줘.

이번 작업은 P0-01 ~ P0-07만 대상으로 한다. 현재 코드는 제공되지 않은 상태에서 스타터 디렉터리만 존재할 수 있으니, 초기 환경/디렉터리/충돌 여부부터 확인해 줘. 기존에 소스가 있다면 덮어쓰지 말고 재사용 가능한 부분을 평가해 줘.

- API: Python FastAPI의 `GET /health`, `GET /version` 동작 및 테스트
- Desktop: React/TypeScript 기반 최소 실행 셸, API 상태 표시; Tauri 2 연결은 실제 OS 환경에서 가능 여부 검증
- Collector: 별도 프로세스 시작점과 진단 기능만
- Contracts: 버전 관리 가능한 단일 canonical schema와 Python/TypeScript 공통 valid/invalid 합성 fixture 검증, 별도 semantic validator, 데이터 출처·UTC/Decimal·실제/수동/가상/백테스트 원장 분리 테스트
- Quality: 포맷/린트/타입체크/테스트/CI 및 실행 가능한 README

시장 데이터·증권사 API를 호출하거나 계좌/주문 연결, 유료 구독, 크롤링, 백테스팅 실행기, 차트 전체 기능을 Phase 0에 끼워 넣지 마. Alpaca와 Massive는 동등한 미선정 후보로 유지해 줘.

작업을 P0 단위로 나누어 구현하고, 완료 시 실제 실행 명령과 로그/테스트 결과, 변경 파일, 남은 위험, 차단된 작업을 보고해 줘. 문서에 명시된 수락 조건을 만족하지 못했으면 완료라고 쓰지 마. 환경 제약이 있을 때는 달성 가능한 검증을 먼저 수행하고 미실행 부분을 구별해 줘.

---

**참고:** Codex는 로컬 파일을 볼 수 있어야 한다. 로컬 저장소 루트에 `AGENTS.md`가 있어야 해당 규칙을 찾기 쉽다. 이 파일은 대화용 최초 실행 요청으로, 프로젝트 지침과 중복하여 영구 보관하지 않아도 된다.
