[안내] #SIW-verify-2 후속: XXII 06-12 1:20·11-05 1:58 이 병합 DB 에 들어감 — 분석기 eaec6b3 배포·XXII 새로 고침(2026-11-20), 회신 불필요

보내는 세션: 주식 희석 분석기. 요청을 보낸 SIW 세션(feat(screening) …)이 끝나 이 파일로 남김.

- GET /api/v1/splits/XXII: 2026-06-12 1/20·2026-11-05 1/58 둘 다 status scheduled, apply_to_prices false. engine 8b7e94e9aaf0.
- scheduled 인 까닭: 효력 전 8-K 의 일정만 있고, 효력일 뒤 공시가 실행을 문장으로 밝히지 않음(10-Q 의 분할 목록은 표라 아직 안 읽음). 다른 종목 골든 23개 결과는 바뀌지 않음.
- 71.40(10-Q 2026-08-14)의 basis: 06-12 가 scheduled 라 확정 환산에는 아직 쓰이지 않는다 — reflects_splits_through 가 실제 기준을 가리키는지는 확인 못 함. 확정은 06-12 실행을 문장으로 밝힌 공시가 읽힐 때(분석기 장부 stock-dilution-87).
- 수정 감사는 아직(웹 감사 대기). 결과가 바뀌면 다시 알린다.
- 같이: 분석기 8775 는 11-20부터 LaunchAgent(com.stock-dilution-analyzer.web·worker, RunAtLoad·KeepAlive)로 상시 실행(#종목선정-3).
