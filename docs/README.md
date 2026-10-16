# 문서 안내

**정리 기준일: 2026-10-15.** Phase 0과 Phase 1A~1C의 로컬 구현까지 진행했다. 실제 Provider 연동과 차트 UI는 미착수다.

## 현재 작업을 이어갈 때

1. [PROJECT_STATE.md](PROJECT_STATE.md): 현재 구현 상태, 검증 근거, 제약과 다음 작업.
2. [IMPLEMENTATION_HISTORY.md](IMPLEMENTATION_HISTORY.md): 지금까지의 작업 내역, 커밋, 주요 파일, 단계별 검증 기록.
3. [EXECUTION_PROCESS.md](EXECUTION_PROCESS.md): 실제 진행 순서와 승인 범위.
4. [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md): 전체 제품의 장기 개발 순서와 아직 남은 요구사항.

## 요구사항·실행·결정 문서

| 문서                                                                 | 용도                                                                                           |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| [루트 README](../README.md)                                          | 설치, API·웹 셸·Collector 실행, 전체 검증 명령                                                 |
| [AGENTS.md](../AGENTS.md)                                            | 저장소 작업 규칙과 최초 Phase 0 범위. 후속 단계는 사용자가 승인한 범위와 현재 상태를 함께 확인 |
| [PROJECT_INSTRUCTIONS.md](PROJECT_INSTRUCTIONS.md)                   | 지속적인 개발 협업·안전 규칙                                                                   |
| [SYSTEM_SPEC.md](SYSTEM_SPEC.md)                                     | 전체 제품 요구사항과 데이터 불변조건. 모든 항목이 구현되었다는 뜻은 아님                       |
| [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md)                   | 구현 디렉터리와 계층별 책임                                                                    |
| [PHASE_0_IMPLEMENTATION_SPEC.md](PHASE_0_IMPLEMENTATION_SPEC.md)     | 완료한 Phase 0의 원래 범위와 수락 기준                                                         |
| [CODEX_PHASE_0_TASK.md](CODEX_PHASE_0_TASK.md)                       | 최초 Phase 0 착수용 요청문. 현재 체크아웃에서 재구현을 지시하는 문서가 아님                    |
| [DATA_PROVIDER_EVALUATION_v1.5.md](DATA_PROVIDER_EVALUATION_v1.5.md) | 2026-10-12 기준 공급업체 공개 문서 조사. 실제 계정 권한·저장권·연동 성공 증거가 아님           |
| [decisions/README.md](decisions/README.md)                           | 채택한 구조 결정과 대안·재검토 조건                                                            |

상태 변경은 `PROJECT_STATE.md`, 완료한 작업 이력은 `IMPLEMENTATION_HISTORY.md`, 다음 실행 순서와 승인 범위는 `EXECUTION_PROCESS.md`에 기록한다. 과거 검증일을 현재 날짜로 덮어쓰지 않고 재실행 여부를 명시한다.
