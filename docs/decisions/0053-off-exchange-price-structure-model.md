# ADR 0053 — 장외 체결 가격 구조 모델(다크풀 스트레스 모델 v2 이식)

- **Status:** accepted. 2026-11-09 사용자 결정(아래 "사용자 결정"). 희석 쪽 스트레스 모델은 사용자가 설계안을 따로 줄 예정이라 이 ADR에 아직 없다.
- **Date:** 2026-11-09
- **Related:** [ADR 0052](0052-daily-candidate-screening.md)(당일 후보 스크리닝, C 희석 공급 가격대·D 장외 체결 가격대), [ADR 0035](0035-alpaca-default-us-provider.md)(Alpaca 기본 공급업체), [ADR 0024](0024-filings-and-dark-pool-agents.md)(다크풀 Agent)

## Context

2026-11-09 사용자 결정: 장외 체결(D) 수집과 희석 수집(C)과는 별개로, 두 데이터를 토대로 한 스트레스 모델을 개발한다. 다크풀 쪽 기본 설계안으로 사용자가 `~/Trading/darkpool-analysis/STRESS_MODEL_V2_IMPLEMENTATION.md`(모델 `darkpool-stress/2.0.0`, 문서 상태 `CODE_COMPLETE_AWAITING_EMPIRICAL_APPROVAL`)를 줬다. 그 저장소(git 저장소 아님)는 읽기만 했다.

원본이 하는 일:

- 입력: Unusual Whales `stock-volume-price-levels`(날짜별·가격별 off·lit 거래량)와 일봉. MacBook LAN 서버, PostgreSQL, NAS RAW 보관. 서버는 2026-11-09에 Tailscale offline이었다.
- 계산: adaptive fine/coarse bin → 시간 감쇠 가격 profile → 분위수 구간(zone) → 해상도 안정성 → 방향별 빈 구간(gap) → 단기 5·장기 20세션 구간 안정성·이동 → 종가 재진입·교차 → off/lit 이상치(robust z) → 방향별 점수(로지스틱) → 단계(LOW·MODERATE·HIGH·EXTREME) → hard/soft gate.
- 승인 절차: 불변 parameter set 등록부, 불변 forecast 등록부, 원래 구간 그대로의 사후 판정(`NO_TOUCH`·`REJECTION`·`ACCEPTANCE`·`TRANSIT`·`AMBIGUOUS`), label 공개 시각을 지키는 walk-forward 평가, 최소 20세션 shadow audit. 승인된 parameter set이 없으면 점수는 null, 단계는 `UNKNOWN`, forecast는 `NOT_ISSUED`다.

이 저장소의 규칙과 맞춰 볼 점:

1. 출처: SIW의 미국 시세는 Alpaca(ADR 0035)이고, D는 Alpaca SIP 체결(거래소 코드 `D`)을 쓴다. 출처가 다른 자료는 조용히 섞지 않는다.
2. 이름: 분석기 세션은 "스트레스"라는 낱말이 판단처럼 읽힌다고 했다(그래서 C 화면 이름이 "희석 공급 가격대"다). D는 "다크풀"이라고 쓰지 않는다. 장외 체결에는 증권사 내부 체결이 섞이고, 방향을 모르며, 보고 시각과 체결 시각이 다르다.
3. 가격 전망: ADR 0052 C와 분석기 계약 규칙 6은 목표가·흡수 비율을 만들지 않는다. 원본의 단계와 반등·돌파 순위는 가격 행동에 대한 추론이다.
4. 승인 절차가 맞물리지 않는다: 평가와 shadow audit은 점수가 있는 forecast를 써야 하는데, 점수는 승인된 parameter set이 있어야 나오고, 승인은 평가 결과가 있어야 받는다. 연구용 parameter로는 점수가 늘 null이라 평가를 시작할 수 없다.
5. 정밀도: 원본은 float로 계산한다. 이 저장소는 금액·수량을 Decimal로 다룬다.
6. 시간: 원본은 날짜 단위 자료다. SIW는 cutoff 시각까지 알 수 있던 값만 쓴다.

## 사용자 결정 (2026-11-09)

- **들이는 방식:** "SIW로 이식(Alpaca)". 계산 로직을 SIW로 옮기고, 입력은 Alpaca SIP 체결의 가격별 장외(`D`)·lit(나머지 코드) 거래량으로 바꾼다. UW 결제나 LAN 서버는 쓰지 않는다.
- **점수·단계:** "승인 절차까지 이식". parameter·평가·shadow 등록부와 사후 판정기까지 옮긴다. 실제 데이터로 승인하면 단계가 나온다.
- **이름:** "장외 체결 가격 구조". 화면과 카드에서는 "스트레스"와 "다크풀"을 쓰지 않는다. 내부 모델 버전은 원본 계열(`darkpool-stress-siw/2.0.0`, `derived_from: darkpool-stress/2.0.0`)로 둔다.
- **세션 수:** "20세션 + 세션별 캐시". 원본의 창(최소 10개 완전 세션·80%, 단기 5·장기 20)을 그대로 쓴다.
- **승인 순환:** "연구 점수는 기록만". 승인만 빼고 모든 gate를 통과하면 연구용 계수로 점수를 계산해 등록부의 따로 된 표(`research_scores`)에만 남긴다. 카드와 화면에는 보이지 않고 단계는 `UNKNOWN`이다. 평가와 사후 판정은 이 기록으로 돌고, 승인된 parameter set부터 `ISSUED` 단계가 나온다.

## Decision

### 1. 입력 (`services/screening/structure/sessions.py`)

- 닫힌 세션 하나(04:00~20:00 ET)의 Alpaca SIP 체결을 처음부터 끝까지 읽어 **정확한 체결 가격별** 장외·lit 거래량으로 둔다. 체결 거르기(취소·정정 제외, 공식 시가·종가 보고 제외, 시장가가 아닌 조건은 거래량에만 넣고 가격대에서 뺌)는 D와 같은 규칙(`off-exchange-conditions-1`)이다. 두 검사가 같은 체결을 센다.
- **세션별 캐시:** `SIW_DATA_DIR/screening_structure.sqlite3`의 `session_levels`. 끝까지 읽은 닫힌 세션만 저장하고, 문서마다 SHA-256을 두어 읽을 때 다시 맞춘다. 상한(세션당 400쪽·300초)이나 오류로 끊긴 세션은 버리고 나중에 다시 읽는다.
- **D와 공유(2026-11-10):** 장외 체결 D 검사(ADR 0052 §5 "D 확장")가 같은 캐시와 같은 백그라운드 읽기를 쓴다. 문서 버전은 `structure-session-levels-2`(D용 `off_exchange_trades`, `off_exchange_off_market_price_volume` 추가, 모델은 읽지 않음). 읽기는 동시 4개이고, 화면 요청이 장 마감 뒤 미리 받기보다 앞선다. `closed_sessions`는 `sessions.py`로 옮겼다.
- **기준 가격:** 지금 세션의 장전~cutoff 사이 마지막 시장가 체결(어느 거래소든). 없으면 마지막 닫힌 세션의 마지막 체결. 시각과 세션(pre/regular/post)을 함께 남긴다.
- **분할:** 체결 가격은 조정되지 않은 값이다. 첫 세션 다음 날부터 cutoff 날짜 사이에 분할·역분할(Alpaca corporate actions)이 있으면 가격 기준이 섞이므로 `H04_CORPORATE_ACTION_IN_WINDOW`로 막는다. 조회가 안 되면 `H04_CORPORATE_ACTION_UNRESOLVED`. 조용히 조정하지 않는다.
- **일봉:** ATR20과 종가 경로 특징에만 쓴다. Alpaca 일봉(분할 미조정, 체결과 같은 기준), `is_final`인 봉만, 다른 출처 봉은 넣지 않는다. 일봉이 정규장만의 값인지는 미확인이다.

### 2. 모델 (`services/screening/structure/model.py`)

원본 `build_stress_forecast.py`의 계산과 gate를 Decimal로 옮겼다. 원본과 다른 점:

| 원본                   | SIW                                                                                 | 이유                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| UW 날짜별 가격대       | Alpaca SIP 정확한 가격별 장외·lit                                                   | 사용자 결정, 출처 하나                                                    |
| float                  | Decimal(정밀도 34), 문서 안 숫자는 모두 문자열                                      | 금액·수량 규칙                                                            |
| 최소 호가 $0.01 고정   | $1 미만은 $0.0001                                                                   | sub-penny 호가가 허용되는 구간. $0.01이면 $0.40 종목의 구간이 2.5%가 된다 |
| snapshot·evidence 해시 | 세션 문서 해시(`H09_INPUT_HASH_MISMATCH`), 읽지 못한 입력(`H00_INPUT_INCOMPLETE`)   | SIW에는 snapshot 등록부가 없다                                            |
| 날짜 단위 decision     | cutoff 시각(`min(cutoff, 지금 - 공개 지연)`), 세션 끝이나 기준 체결이 그 뒤면 `H03` | 미래 데이터 누출 금지                                                     |
| 지난 세션              | cutoff 뒤에 읽은 세션은 `S09_RECONSTRUCTED_AFTER_CUTOFF`(soft)                      | 취소·정정 표시가 그때와 다를 수 있다(ADR 0024 "나중에 받아 재구성")       |
| —                      | `S04_SOURCE_SCOPE_LIMITED` 늘 붙음                                                  | 체결 출처가 Alpaca SIP 하나                                               |
| 해시 RFC 8785          | 정렬한 compact JSON의 SHA-256                                                       | 숫자가 모두 문자열이라 같은 결과. 새 의존성 없음                          |

- 지금 열려 있는 세션은 profile에 넣지 않는다(닫힌 세션만 캐시하고 재현한다). 기준 가격만 지금 세션에서 읽는다.
- 모르는 값은 null이고 0으로 바꾸지 않는다. 세션이 부족하면 단계는 `UNKNOWN`이지 `LOW`가 아니다.

### 3. 등록부와 승인 절차 (`registry.py`, `evaluation.py`, `service.py`)

- `screening_structure.sqlite3`에 `parameter_sets`·`forecasts`·`research_scores`·`validations`·`evaluations`·`shadow_audits`가 있다. 모든 행은 SQLite trigger로 수정·삭제를 막고, 문서 해시를 읽을 때마다 다시 맞춘다.
- **parameter set:** 원본의 등록 규칙 그대로. 승인 상태(`SHADOW_APPROVED`·`PRODUCTION_APPROVED`)는 학습·검증 창, 대상 종목군, 선택 지표, 승인 시각, 단계 기준점, 등록된 eligible 평가 보고서 해시가 있어야 등록된다. production은 eligible shadow audit 해시도 있어야 한다. JSON의 `approval_status`만 바꾼 문서로는 forecast를 등록할 수 없다(등록된 문서의 해시와 맞춰 본다).
- 연구 기준 parameter set(`darkpool-stress-siw-research-baseline/2.0.0`, 원본 연구 계수 그대로)은 처음 열 때 등록한다.
- **forecast:** 검사를 돌릴 때마다 forecast를 등록한다(`NOT_ISSUED` 포함). 연구 점수는 `research_scores`에 forecast 해시와 묶어 따로 둔다.
- **사후 판정:** forecast의 원래 구간을 다시 그리지 않고 decision 다음 `horizon_sessions`(5)개 세션의 일봉으로 판정한다. label 공개 시각은 decision 뒤여야 한다. 일봉은 장중 순서를 모르므로 결과가 둘 이상이면 `AMBIGUOUS`다. 원본 기준표(`validation-policy.json`) 그대로.
- **walk-forward 평가:** 원본 그대로 train에서 기준점 후보를 내고, label이 split 끝 뒤에 나온 기록은 그 split에서 뺀다. 기록에 `score_basis`(`issued`/`research`)를 남긴다. 기준(최소 test 수, UNKNOWN 비율, Spearman, 상위 10% lift, 단계별 사건 비율의 단조성)은 예시 계획(`evaluation-plan.example.json`)에 있고, 실제 split·embargo·기준은 사용자가 정한다.
- **shadow audit:** 서로 다른 거래 세션 20개 이상, UNKNOWN 비율, 해시 유효성. 원본 그대로.
- 평가기와 감사기는 보고서만 만들고 `automatic_approval: false`다. 승인은 사람이 `register-parameters`로 등록한다. 코딩 Agent는 승인 문서를 만들거나 등록하지 않는다.
- 명령: `uv run python -m services.screening.structure.service {prefill|run|validate-due|export-records|evaluate|shadow-audit|register-parameters}`.

### 4. 스크리닝 카드 (`check.py`, `services/api/screening_structure_routes.py`)

- `POST /v1/screening/checks/off_exchange_structure {symbol, session_date?}`. 결과는 ADR 0052 S1의 `screening_check`(`check: "off_exchange_structure"`)로 `screening.sqlite3`에 덧붙인다. 계약 `ScreeningOffExchangeStructureResult`.
- 20세션을 처음 읽으면 60초를 넘을 수 있다. 그래서 요청이 뒤에서 읽기를 시작하고 30초까지 기다린다(화면 60초 안). 다 못 읽었으면 `pending`과 진행(`progress`: 받은 세션 수·읽는 중·빠진 세션과 사유)을 저장하고, 같은 POST를 다시 부르면 이어 받는다. 대기 중에는 forecast를 등록하지 않는다.
- status: `ok`(gate가 승인·점수 특징만 막음), `incomplete`(세션 부족·분할·기준 가격 등), `no_data`(가격대 거래량 없음), `pending`.
- 카드에 보이는 것: 구간(기준 가격에 가까운 8개, 위/아래/걸침), 빈 구간, 구간 안정성·경로·이상치, 범위, 분할 확인, gate 사유(코드와 한국어 설명), 한계와 미확인.
- 단계·점수·반등/돌파 순위는 `ISSUED`일 때만 보인다. 계약이 `NOT_ISSUED`인데 단계·점수가 있는 문서를 거절한다. shadow 단계 parameter의 단계에는 "shadow 단계(운영 승인 전)" 경고를 붙인다.
- 카드에는 연구 점수가 들어가지 않는다.
- 패널(S1 세션 회신 `[회신 #SIW-stress-1]`, 2026-11-09): D 열과 따로 "가격 구조" 열을 두고 S1 세션이 붙인다. 칸에는 `stage`를 보이되 `UNKNOWN`은 "판정 없음", `deployment_stage`가 `RESEARCH_ONLY`면 "연구용"을 붙인다. 펼치면 `forecast_status`·`zones`·`gaps`·`eligibility.hard_failures`. 화면 문구는 "예측"이라 하지 않고 승인 단계와 한계를 드러낸다. 이어 받기는 같은 POST를 다시 부른다(`/checks/{id}/refresh`는 쓰지 않는다).

### 5. 가격 전망과 판단에 대해

- 구간과 빈 구간은 과거에 거래가 몰렸거나 비었던 가격대다. 목표가나 가격 경로가 아니라고 카드에 늘 적는다.
- 단계는 원본의 "경로 불안정" 점수 구간이고, 반등·돌파 순위는 규칙으로 붙인 순위다. 사용자가 승인 절차까지 들이기로 했으므로 승인된 parameter set에서는 보인다. 승인 전에는 보이지 않는다.
- 스크리닝은 주문을 내지 않는다.

### 6. LLM

- 이 모델에는 LLM이 없다. 모든 값은 계산이다. 그래서 모델 배정 상담은 하지 않았다.

## Alternatives

- **원본 LAN 서버 호출:** UW 키와 서버 가동이 필요하고, 서버는 offline이다. 출처도 Alpaca와 다르다.
- **둘 다(출처 분리):** 작업량이 가장 크다. 서버가 다시 켜지면 다시 볼 수 있다.
- **연구 점수도 카드에 표시:** 검증 안 된 HIGH/EXTREME이 판단처럼 읽힌다. 사용자가 고르지 않았다.
- **점수 없이 구간만:** 사용자가 승인 절차까지 원했다.
- **분할을 자동 조정:** 비율·적용 시각을 잘못 잡으면 가격대가 조용히 틀어진다. 지금은 막고, 필요해지면 따로 정한다.

## Consequences

- 종목 하나를 처음 볼 때 20세션 체결을 받는다. 급등 소형주는 세션당 7~28초(S2 측정), 대형주는 36~85초라 첫 결과는 몇 분 늦다. 그 뒤로는 새로 닫힌 세션만 받는다. 대형주는 세션당 상한(400쪽·300초)에 걸릴 수 있다.
- `screening_structure.sqlite3`는 체결 가격별 거래량을 계속 쌓는다(세션당 수천 행까지). 지우는 기능은 없다.
- 실제 데이터로 평가·shadow를 마치기 전에는 단계가 나오지 않는다. 이것은 원본 명세의 production gate다.
- 원본의 숫자와 같지 않다(입력 출처, 호가 단위, 정확한 가격). 원본에서 승인한 parameter set을 그대로 쓰지 않는다.

## 미확인

- 실제 Alpaca 키로 20세션을 받아 보지 않았다. 시험은 가짜 체결로만 돌렸다.
- Alpaca 일봉이 정규장만의 고가·저가·종가인지.
- 실제 평가 데이터셋(과거 cutoff로 다시 만든 forecast와 판정), 승인 기준·split·embargo는 사용자가 정할 일이다.
- 희석 스트레스 모델: 설계안을 받은 뒤 이 ADR에 더하거나 새 ADR로 쓴다.

## Revisit when

- 희석 스트레스 설계안을 받았을 때.
- 실제 평가 결과가 나왔을 때(기준점, subgroup 실패).
- 원본 LAN 서버가 다시 켜지고 UW 자료를 함께 볼지 정할 때.
- 분할이 잦은 종목에서 `H04`로 막히는 일이 많을 때.
