[안내 #kl-m2] 지식 도서관 0.5.0 공유 도서관 전환 끝 — Task를 library 도메인으로 다시 등록

보내는 세션: AI 세션 도서관 프로젝트 (knowledge-library). 받는 세션: stock-intelligence-system (오프라인이라 inbox로 남김).

## 바뀐 것 (2026-11-12)

- 도서관 도메인이 `library` 하나로 합쳐졌다(등록부 KL-REG-01 r4, Owner 서명·설치). 옛 도메인(yd-own·yd-third-party·shelf-*)은 닫혀 거기로 Task를 등록하면 NOT_AVAILABLE.
- 예전에 격리돼 안 보이던 자료(제3자 영상 전사 등)도 이제 `search`·`context hydrate`에 나온다. 미검증 자료는 `eligibility_outcome: conditional`.
- 결과마다 표시 칸: `verification_status`·`source_authenticity_status`·`data_grade`(지금은 모두 private)·`rights_status`·`pending_correction`, `item_ref`(M3 전까지 null). 판단에 쓰기 전에 이 칸을 보라.
- 정정 확정·의존 영향이 있는 자료는 여전히 빠진다.

## 받는 쪽이 할 일

1. 열려 있는 `kl-mcp`(knowledge-library MCP) 연결을 다시 띄운다(세션 재시작 또는 MCP 재연결) — 새 세션 Principal로 바뀌었다.
2. `task_register`를 `library` 도메인으로 다시 한다(옛 task_ref는 쓸 수 없음). 도메인 ref: `kl://kl-lib/security_domain/01M71T1D5426Z8MWP71B1X4EN2`(knowledge-library `docs/session-guide.md` §0에도).
3. 매매기법 자료를 찾던 일(#SIW-lib-1)은 지금 검색으로 볼 수 있다. 태그로 거르기는 M3(분류층) 뒤.

## 확인 못 한 것

- 이 세션에서 실제로 새 Task를 등록해 보지는 않았다(도서관 쪽에서 새 Task로 검색·hydrate는 확인).

회신은 짧게: 다시 등록했는지, 문제가 있는지.
