# Architecture Decision Records

결정에 중대한 변경 비용이 있을 때에만 `0001-의사결정-제목.md` 형식의 ADR을 작성한다.

```text
Status: proposed | accepted | superseded
Context: 어떤 제약/문제가 있었나
Decision: 무엇을 선택했나
Alternatives: 어떤 대안이 있었나
Trade-offs: 이점/비용/실패 모드
Evidence/verification: 확인한 코드/테스트/근거
Revisit when: 어느 조건에서 재검토하나
```

불필요한 자잘한 결정을 ADR로 남기지 않는다. 사용자와 합의된 `Alpaca + Massive 미선정` 상태를 구현 편의로 변경하지 않는다. 2026-10-17 사용자 결정에 따른 Alpaca 프로토타입(opt-in)은 정식 선정이 아니다(ADR 0012).

- `0001-phase-0-toolchain-and-contract-source.md`: Phase 0 toolchain과 canonical contract source
- `0002-local-market-data-storage.md`: SQLite metadata, immutable Parquet와 복구 journal
- `0003-us-equity-calendar-and-aggregation.md`: US equity calendar, 세션 정책과 1분봉 집계
- `0004-multi-process-store-and-point-in-time-contract.md`: 다중 프로세스 cache 소유권 복구와 point-in-time 응답 규칙
- `0005-calendar-fingerprint-query-boundary-and-job-lifecycle.md`: calendar 일정 fingerprint·고정 범위, cache 전 query 검증, job 종료 상태
- `0006-session-partitions-api-startup-and-single-version.md`: 세션 날짜 partition, 부작용 없는 API import, 단일 제품 버전
- `0007-aggregated-api-cache-composition-and-backfill.md`: 공개 집계 timeframe, cache 범위 조합, 재개 가능한 backfill
- `0008-chart-workspace-slice.md`: Phase 2A 차트 2개 workspace, 합성 SYNTH 데이터, 세션·종목 API
- `0009-manual-and-hypothetical-trade-ledgers.md`: 수동(실제)·가상 거래 원장, 멱등 기록, ET 시간 변환, 분리된 P&L
- `0010-named-workspaces-and-layout-templates.md`: 이름 붙인 workspace, Terminal/Review 템플릿, 되돌리기
- `0011-broker-statement-csv-import.md`: 증권사 거래내역 CSV 가져오기, 중복 검토, 별도 원장, 원본 미보관
- `0012-alpaca-prototype-provider.md`: Alpaca 시세 adapter 프로토타입(opt-in), 무료 요금제 15분 제한, 무체결 분 처리
- `0013-broker-market-data-adapters.md`: 한국투자·토스·키움 시세 adapter(공식 문서 기반, 실계정 확인 대기), 미래에셋 미지원
- `0014-provider-key-registry.md`: 화면에서 시세 키 등록, secret은 OS 키체인, 계좌별 여러 키와 호출 한도 시 다음 키로 전환
- `0015-daily-bar-cache-by-year.md`: 일봉 캐시를 확정된 연도 단위로 저장, 올해는 항상 새로 조회, 리비전 혼합 금지
- `0016-ticker-search-symbol-directory.md`: 미국 상장 전 종목 티커 검색(Alpaca 자산 목록, 하루 단위 로컬 사본), 검색한 종목 차트·관심종목 추가
- `0017-toss-direct-integration.md`: 토스 직접 연동(키 1개 공유 세션, 조회 전용 허용목록, 그룹별 호출 한도), 일봉·실시간 시세, 단계 계획
- `0018-broker-api-execution-ledger.md`: 증권사 API 체결 원장(토스, 주문 1건당 1행·평균가·마지막 체결 시각), 계좌 조회 전용, 계좌번호 끝 4자리
- `0019-toss-realtime-relay-and-tick-store.md`: 토스 실시간 중계(API 단일 연결, Origin 검사, 재연결·PING), 체결 시 원장 자동 동기화, 유실 표시 틱 저장소(봉과 분리)
- `0020-korean-market-foundation.md`: 국내 주식 기반(K1) — XKRX 캘린더·NXT 확장 시간, kr: 종목 ID, KRW, 시장별 라우팅(국내=토스), 하루 전체 일봉(all_day)
- `0021-backtest-engine.md`: 백테스트 엔진(B1) — 규칙·Python 전략(격리 프로세스), 차트와 같은 인과적 지표, 다종목 포트폴리오, 다음 봉 시가 체결·수수료·슬리피지, 입력 고정, 대기열 실행
- `0022-chart-trade-review-agent.md`: 차트·매매 복기 Agent(Phase 5) — 로컬 근거(사후 정보 표시, 시점 이후 차단) + 요청 시 Claude 해설(근거만 전송, 투자 조언 아님), 차트 시점·실제 체결·백테스트 복기, 조회 전용
- `0023-python-custom-indicators.md`: Python 사용자 지표(Phase 4) — update(ctx)를 봉마다 호출해 구조적으로 미래 차단, 전략 샌드박스 재사용, 버전 고정(py:id@버전), 차트 통합
- `0024-filings-and-dark-pool-agents.md`: 공시·다크풀 근거 Agent(Phase 6) — SEC EDGAR(Submissions·CompanyFacts, User-Agent 명시, 초당 5회 이하)와 FINRA 주간 ATS/비ATS 집계(지연·방향 없음 표시), 게시 시각과 최초 관측 시각 분리, 시점 이후 차단, 조회 전용
- `0025-broker-reconciliation.md`: 증권사 API 원장과 수동·거래내역서 체결의 대사(조회 전용 계산 보기, 원장 불변) — 시각 정밀도별 창, bp 가격 허용 오차, 1:1·여러 체결 합산·주문번호 연결, 일치·불일치·한쪽에만 분류와 사유
- `0026-formula-indicators.md`: 수식 지표(Phase 4) — 허용 목록 함수·사칙연산의 안전한 수식, eval 없는 파서, 상수 길이·과거만 참조, 차트별 저장, 입력 중 검증
- `0027-higher-timeframe-indicators.md`: 큰 시간대 지표 — 분봉 차트에 일봉·주봉 지표, 확정·공개된 큰 봉만 각 차트 봉 종료 시점 기준으로 반영(미래 차단), 요청별 캐시
- `0028-electron-desktop-shell.md`: Electron 데스크톱 셸 — siw://app 출처로 화면 제공, 로컬 API는 저장소에서 찾거나 실행(번들 안 함), 서명 없는 x64 .app/.dmg
- `0035-alpaca-default-us-provider.md`: Alpaca(SIP·BOATS)를 미국 시세·종목 검색·기업 행위의 기본 공급업체로 선정(사용자 결정, 코드 기본값은 합성 데이터 유지), Massive는 미검증 대체 후보
- `0036-desktop-app-roles.md`: 데스크톱 앱 역할 분리 — Tauri(SIW Agent)는 승인된 전략 실행·단계별 주문 Gate(첫 단계 KIS 모의투자만), Electron(SIW Research)은 사용자·AI 연구·백테스트(주문 없음)
- `0037-paper-order-execution.md`: (accepted) SIW Agent 단계 1 설계 — 모의투자 주문(키움 먼저, KIS 다음), 증권사 중립 의도 계약·adapter, 별도 모의 원장, 주문 의도 계약, 실계좌 도메인 코드 차단, 한도·킬 스위치, 별도 실행기 프로세스, 화면 빌드 분리
- `0038-price-level-strategies.md`: (proposed) 가격대 진입형 전략 — 미리 걸어 두는 지정가(증권사 체결)와 실시간 감시 돌파(키움 모의는 지정가만), `levels` 규칙, 백테스트 대응, 킬 스위치·실시간 출처 미결
- `0039-indicator-style-and-lower-timeframes.md`: (accepted) 보조지표 설정을 TradingView식 입력·스타일 탭으로(소스, 선별 색·굵기·선 모양·표시, 기준선), 더 작은 시간대 지표(각 차트 봉의 마지막 작은 봉 값, 미래 값 없음)
- `0040-pine-script-indicators.md`: (accepted, 보충 2026-10-25) Pine Script — 앱 안의 인터프리터(eval 없음, 봉 단위 실행, 미래 값 없음, 실행 한도). 입력·plot·plotshape·bgcolor·fill·그리기(line/label/box/table)·배열·문자열·switch·메서드, request.security(확정 시점 정렬, lookahead는 x[1]만), strategy는 차트 백테스트 체결만(주문·원장 없음, 봉 안 순서 가정 표시)
- `0041-premarket-high-on-daily-charts.md`: (accepted, 보충 2026-10-26) 미국 일봉 차트의 프리마켓 — 세션마다 장전 구간 `960m` 봉 하나를 따로 받아(최근 60세션, 일봉을 받은 뒤) 일봉에 섞지 않고 일봉 뒤 반투명 캔들로 표시, 최신 고가 가격선, 미확정은 회색·점선, 리플레이는 장전 봉이 닫힌 뒤에만
- `0043-meritz-open-api.md`: (accepted, 실계좌 확인 전) 메리츠증권 Open API 읽기 전용 연동 — 키별 공유 세션(MAC 주소는 키와 함께 저장, 허용 목록에 주문 경로 없음), 해외주식 체결내역 → 증권사 원장(체결 시각은 확인 전까지 한국 시간으로 보고 세션 밖은 제외), 계좌 잔고·현금·미체결, 일봉·현재가(시세 출처 선택), 실시간 시세 중계(토스와 섞지 않음)
- `0044-paper-protective-orders.md`: (proposed) SIW Agent 보호 주문 — 모의투자에서 손절·익절·트레일링을 확정 봉 감시로 시작, 킬 스위치·한도·세션 밖 처리는 사용자 결정 필요
- `0045-agent-on-linux.md`: (accepted) SIW Agent의 Linux 실행 — Ubuntu 24.04, systemd 사용자 서비스, 재부팅 뒤에는 로그인해야 키링이 열리고 실행기가 뜸(linger 없음), `agent-linux` CI
- `0046-shared-data-space.md`: (proposed) 주식 희석 분석기와 공동 데이터 공간 — 저장소 밖 폴더 하나(`STOCK_SHARED_DATA`), 데이터마다 쓰는 프로그램은 하나, SEC 원문과 분석기의 요청 잠금 공유, SIW는 병합 장부만 읽기, 시세는 SIW만 받고 쓰며 읽는 프로그램은 아직 없음(캐시 결함 결정 뒤), 아직 옮긴 것 없음
- `0047-live-orders-per-order-confirmation.md`: SIW Agent 단계 2 — 실계좌 주문, 모의 실행기 판단을 그대로 쓰고 제출 앞에 주문마다 사용자 승인(60초 만료, 보호 청산 포함), 별도 원장 `live_orders.sqlite3`, 실전 도메인은 `services/live_trading/`에만, 달러 한도·하루 주문 수 필수, 같은 키움 키의 조회 동기화 중지
- `0048-kiwoom-condition-search.md`: (accepted) 키움 미국주식 조건검색 연결 — 연결한 조건식 3개(`SIW_전일신고거래량`·`SIW_당일급등주`·`SIW_전일급등주`)를 이름으로 찾아 1회 검색(일련번호는 조건식을 추가하면 바뀌어 저장하지 않음)(`usa20280`·`usa20281`, WebSocket), 한 번 부를 때마다 로그인하고 닫음, 보낼 수 있는 메시지는 허용 목록으로 제한, 결과는 저장하지 않고 리플레이·백테스트·Agent에 쓰지 않음, 티커는 앱의 종목 조회로만 연결(키움 거래소 코드로 거래소를 추측하지 않음), 실시간 편입·이탈과 국내 조건식은 나중에
- `0049-toss-stock-rankings-panel.md`: (accepted, 실계좌 확인 전) 토스 주식 랭킹 패널 — `GET /v1/rankings`(`stock_rankings_response`), 거래대금·거래량·급상승·급하락·토스증권 기준과 국내 투자자별 순매수·순매도(1.2.28 신규 엔드포인트), 받은 값 그대로, 이미 아는 종목 목록으로만 listing을 붙여 클릭하면 활성 차트에 열기, 저장하지 않음
- `0050-quote-command-for-local-tools.md`: (accepted) 다른 로컬 도구용 읽기 전용 시세 명령 — `scripts/siw-quote quote --ticker`, Alpaca 분할 조정 값과 기준일(`split_basis`), 마지막 1분봉 종가·오늘 세션별 거래량·정규장 1분봉 합 ADV와 일봉 ADV, 캐시를 읽거나 쓰지 않음, 재배포 금지(약관 원문 미확인)
- `0051-kiwoom-realtime-condition-watch.md`: (accepted) SIW_당일급등주 실시간 기록 — 별도 collector 명령 `siw-collector watch-condition`이 미국 세션마다 프리마켓 시작부터 애프터마켓 끝까지(뉴욕 04:00–20:00, 조기 폐장 17:00, 휴장일 제외) 실시간 조건검색(`usa20290`)을 구독해 첫 목록과 편입·이탈을 받은 시각과 함께 `screeners/kiwoom_condition_watch.sqlite3`에 덧붙여 저장, 30분마다 다시 구독하고 새 목록과 직전 상태의 차이(`differs_from_previous`)로 알림 없이 바뀐 종목을 드러냄, 끊기면 다시 연결(5초~5분), 기록되지 않은 끝은 `interrupted`로 남김, `export-condition --date`로 읽기, 리플레이·백테스트·Agent는 읽지 않음, ADR 0048의 "저장하지 않음"을 이 조건식에 한해 바꿈
- `0052-daily-candidate-screening.md`: (proposed, 초안) 당일 후보 종목 스크리닝 — 조건검색 편입 종목을 A 확정 일봉 진입조건(백테스트 엔진), B 뉴스 재료(Alpaca News 후보·권한 미확인, 규칙 분류 먼저, LLM은 모델 배정 뒤, 재료별 조건 프리셋 표), C 희석 스트레스(희석 분석기 서비스 계약 JSON의 계약 가격대, 가격 전망 없음), D 장외 체결 가격대(Alpaca SIP의 TRF 보고 체결, "다크풀"이라 부르지 않음)로 걸러 카드로 저장, 주문 없음, 사용자가 고른 종목만 SIW Agent로
- `0053-off-exchange-price-structure-model.md`: (accepted) 장외 체결 가격 구조 — 다크풀 스트레스 모델 v2(`darkpool-stress/2.0.0`)를 Alpaca SIP 체결(장외 `D` + lit)로 이식, Decimal, 20세션 세션별 캐시, 불변 등록부·사후 판정·walk-forward·shadow audit까지 이식, 승인 전에는 단계 `UNKNOWN`이고 연구 점수는 등록부에만, 화면에서 "스트레스"·"다크풀"을 쓰지 않음, 주문 없음
- `0054-selection-to-agent-scenario.md`: (proposed, 초안, 흐름은 0057로 대체) 종목 선정에서 SIW Agent 시나리오까지 — 1단계 선정(검색식 모멘텀 → 다크풀·공매도·뉴스·긴 시계열 차트, 장중 계속·주로 미국 정오 전 완료), 2단계 선정 종목만 전략(움직임 예상 분류(주식 수·차트 모양·재료, LLM 필수) → 실시간 수급(자료에 따라 정의) → 희석·다크풀 스트레스 모델 → 전략 적용), 3단계 종목·전략 버전·진입·청산 가격대만 Agent로, 근거는 Research에 두고 외부 감사 LLM과 사용자가 검토, 공매도는 유료 API, 주문 없음
- `0055-intraday-momentum-candidates.md`: (accepted) 하루 내 모멘텀 종목 수집(ADR 0054 U0) — 조건식 편입 자체를 모멘텀으로 보고 거르지 않음, 기록의 빈틈을 구간으로(`watch_coverage.gaps`), 구독 시작 목록에서 본 종목은 편입 시각 모름과 가장 이른 시각, 편입 횟수·머문 시간, `as_of`와 정오(뉴욕 12:00) 기준, 검사 `momentum`(처음 본 시각 전에 닫힌 Alpaca SIP 1분봉의 가격·전일 대비·거래대금 추정, 키움 값과 섞지 않음), 기록기·키움 요청은 바꾸지 않음
- `0056-technique-sources-to-strategy.md`: (proposed, 초안) 매매기법 자료(영상·PDF)에서 규칙 빌더 전략까지 — Commonplace(`cpl`)로 등록해 도서관 경유(영상 출처 자료 격리·PDF 글자 추출은 도서관 회신 대기), SIW Research에서 자료를 골라 근거 인용(영상 시각·PDF 쪽)이 붙은 기법 카드 → 모호한 점은 사용자 답 → ADR 0042 전략 연구로 Pine → 규칙 빌더 변환, 인용 대조는 스크립트, 모델은 배정 대기, 주문 없음
- `0057-selection-flow-seven-stages.md`: (accepted, 흐름) 종목 선정에서 SIW Agent까지 7단계 — 1 모멘텀 수집(거르지 않음) → 2 재료 영향력(NIR, 없으면 규칙, 판정 불가는 "재료 미확인"으로 통과, 저영향 재료면 그날 제외, 목록은 별도 논의) → 3 실시간 수급(스크립트, 1분봉 5봉 중 4봉 거래대금 $5,000·거래량 1,000주, 20봉 중 1%·2호가 이상 3봉, halt 빈 분 제외(halt는 `siw-collector watch-halts`가 SIP statuses를 기록), 지금 시점만 제외·장 마감까지 재진입) → 4 희석·장외 체결·공매도 정보(LLM 없음) → 5 차트 모니터링·가격대(규칙 별도 논의, 장중 수정) → 6 전략 틀에 진입·청산 가격대 → 7 SIW Agent 키움 모의투자 자동 실행, 사용자는 흐름만 확정, 주 1회 희석 LLM 검수, 움직임 분류·피보나치·진입조건 A 폐기
