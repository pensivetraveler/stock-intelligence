# ADR 0052 — 당일 후보 종목 스크리닝(초안)

- **Status:** proposed. 2026-11-09 사용자 결정으로 S1부터 착수한다(아래 "진행 방식"). 나머지 "사용자가 정할 것"은 각 단계 착수 전에 정한다.
- **Date:** 2026-11-09
- **Related:** [ADR 0048](0048-kiwoom-condition-search.md)·[ADR 0051](0051-kiwoom-realtime-condition-watch.md)(조건검색), [ADR 0024](0024-filings-and-dark-pool-agents.md)(공시·다크풀 Agent), [ADR 0046](0046-shared-data-space.md)(희석 분석기와 공동 데이터 공간), [ADR 0050](0050-quote-command-for-local-tools.md)(시세 명령), [ADR 0036](0036-desktop-app-roles.md)(앱 역할), [ADR 0042](0042-claude-assistants.md)(Claude 보조)

## Context

**2026-11-09 사용자 설명:** 조건검색(SIW_당일급등주)을 돌리는 이유는 당일 전략을 실행할 종목을 고르기 위해서다. 검색기로 받은 종목을 다음 네 가지로 거른다.

1. 일봉이나 더 긴 시계열에서 진입조건을 확인한다.
2. 뉴스로 재료를 확인한다. 재료에 따라 진입조건을 조정한다.
3. 주식 희석 리스크로 주가 스트레스 구간을 확인한다.
4. 다크풀 분석으로 지지·저항 구간을 확인한다.

지금 있는 것과 빠진 것(2026-11-09 저장소 기준):

| 분석                | 있는 것                                                                                                                                                               | 빠진 것                                                                                    |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 후보 목록           | 조건검색 1회 검색(ADR 0048), 실시간 편입·이탈 기록(ADR 0051), 토스 랭킹 패널(ADR 0049)                                                                                | 기록을 읽어 후보로 넘기는 단계. ADR 0051 기록은 지금 리플레이·백테스트·Agent가 읽지 않는다 |
| 1. 진입조건         | Alpaca 일봉(분할 조정), 백테스트 규칙 엔진(`services/backtest/strategy.py`), 저장한 전략                                                                              | 저장한 전략의 진입조건을 후보 종목의 확정 일봉에 한 번 평가하는 단계                       |
| 2. 뉴스 재료        | 없음. ADR 0024는 뉴스를 "따로 검토하고 나중 ADR"로 미뤘다                                                                                                             | 뉴스 출처, 재료 분류, 재료별 진입조건 조정 규칙                                            |
| 3. 희석 스트레스    | 공시 Agent(공시 양식·건수). 희석 분석은 "주식 희석 분석기"가 맡는다: 로컬 서비스 `127.0.0.1:8775`, JSON 계약 v1(행사·전환가, floor, ATM, 선반 등록, 재매도 등록 물량) | 분석기 결과를 받아 가격대로 보여 주는 단계. 분석기와의 합의                                |
| 4. 다크풀 지지·저항 | Dark Pool Agent: FINRA 주간 ATS 자료                                                                                                                                  | 가격 정보. FINRA 주간 자료는 2~4주 늦고 거래량만 있어 가격 구간을 낼 수 없다               |

지켜야 할 규칙(AGENTS.md, SYSTEM_SPEC):

- 미래 데이터 누출 금지. 스크리닝 시각까지 알 수 있던 자료만 쓰고, 게시 시각과 SIW가 받은 시각을 구분한다.
- 출처·권리 명시. 출처가 다른 자료를 조용히 섞지 않는다.
- Agent 추론과 근거를 구분하고, 데이터·버전·시점·불확실성을 기록한다.
- 주문은 SIW Agent에서만, 기존 승인 흐름으로만 나간다. 스크리닝은 주문을 내지 않는다.

## Decision (제안)

### 1. 흐름

```
후보 목록 ──▶ A 진입조건 ──▶ B 뉴스 재료 ──▶ C 희석 스트레스 ──▶ D 장외 체결 구간 ──▶ 후보 카드
(조건검색 편입,                (재료에 따라 A의                                              (사용자가 골라
 직접 추가)                     조건 프리셋을 고름)                                            SIW Agent 승인으로)
```

- **후보:** 조건검색에 편입된 종목(ADR 0051 기록)과 사용자가 직접 넣은 종목. ADR 0051의 "Agent는 읽지 않는다"를 이 스크리닝에 한해 바꾼다. 리플레이·백테스트는 여전히 읽지 않는다.
- **언제:** 종목이 처음 편입될 때 한 번, 그리고 사용자가 요청할 때. 같은 종목·같은 뉴욕 날짜는 앞 결과를 다시 쓰고, 다시 돌리면 새 판으로 쌓는다.
- **결과:** 종목마다 카드 한 장. `SIW_DATA_DIR/screening.sqlite3`에 저장한다(시장 데이터·원장과 다른 파일). 카드에는 검사마다 결과, 근거, 기준 시각(cutoff), 출처, 버전, 실패·미확인이 들어간다.
- **넘기기:** 카드는 주문도 실행 승인도 만들지 않는다. 사용자가 카드를 보고 고른 종목만 SIW Agent의 기존 실행 승인 화면에 종목으로 들어간다.

### 2. A — 일봉 진입조건

- 저장한 규칙 전략의 진입조건을 후보 종목의 **확정 일봉**(전 거래일 종가까지)에 평가한다. 백테스트와 같은 엔진을 쓰고 판단 코드를 새로 만들지 않는다.
- 결과는 규칙마다 충족 여부와 그때의 값(예: 20일 신고가 대비 거리, 거래량 배수)이다.
- 프리마켓 값(ADR 0041의 장전 봉)을 쓰는 조건은 스크리닝 시각까지 닫힌 봉만 쓰고 "미확정"으로 표시한다.
- 출처는 Alpaca SIP 일봉(분할 조정, 기준일 표시). 증권사 시세와 섞지 않는다.

### 3. B — 뉴스 재료

- **구조(2026-11-09 사용자 결정):** "뉴스 수집, 개별 뉴스 분석, 개별 종목의 뉴스 사이클 분석은 각각 별도의 모듈"이고, 셋을 SIW 밖 "별도 프로젝트·서비스"에 둔다. 서비스는 news-analysis(`~/Trading/news-analysis`, GitHub `pensivetraveler/news-analysis`, `127.0.0.1:8776`, 계약 `news-contract-v1`)다. 희석 분석기(C)와 같은 방식이고, SIW는 요청하고 결과를 받기만 한다. 수집 키와 기사 저장소도 서비스가 가진다.
- **SIW 쪽(검사 B, `services/screening/news_service.py`, `services/api/screening_news_routes.py`):**
  - `POST /v1/screening/checks/news` `{symbol, session_date?, lookback_hours?(기본 72, 1~720)}`이면 서비스에 `POST /api/v1/news-checks` `{ticker, cutoff_utc, lookback_hours, refresh: "auto"}`를 보낸다. cutoff는 A와 같이 `min(지금, 세션 끝)`이다. 그 뒤 작업을 45초까지 기다려 `news_check` 결과를 받는다(2026-11-10, 15초에서 늘림: 서비스의 LLM 단계가 기사당 약 6초, 최대 5건 더 걸림).
  - 결과는 `screening_check`(`check: "news"`)로 저장한다. `status`는 서비스 값(`material`·`no_material`·`no_data`·`error`)을 그대로 쓰고, 끝나지 않았거나 서비스가 꺼져 있으면 `pending`이다. `result`에는 카드용 요약(categories, out_of_scope, feeds, article_count, latest_categorized, nir)과 서비스 결과 원문(`news_check`)을 둔다.
  - `versions`에는 검사 판과 계약, 서비스 판, 규칙 판·해시, 출처별 파서, (있으면) NIR 프롬프트 판·모델을 담는다. `sources`에는 `{service, contract, result_id}`를 담는다.
  - 기다리던 검사는 `POST /v1/screening/checks/news/{check_id}/refresh`로 같은 cutoff에서 다시 확인한다.
  - 다른 계약 판(`news-contract-v1`이 아닌 것)은 받지 않는다. SIW는 서비스를 띄우지 않는다. 서비스 주소는 `SIW_NEWS_URL`이고 이 컴퓨터 주소만 허용한다.
- **범주:** 서비스 규칙(`news-rules-2`, 2026-11-10부터 `news-rules-3`)을 따른다. 카드는 서비스 범주 여덟 개 모두에 이름을 붙인다. 구속력 있는 계약(`binding_contract`)과 MOU·LOI(`non_binding_intent`)를 나누고, 유상증자·공모 같은 희석 기사는 범주로 두지 않고 `out_of_scope.dilution`으로 표시만 한다. 희석은 C가 판단한다.
- **개별 분석(NIR)·사이클 분석(CIR):** 서비스의 모듈이다. 모델 배정과 사용자 확정은 서비스 쪽에서 다룬다(news-analysis ADR 0002: openai/gpt-6-luna, 대체 claude-haiku-5.5, 건당 $0.01·월 $15 상한). CIR 분석은 보류(사용자 결정 2026-11-10).
- **카드의 NIR 요약(검사 판 `screening-news-3`, 사용자 결정 2026-11-10):** 서비스 결과의 `nir` 블록에서 만든 기사(`created`·`reused`)를 최대 5건, 사건 종류·구속력·확정/잠재 금액·경제적 실체·홍보성 위험·확신·한국어 요약으로 보인다. 늘 "LLM 추론, 확인된 사실 아님"을 함께 적고, 기사에서 못 찾은 값이나 규칙과 어긋난 점이 있으면 표시한다. LLM 판단이 없으면(키 없음·상한 도달·서비스가 LLM 단계 전 판·옛 결과) 범주가 있을 때 "규칙 결과만"과 이유를 보인다. LLM은 `status`를 바꾸지 않고, 뉴스가 없거나 LLM이 없어도 후보는 빠지지 않는다.
- **이전 구현(PR #147, 3b99763):** SIW 안에서 세 출처를 직접 모으고 `news-rules-1`로 분류했다. 그 코드(`services/public_sources/*news*`, `services/screening/news.py`·`news_rules.py`)와 Keys 화면의 `marketnewsapi`·`finnhub` 항목은 서비스로 옮기며 지웠다. 두 항목에 등록된 키는 없었다. 그때 확인한 권한·약관은 아래에 기록으로 남긴다.

#### 기록: SIW 안 구현 때의 확인(2026-11-09, 지금은 서비스가 맡음)

- **출처(2026-11-09 사용자 결정: "MarketNewsAPI, Finnhub 모두 사용"):** 세 뉴스 공급원을 각각 별도 출처로 읽는다. 서로 섞거나 하나로 합치지 않는다.
  - **Alpaca News** (`alpaca-news`): `GET /v1beta1/news`. 기사는 여러 공급원에서 온다(`source`: Benzinga, fiscal.ai 등).
  - **MarketNewsAPI** (`marketnewsapi-news`): `GET https://api.marketnewsapi.com/v1/news`. 발행사 보도자료(GlobeNewswire, Business Wire, PR Newswire, ACCESS Newswire, Newsfile)와 일반 기사를 준다. 원본 보도자료를 모으는 용도다. 키는 `X-API-Key` 헤더로 보낸다.
  - **Finnhub** (`finnhub-news`): `GET https://finnhub.io/api/v1/company-news`. 북미 기업 뉴스다. 키는 `X-Finnhub-Token` 헤더로 보낸다(URL에 넣지 않는다).
  - 세 키 모두 Keys 화면에 등록한다(`alpaca`, `marketnewsapi`, `finnhub`). 키가 없는 출처는 건너뛰고 결과에 그렇다고 적는다.
- **확인 결과(2026-11-09, S4 세션):**
  - **Alpaca 권한:** Keys 화면의 첫 Alpaca 키로 읽기 전용 2회 호출(AAPL, 3건), HTTP 200. 한도 헤더 `X-RateLimit-Limit: 10000`(분당). 응답 필드는 `id, headline, summary, author, created_at, updated_at, url, symbols, source, content, images`. `include_content`를 주지 않아도 `content`는 빈 문자열이었다.
  - **Alpaca 약관:** Alpaca Terms and Conditions(`files.alpaca.markets/disclosures/library/TermsAndConditions.pdf`, 2026-11-09 받음)는 뉴스를 포함한 "Content"를 개인·비상업 용도로만 쓰게 하고, 다른 매체로 게시·배포하려고 복사·재게시하는 것을 금한다. 제3자(Benzinga 등) 콘텐츠도 이 Content에 들어간다. 그래서 제목·요약을 **이 컴퓨터의 로컬 캐시에만** 두고 화면에 보이는 것은 개인 연구로 보고 진행한다. 내보내기·공유·커밋은 하지 않는다. 시험 자료는 지어낸 기사만 쓴다.
  - **Alpaca 미확인(unverified):** 뉴스가 어느 요금제에 들어가는지(공식 문서에 표 없음, 한도 10000/분은 유료 요금제 값과 같다), 뉴스 전용 과거 기간·한도, `created_at`이 게시 시각인지(문서는 "만든 시각"이라고만 함), 기사 수정 때 `updated_at`이 바뀌는지, Benzinga 쪽 별도 사용 조건. 약관 해석은 법률 검토가 아니다.
  - **MarketNewsAPI(공식 OpenAPI·약관·요금 페이지, 키 없음):**
    - 요청: `tickers`(최대 50), `type=news,press_release`, `include_legal=false`, `from`·`to`(ISO 8601 UTC, 초 단위), `limit` 1~100, `cursor`로 쪽 넘김.
    - 응답: `id, title, url, source{name,domain}, published_at, type, tickers, listings, snippet, summary, body`. `summary`는 공급자가 기계로 만든 요약이고, `body`는 보도자료에만 있다.
    - 요금: 무료는 하루 50회, 15분 지연, 1년치다. Pro는 월 $15.
    - 약관(`marketnewsapi.com/legal/terms`): 개인·내부 용도, "합리적인 기간"의 캐시, 내부 연구·백테스트를 허용한다. 보도자료 전문은 출처 표시와 원문 링크를 붙이면 보여 줄 수 있다. 피드 재판매·재배포, 서비스를 대신하려는 체계적 보관은 금지한다.
    - 2026-10-06에 v1.0을 낸 신생 서비스다.
  - **Finnhub(공식 문서·약관):**
    - 요청: `symbol`, `from`·`to`. 날짜만 받는다(`YYYY-MM-DD`).
    - 응답: `id, datetime(UNIX 초), headline, summary, source, url, related, category, image`.
    - 무료 등급: 분당 60회, 1년치. 문서가 이 엔드포인트의 무료 사용을 명시한다.
    - 약관(`finnhub.io/terms-of-service`): 개인 용도만 허용한다. 데이터와 그 데이터로 만든 결과의 공유·재배포를 금하고, **구독이 끝나면 모든 데이터를 지워야 한다**.
  - **두 곳 모두 키를 받기 전이라 실제 호출은 하지 않았다(미확인).** 키를 등록한 뒤 읽기 전용으로 한두 번 호출해 응답 모양, 소형주 적중, 시간대를 확인한다.
- **저장:** 출처마다 `public_sources.sqlite3`(ADR 0024 캐시)에 종목별 기록(`<TICKER>:<id>`)으로 넣는다. 기사가 바뀌면 새 판으로 옆에 쌓고 덮지 않는다. 공통 필드: 제목, 요약, 작성자, 종목, `created_at`(공급자가 준 게시 시각, UTC), `updated_at`(없으면 null), `first_observed_utc`(SIW가 처음 받은 시각), 원문 링크, 발행처, 종류(`news`/`press_release`).
  - **Alpaca:** `include_content=false`로 요청하고 본문·이미지는 버린다. 한 번에 50건씩 최대 10쪽.
  - **MarketNewsAPI:** 보도자료 `body`는 저장한다(약관상 출처 표시와 함께 보여 줄 수 있음). 요약 칸에는 발행처 `snippet`을 넣고, 공급자가 만든 요약은 `generated_summary`로 따로 둔다. 그 종목이 비미국 거래소(TSXV 등)에만 상장된 기사는 뺀다. 무료 플랜 지연(15분)은 경고로 적는다. 한 번에 100건씩 최대 3쪽(무료는 하루 50회).
  - **Finnhub:** 날짜로만 조회되므로 창 앞뒤로 하루씩 넓혀 받고 cutoff는 받은 뒤 적용한다. 쪽 넘김이 문서에 없어 많은 날은 잘릴 수 있다(미확인). 썸네일은 버린다.
  - **삭제:** `PublicSourceCache.delete_source(source_id)`로 출처 하나의 기록을 모두 지운다(Finnhub 구독 종료 시). `screening.sqlite3`에 남은 결과 문서 안의 제목은 이 함수가 지우지 않는다(미해결).
  - 받는 창은 cutoff 72시간 전(최대 30일)부터 지금까지다.
- **분류 1단계(규칙, `news-rules-1`):** 제목과 요약에서 정해진 낱말·구절(대소문자 무시, 단어 경계)로 재료 종류를 붙인다. 종류: `offering`(유상증자·공모), `earnings`(실적), `clinical_fda`(임상·FDA), `contract_order`(계약·수주), `merger_acquisition`(인수합병), `reverse_split`(역분할), `listing_notice`(상장 유지 통지), `none`(규칙상 재료 없음). 한 기사에 여러 종류가 붙을 수 있다. 낱말 목록은 `services/screening/news_rules.py`에 있고, 결과에 판 이름과 SHA-256을 남긴다. 낱말 목록 첫 판은 사용자 검토 전이다.
- **검사 결과:** `POST /v1/screening/checks/news`(body `{symbol, session_date?, lookback_hours?}`, 기본 72시간)가 S1의 `screening_check` 하나(`check: "news"`)를 `screening.sqlite3`에 덧붙인다. 세션 창과 `cutoff = min(지금, 세션 끝)`, `after_the_fact`는 A와 같다. `status`는 `material`(재료 기사 있음), `no_material`(기사는 있으나 규칙에 안 걸림), `no_data`(창 안에 기사 없음), `unsupported`(미국 종목 아님), `error`(읽을 수 있는 출처가 하나도 없을 때. `result.reason`: `news_credentials_missing` 또는 `source_unavailable`). `result`에 `window_start_utc`, `lookback_hours`, `feeds`(출처마다 `ok`/`no_key`/`error`, 기사 수, 잘림), `categories`, `articles`, `facts`, `missing`, `uncertainty`, `reason`을 둔다. 기사마다 출처(`source_id`)·발행처·기사 종류·본문 보관 여부, 재료 종류·걸린 낱말·게시·수정·처음 받은 시각·판 수, 그리고 `observed_by_cutoff`(cutoff 전에 SIW가 실제로 받았는지)와 `revised_after_cutoff`(보이는 글이 cutoff 뒤 수정본일 수 있음)를 둔다. 같은 제목이 다른 출처에도 있으면 `same_headline_as`로 가리키되 합치지 않는다. `sources`는 `{records: [source_record…]}`, `versions`는 규칙 판·해시·출처별 파서 판(`parser.<source_id>`), 캐시 사용 같은 알림은 `warnings`. `created_at > cutoff`인 기사는 결과에 넣지 않는다. 판은 ADR 0024 규칙대로 cutoff까지 SIW가 본 판을 쓰고, 본 판이 없으면 최신 판을 쓰되 표시한다. "오늘의 후보" 패널의 뉴스 열(`checks.news`)은 S1이 붙인다.
- **분류 2단계(LLM, 선택):** 규칙으로 정하지 못한 기사만 LLM에 묻는다. 정하기 전에 "Local AI Gateway 개발·유지보수" 세션에 모델 배정을 상담하고(`[요청 #SIW-<번호>] 모델 배정: 뉴스 재료 분류`), 회신을 이 ADR에 적어 사용자 확정을 받는다. LLM의 답은 "추론"으로 표시하고 근거 기사와 분리한다.
- **진입조건 조정:** 재료 종류 → A의 조건 프리셋을 고르는 **표**를 사용자가 정한다(예: 공모 재료는 후보에서 뺌, 실적 재료는 거래량 배수 문턱을 높임). LLM이 조건 값을 직접 바꾸지 않는다. 카드에 어떤 표의 어떤 줄로 어떤 프리셋이 골라졌는지 남긴다.

### 4. C — 희석 스트레스 구간

- 희석 분석기 서비스에 분석을 요청한다(`POST /api/v1/analyses` `{ticker, as_of, price}` → 작업 상태 확인 → 결과 JSON). SIW는 계약 JSON을 그대로 받아 저장하고 해석만 한다. 분석기 코드와 저장소는 고치지 않는다.
- **보여 줄 가격대:** 계약 JSON에 있는 계약 가격만 쓴다. 워런트 행사가, 전환가, floor, 가격 구간, ATM·선반 여력(달러), 재매도 등록 물량, 주총 희석 안건. 현재가 위·아래에 어떤 공급이 걸려 있는지를 가격 축에 표시한다.
- **가격 전망 없음:** 분석기 계약(규칙 6)처럼 SIW도 목표가나 흡수 비율을 만들지 않는다. "스트레스 구간"은 "이 가격대에서 새 주식이 나올 수 있다"는 뜻으로만 쓴다.
- **상태 존중:** `confirmed`·`computed`·`inferred`만 사실로 보이고, 나머지(`unverified`, 불완전 판, 차단)는 그렇다고 표시한다. FPI 경고 등 계약이 함께 보이라는 고지는 그대로 보인다.
- **시간과 비용:** 분석 하나는 몇 분 걸릴 수 있다(분석기 시간 예산 20분). 그래서 편입 즉시 요청하고, 카드는 먼저 다른 검사 결과로 보이다가 결과가 오면 채운다. 분석기의 LLM 비용 상한(종목·날짜당 $0.15, 월 $10)은 하루 후보가 많으면 월 상한에 걸릴 수 있다. 하루 요청 수 상한을 둔다(사용자가 정함).
- **분석기 세션과의 합의가 먼저다:** 요청 빈도, 비용 상한, 규칙만 모드(`llm=false`) 사용 여부, 실패 시 동작을 "주식 희석 분석기" 세션에 요청해 회신을 받은 뒤 구현한다.
- **분석기 세션 회신(2026-11-09, `[회신 #SIW-screen-1]`, 분석기 main 945e5eb 기준):** 사용 방식은 계약 v1·서비스 API 의도에 맞다. 지킬 것과 권고:
  - 봉투 `state`가 `blocked`·`unsupported`·`failed`면 결과가 없다(`blocked`면 결과 요청이 422). `incomplete_reasons`가 있으면 합계·하한은 `null`·`unverified`이고 0으로 보이지 않는다. 기준 시점 뒤 8-K 증권은 "(기준 시점 뒤 발행)"·`inferred`, `shareholder_approval`(unknown) 게이트는 "승인 전 전환 불가일 수 있음"으로 보인다.
  - 요청한 `price`는 결과(`user_price`)에 남는다. Alpaca 시세로 만든 결과는 로컬 화면에서만 쓴다.
  - 비용: 종목·ET 날짜당 $0.15, 월 $10(LLM 새 분석 최대 약 $0.106 → 월 90건쯤에서 막힘). **권고: 후보 전부는 규칙만(`llm=false`), LLM은 사용자가 고른 하루 2~3종목에만.** 규칙만은 늘 불완전(`llm_disabled`)이라 합계 희석률은 없고, 개별 증권 값은 약 49%만 나오지만 행사·전환가 가격축 용도로는 쓸 만하다.
  - 대기: 작업자 하나, 들어온 순서, 우선순위·취소 없음. 규칙만+SEC 캐시 4~7초, LLM 새 분석 1~9분, 캐시 재실행 10~60초, 작업 예산 20분. 장 전 30종목 규칙만은 몇 분, LLM이면 1시간 넘을 수 있다.
  - 이름: "스트레스"는 판단처럼 읽힌다 → **"희석 공급 가격대"** 또는 "행사·전환가 분포"를 권한다. 가격축 필드: `securities[].price`, `price_range`, `pricing`, `conditions[].threshold`, `clause_terms`, `capacity`, `shelves`, `resale`, `scenarios[].undetermined_security_ids`. `proposals`에는 가격이 없다.
  - 서비스가 꺼져 있으면 "대기"로 두고 띄우지 않는다(`127.0.0.1:8775`의 `GET /` 제목이 "주식 희석 분석기"가 아니거나 연결 거부면 꺼짐, 작업자만 꺼지면 `queued`에 머문다).
- **사용자 확정(2026-11-09, "그렇게 확정"):** 후보 전부는 규칙만(`llm=false`)으로 요청하고, LLM 분석은 사용자가 고른 종목(하루 2~3개)에만 요청한다. 화면 이름은 "희석 공급 가격대"다.

### 5. D — 장외 체결 가격대(“다크풀” 대신)

- FINRA 주간 자료로는 가격 구간을 낼 수 없다(늦고, 가격이 없다). 대신 **Alpaca SIP 체결 중 FINRA TRF/ADF로 보고된 장외 체결**(거래소 코드 `D`로 표시되는 것으로 알려져 있음, **미확인**)을 모아 최근 N세션(기본 5)의 가격대별 거래량을 낸다. 거래량이 몰린 가격대를 지지·저항 후보로 보인다.
- **이름:** 장외 체결에는 다크풀(ATS)뿐 아니라 증권사 내부 체결도 섞여 있다. 화면과 카드에는 "장외 체결(TRF 보고)"이라고 쓰고 "다크풀"이라고 쓰지 않는다. FINRA 주간 ATS 비중은 맥락 정보로 함께 보인다(ADR 0024 규칙 그대로).
- **한계:** 장외 체결의 보고 시각은 실제 체결과 다를 수 있고, 늦게 보고된 체결·정정 체결이 있다. 매수·매도 방향은 모른다. 1분봉으로 체결을 만들어 내지 않는다. 체결 자료는 급등 소형주에서 하루 수백만 건일 수 있어, 받는 쪽수와 시간을 재고 상한을 둔다.
- **출처:** Alpaca SIP 체결 하나뿐이다. 다른 출처 체결과 섞지 않는다.

#### S2 확인 결과 (2026-11-09, 지금 쓰는 Alpaca 키, 읽기 전용)

- **거래소 코드:** `GET /v2/stocks/meta/exchanges`는 `D`를 "FINRA ADF"라고만 부른다. 문서(stocktrades, meta 페이지)에는 코드 표가 없고 메타 엔드포인트를 보라고만 한다. ADF 자체 체결은 아주 적은데 `D` 거래량 비중이 대형주 35.8%, 급등 소형주 61.6~68.5%였다. 그래서 **FINRA TRF 보고분이 `D`에 들어 있다고 판단**한다. 공급자 문서로 확인한 것은 아니라 결과의 `unverified`에 늘 적는다.
- **조건 코드:** `GET /v2/stocks/meta/conditions/trade?tape=A|B|C`로 읽었다. 늦은 보고는 `L`(Sold Last, Late Reporting), `Z`(Sold Out Of Sequence), `U`(Extended Hours Sold Out Of Sequence). 시장 가격이 아닌 체결은 `4`(Derivatively Priced), `W`·`B`(평균가, `B`는 테이프 A/B에서만 평균가이고 C에서는 Bunched), `P`(Prior Reference Price), `7`(QCT), `V`(Contingent), `C`·`N`·`R`(결제 조건), `H`(Price Variation). `M`·`Q`·`9`는 공식 시가·종가 보고라 체결이 아니다.
- **정정·취소:** 과거 체결에는 별도 정정 엔드포인트가 없고 체결의 `u` 필드가 `canceled`·`incorrect`·`corrected`를 표시한다(없으면 유효). 측정에서 AAPL 하루 11건, 소형주 0~1건이 `canceled`였다. `u`는 받은 시점 상태다.
- **자료량**(2026-11-06 하루, 04:00~20:00 ET, 쪽당 10,000건):

  | 종목              | 체결 건수 | 쪽  | 걸린 시간 | 받은 양 | `D` 거래량 비중 |
  | ----------------- | --------- | --- | --------- | ------- | --------------- |
  | AAPL(대형주)      | 714,759   | 72  | 36.1초    | 69.6MB  | 35.8%           |
  | SDA(+145%, $0.38) | 618,236   | 62  | 28.3초    | 59.0MB  | 68.5%           |
  | STAK(+82%, $3.26) | 181,131   | 19  | 9.2초     | 17.2MB  | 66.2%           |
  | GOGO(+72%, $3.89) | 120,063   | 13  | 6.6초     | 11.3MB  | 61.6%           |

  쪽당 평균 0.42~0.48초(최대 1.5초). 거래소로 거르는 인자가 없어 모든 체결을 받아야 한다. 대형주 5세션은 약 360쪽·3분이라 상한이 필요하다.

#### S2 구현 규칙 (`services/screening/off_exchange.py`, `version off-exchange-check-1`)

- **창:** cutoff 전에 장전(04:00 ET)이 열린 최근 N세션(기본 5), 최신부터. 마지막 세션은 `min(cutoff, 계산 시각 − 공개 지연)`에서 자르고 `partial`로 표시한다. 그 뒤 체결은 받지 않는다.
- **세는 것:** `u`가 `canceled`·`incorrect`인 체결은 빼고(`corrected`는 남김), `M`·`Q`·`9`는 체결로 세지 않는다. 전체 거래량은 모든 거래소, 장외 거래량은 `x == "D"`. 가격대에는 위의 "시장 가격이 아닌" 조건을 뺀 `D` 체결만 넣는다(그 양은 `off_market_price_volume`으로 따로 적는다). 늦은 보고(`L`·`Z`·`U`)는 가격이 실제 체결가라 가격대에 넣고, 양을 `late_report_volume`으로 적는다. 홀수주(`I`)는 넣는다.
- **가격대:** 기준가는 cutoff 전 마지막 시장가 체결(거래소 무관). 칸 폭은 기준가의 0.5%에 가까운 1·2·5 단위(최소 $0.0001), 칸이 500개를 넘으면 폭을 두 배로. 양옆 칸 이상인 칸(봉우리) 중 가격대 거래량의 1% 이상인 것을 큰 순서로 5개 고르고 기준가 위·아래·걸침을 적는다. 모두 Decimal 문자열.
- **상한:** 40초, 쪽 80개(80만 건, 실측 쪽당 0.45~0.67초). 화면 타임아웃(60초) 안에 답하기 위해서다(S1 세션 요청). 엔드포인트에서는 FINRA 맥락을 읽은 시간을 40초에서 빼고 체결에 남은 시간을 준다(최소 10초). 쪽 사이에서 확인하므로 마지막 쪽 하나(최대 약 1.5초)만큼 넘을 수 있다. 세션은 최신부터, 세션 안의 체결도 최신부터(`sort=desc`) 읽는다. 다 못 읽은 세션은 `missing`에 적고 `status: incomplete`. 단 가장 최근 세션은 다 못 읽어도 읽은 부분(cutoff 직전 몇 시간)을 `truncated: true`로 남기고 `range_utc`를 실제로 읽은 첫 체결 시각(초 단위)부터로 적는다. 그보다 오래된 세션을 일부만 읽었으면 통째로 뺀다. AAPL 실측(2026-11-06 cutoff 장 마감 후): 처음 상한 150쪽·90초에서 1세션 84.6초, 40초 상한에서는 71쪽·38.6초로 1세션을 다 읽고 `incomplete`. 같은 조건의 다른 실행은 쪽당 0.67초라 60쪽에서 멈췄다(최신부터 읽기 전이라 0세션). 그래서 최신부터 읽어 일부라도 남기도록 바꿨다.
- **결과:** S1의 공통 계약 `screening_check`(`check: "off_exchange"`)로 저장한다(`ScreeningStore.add_check`, 덧붙이기만). `status`는 ok·incomplete·no_data·error(사유는 `result.reason`). `result`는 하위 정의 `ScreeningOffExchangeResult`로 검사한다: `label: "장외 체결(TRF 보고)"`, `data_until_utc`, 세션별 수치, `reference_price`, `bin_width`, `histogram`, `levels`, `context.finra_weekly_ats`(Dark Pool Agent의 FINRA 주간 ATS 사실을 그대로, 가격대 계산에는 쓰지 않음), `limits`, `limitations`(증권사 내부 체결이 섞임·방향 모름·보고 시각 ≠ 체결 시각·FINRA 주간 자료는 맥락), `missing`, `unverified`. 봉투의 `warnings`에도 한계 문구가 늘 앞에 들어간다. 지난 cutoff를 나중에 다시 계산하면 `u` 상태가 나중 것이라는 "나중에 받아 재구성" 문구를 붙인다.
- **엔드포인트:** `POST /v1/screening/checks/off_exchange` `{symbol, session_date?}`(`services/api/screening_off_exchange_routes.py`). 세션 창, `cutoff = min(now, 세션 끝)`, `after_the_fact`는 검사 A와 같다. Alpaca 키가 없으면 503. FINRA 맥락은 공개 자료 저장소가 있으면 최근 12주를 읽고, 없거나 실패하면 경고만 남긴다. 화면 타임아웃(60초)을 넘을 수 있는 대형주는 쪽 상한에 걸려 `incomplete`로 답한다.
- **명령:** `uv run python -m services.screening.off_exchange STAK --cutoff 2026-11-06T15:00:00Z`(등록한 Alpaca 키, 읽기 전용, 결과 JSON 출력).
- **아직 안 한 것:** 후보 패널의 D 열과 차트 겹쳐 그리기(`result.levels`, `result.histogram`)는 S1 쪽에서 붙인다. 같은 종목·같은 날 결과 다시 쓰기(캐시)는 정하지 않았다(지금은 요청마다 새로 계산해 덧붙인다). → 2026-11-10 닫힌 세션 캐시를 붙였다(아래 "D 확장").

#### D 확장: 닫힌 20세션 + 오늘 (2026-11-10, `off-exchange-check-2`)

- **사용자 결정(2026-11-10):** 가격 구조 모델의 세션 캐시와 백그라운드 읽기를 같이 쓴다(동기 대기만 하는 안은 고르지 않음). 기본은 닫힌 20세션 + cutoff까지의 오늘. 세션은 동시 4개로 받는다. 장 마감 뒤 그날 후보 종목의 세션을 미리 받는다.
- **실측(2026-11-10, 등록 키, 읽기 전용, 동시 5개):** STAK 21세션 100쪽 15.4초(세션별 시간 합 46.7초), 캐시 JSON 0.61MB. AAPL 10세션 926쪽 106.0초(합 492.9초), 8.28MB. 동시에도 쪽당 약 0.53초라 4.6배 빨라지고, 벽시간은 가장 큰 세션 하나가 정한다(AAPL 11-04 131쪽 67초). 응답 헤더의 요청 한도는 분당 10,000건이고 동시 5개는 분당 약 560건이었다. AAPL 하루는 61~131쪽이라 예전 상한(40초·80쪽)으로는 하루도 다 못 읽는 날이 있었다. 추정: AAPL 20세션 첫 받기 약 1,850쪽, 동시 4개 약 4분, 캐시 약 17MB. 소형주 20세션 약 0.6MB.
- **닫힌 세션:** ADR 0053의 `session_levels`(`SIW_DATA_DIR/screening_structure.sqlite3`)를 그대로 읽는다. 캐시 문서는 `structure-session-levels-2`로 올려 D가 세션마다 보이는 두 값(`off_exchange_trades`, 장외 중 시장가 아닌 거래량 `off_exchange_off_market_price_volume`)을 더했다. 조건 규칙은 같은 `off-exchange-conditions-1`이고 공통 상수는 `services/screening/trade_conditions.py`로 옮겼다. 두 검사가 같은 큐(`SessionFiller`)를 써서 한 세션은 한 번만 받는다. 화면 요청은 큐 앞에, 미리 받기는 뒤에 넣고, 미리 받기 뒤에서 기다리던 세션을 화면이 요청하면 앞으로 옮긴다. 끝까지 읽은 닫힌 세션만 저장한다(세션당 400쪽·300초).
- **오늘:** cutoff에 열려 있는 세션은 캐시에 넣지 않고 요청 안에서 최신부터(`sort=desc`) `data_until_utc`까지 읽는다(`source: "live"`, `partial: true`). 상한(쪽 80, 25초)에 걸리면 읽은 부분만 `truncated: true`로 남긴다.
- **시간:** 요청 하나의 체결 예산은 40초다(화면 60초 안). 닫힌 세션을 큐에 넣고 → 오늘을 읽고 → 남은 시간만큼 큐를 기다린다. FINRA 맥락을 읽은 시간은 예산에서 뺀다(최소 10초). 다 못 받았으면 `status: "pending"`이고 그때까지 받은 세션으로 계산한 가격대와 `result.progress`(전체·받은·받는 중·빠진 세션과 사유)를 저장한다. 같은 POST를 다시 보내면 이어서 보인다(가격 구조 검사와 같은 방식). 받지 못한 세션은 `missing`에 남고 `incomplete`다.
- **요청:** `POST /v1/screening/checks/off_exchange {symbol, session_date?, sessions?}`, `sessions`는 1~20(기본 20). 5나 10이어도 캐시는 공유라 더 받는 것은 없다. 패널에 "장외 체결 거래일"(5·10·20)을 두었다.
- **결과 계약 변경:** 세션마다 `source`(`live`·`cache`)와 `read_at_utc`, `progress`, `limits.live_max_seconds`. `sessions_requested`는 닫힌 세션 수다. 캐시 세션을 `data_until_utc`보다 1시간 넘게 뒤에 받았으면 "나중에 받아 재구성"을 적는다(ADR 0053 S09와 같은 기준).
- **장 마감 뒤 미리 받기(`services/api/screening_prefetch.py`):** 앱이 켜져 있는 동안 5분마다 본다. 세션이 닫히고 공개 지연이 지나면 그 세션의 후보(조건검색 기록 + 직접 추가한 종목, 패널 목록과 같다)마다 닫힌 20세션을 큐 뒤에 넣는다. 세션당 한 번이다. 앱이 꺼져 있던 날은 다음에 켤 때 마지막 닫힌 세션의 후보를 넣는다(이미 받은 세션은 건너뜀). 모든 종목이 아니라 후보만이다. `SIW_SCREENING_PREFETCH=0`이면 끈다.
- **명령:** `uv run python -m services.screening.off_exchange STAK [--cutoff …] [--sessions 20]`은 이제 빠진 닫힌 세션을 모두 받아(기다림 없음) 캐시에 넣고 결과를 출력한다(검사 결과는 저장하지 않음).
- **하지 않은 것:** 세션 하나를 시간대로 나눠 동시에 받기(대형주 하루 67초를 줄이는 방법, 지금은 세션 단위로만 동시), 캐시 압축(zlib로 약 1/6), 오래된 캐시 지우기.

### 6. 시점과 기록

- 카드마다 `cutoff_utc`(이 시각까지 알 수 있던 자료만)를 둔다. 뉴스는 `published_at ≤ cutoff`, 공시는 접수 시각 ≤ cutoff, 봉은 cutoff 전에 닫힌 것만 쓴다.
- 지난 날짜를 다시 돌리면 그때 SIW가 실제로 받지 않은 자료는 "나중에 받아 재구성"으로 표시한다(ADR 0024와 같다).
- 검사마다 사용한 전략 버전, 규칙·낱말 목록 버전, 모델·프롬프트 버전, 분석기 계약 버전·`result_id`를 남긴다.

### 7. 화면

- SIW Research에 "오늘의 후보" 패널: 편입 시각순 목록, 검사별 표시(충족/불충족/대기/실패), 카드 열기, 카드에서 차트 열기.
- 차트에 C의 계약 가격대와 D의 장외 체결 가격대를 선택해 겹쳐 그린다(기본 꺼짐).
- SIW Agent에는 사용자가 고른 종목만 넘어간다.

## 구현 단위 (각 단계는 사용자 승인 뒤)

1. **S1:** 후보 목록(ADR 0051 기록 읽기 + 직접 추가)과 A 진입조건, 카드 저장소, Research 패널. 외부 호출을 새로 늘리지 않는다.
2. **S2:** D 장외 체결 가격대. Alpaca 체결의 거래소 코드와 자료량을 실제로 재고 나서 확정한다.
3. **S3:** C 희석 스트레스. 분석기 세션의 회신 뒤.
4. **S4:** B 뉴스. Alpaca News 권한·약관 확인과 규칙 분류 먼저, LLM 분류는 모델 배정 뒤.
5. **S5:** 재료별 조건 조정 표와 SIW Agent로 넘기기.

순서는 외부 의존이 적은 것부터다. 사용자가 바꿀 수 있다.

### S1 구현 (2026-11-09)

- **후보:** `services/screening/candidates.py`가 ADR 0051 기록 파일을 **읽기 전용 SQLite 연결**로 연다. `ConditionWatchStore`로 열면 스키마를 만들고 끝나지 않은 구독을 `interrupted`로 닫아, 기록 중인 기록기를 방해한다. cutoff 전에 SIW가 받은 이벤트만 쓴다. 종목마다 처음 받은 시각, cutoff 직전 구독 기준 목록 포함 여부를 내고, 구독 수·중단 수·지금 기록 중인지로 기록의 빈틈을 함께 보인다. 직접 넣은 종목은 `manual`.
- **A:** `services/screening/entry.py`. 저장한 규칙 전략의 `entry`를 cutoff 전에 정규장이 마감한 마지막 세션까지의 확정 일봉(분할 조정, 없으면 원가격과 경고)에 백테스트와 같은 `RuleEvaluator`로 평가한다. 단순 조건마다 비교한 두 값과 충족 여부를 남긴다. 시간대 조건과 Python 지표는 `unsupported`로 답한다.
- **저장:** `SIW_DATA_DIR/screening.sqlite3`(`services/screening/store.py`). 검사 결과는 덧붙이기만 하고 카드는 종류별 최신 결과를 보인다.
- **API:** `GET/POST /v1/screening/candidates`, `POST /v1/screening/checks/entry`, `GET /v1/screening/checks/{id}`. 새 계약 `screening_candidates`, `screening_check`(`check`는 `entry`·`news`·`off_exchange`·`dilution`; B·C·D 세션이 같은 문서 모양으로 붙인다).
- **화면:** SIW Research 오른쪽 "조건검색" 패널 위에 "오늘의 후보". 전략 고르기(이 화면에서만 기억), 모두 검사·종목별 검사, 검사 결과 펼쳐 보기, 종목 추가, 종목을 누르면 차트.
- **아직 없음:** 편입 즉시 자동 검사(지금은 버튼), 프리마켓 장전 봉 조건, B·C·D, SIW Agent로 넘기기(S5).

### C 구현 (2026-11-09)

- `services/screening/dilution.py`: 분석기 서비스(`SIW_DILUTION_URL`, 기본 `http://127.0.0.1:8775`, 이 컴퓨터 주소만 허용)에 `POST /api/v1/analyses {ticker, as_of, llm}` → 작업 상태 → 결과를 읽는다. `price`는 보내지 않는다(Alpaca 시세가 결과에 남지 않게, 현재가는 SIW 차트에 있다).
- `POST /v1/screening/checks/dilution {symbol, session_date?, llm=false}`: 15초까지 기다려 끝나면 `ok`(불완전 사유가 있으면 `incomplete`), 아니면 `pending`(작업 번호 보관). `POST /v1/screening/checks/{id}/refresh`가 대기 중인 작업을 다시 보고 끝났으면 새 결과를 덧붙인다. 서비스가 꺼져 있으면 `pending`과 사유, 띄우지 않는다. `blocked`·`failed`는 `error`, `unsupported`는 `unsupported`이고 결과가 없다.
- 결과에는 분석기 계약 JSON을 그대로(`result.contract`) 두고, 가격축 요약(증권별 행사·전환가, 구간, 하한, 잔량, 조건 문턱, 승인 미확인 게이트, 기준 시점 뒤 발행, ATM 여력, 선반·재매도, 불완전 사유, 발행인 경고)을 붙인다. 값은 분석기 상태와 함께 두고, 모르는 값은 `null`이다.
- 화면: "희석 공급" 열, "희석 모두 검사(규칙만)", 결과 펼치기(모르는 값은 "?", 사실이 아닌 값은 상태 표시, "승인 전 전환 불가일 수 있음"), "LLM으로 분석(비용 듦)"은 종목별로만, 대기 중이면 "다시 확인".
- 한계: 분석기의 as-of는 날짜라 같은 날 cutoff 뒤 공시가 들어갈 수 있다(경고로 남김). 실제 서비스 확인(2026-11-09)에서는 분석기 작업자가 "엔진 파일이 바뀌었으니 다시 시작하라"로 실패했고, SIW는 `error`와 그 사유로 기록했다. 작업자 재시작 뒤 성공 경로는 미확인이다.

### 진행 방식 (2026-11-09 사용자 결정)

- "S1부터 진행." S1은 이 저장소의 담당 세션이 구현한다.
- "뉴스와 다크풀은 별도 세션을 통해 개발을 진행할 것." B(S4)와 D(S2)는 각각 따로 연 세션이 이 ADR을 따라 자기 브랜치·PR로 개발한다. 카드 저장소와 후보 목록(S1)에 결과를 붙이는 연결은 S1이 정한 모양을 따른다.
- "희석분석은 `주식 희석 분석기` 세션을 통해 협업 진행할 것." C(S3)는 그 세션에 요청을 보내 합의한 뒤 구현한다. 분석기 저장소는 그 세션만 고친다.

### 장외 체결 가격 구조 (2026-11-09)

- 장외 체결·희석 데이터를 토대로 한 모델은 [ADR 0053](0053-off-exchange-price-structure-model.md)에 따로 둔다. 다크풀 쪽은 스트레스 모델 v2를 Alpaca SIP 체결로 옮긴 `off_exchange_structure` 검사(화면 이름 "장외 체결 가격 구조")다. 희석 쪽은 설계안을 받은 뒤 정한다.

### 패널 열과 차트 표시 (2026-11-09)

- "오늘의 후보" 표에 진입조건 뒤로 뉴스·희석 공급·장외 체결·가격 구조 열을 둔다. 종목별 "검사"와 "뉴스·장외·구조 모두 검사"(종목마다 차례로)가 있다. 대기 중인 결과를 누르면 그 종류의 경로로 다시 본다: 뉴스는 `/v1/screening/checks/news/{id}/refresh`, 희석은 `/v1/screening/checks/{id}/refresh`, 가격 구조는 같은 요청을 다시 보내 진행 상황을 본다.
- 펼쳐 보기: 뉴스(분류별 건수, 최근 분류 기사, 희석 기사는 C로), 장외 체결(가격대·비중·위치, 일부만 읽은 세션 수), 가격 구조(단계는 ISSUED일 때만, 아니면 "판정 없음"·"연구용", 구간·빈 구간, 단계를 내지 않은 이유).
- 희석에서 증권이 0개이고 규칙만이거나 불완전하면 "없음"이 아니라 "모름"이라고 적는다. 분석기 회신: 규칙만으로는 핵심 증권 필드의 약 17%만 찾는다(D37).
- §7의 차트 표시: 펼쳐 보기에서 "차트에 선 N개 표시"를 누를 때만 그 종목 차트에 점선 가격선을 그린다(기본 꺼짐, 한 번에 검사 하나, "차트에서 지우기"). 희석은 주당 가격(행사·전환가, 구간, 하한, 조건 문턱; 확정 아닌 값은 점선 더 촘촘히), 장외 체결은 가격대의 위·아래, 가격 구조는 구간의 위·아래.
- 후보 목록 계약 `checks`에 `off_exchange_structure`가 빠져 있어, 가격 구조 결과가 저장되면 후보 목록 조회가 계약 검사에서 실패했다. 계약에 더하고 시험을 붙였다.
- 한계: 차트가 분할 조정 가격을 보일 때 희석 계약 가격(발행 당시 기준)과 단위가 다를 수 있다. 분할이 있었던 종목은 선 위치를 그대로 믿지 않는다(미확인).

## 사용자가 정할 것

1. 구현 순서(위 S1~S5 그대로인지).
2. A에 쓸 전략: 어느 저장 전략의 진입조건을 스크리닝 조건으로 쓸지, 스크리닝 전용 조건을 따로 둘지.
3. B 뉴스 출처: Alpaca News로 확인을 시작할지, 다른 출처가 있는지. 기사 본문을 쓰지 않고 제목·요약만 쓰는 것에 동의하는지.
4. B 재료 종류 목록과 재료별 조정 표의 첫 판.
5. C 하루 분석 요청 상한과 규칙만 모드를 쓸지(LLM 비용).
6. D를 "장외 체결(TRF 보고)"로 대신하는 것에 동의하는지, 기본 세션 수 N.
7. 후보 목록에 토스 랭킹 등 다른 출처를 넣을지.

## Alternatives

- **스크리닝을 Agent 실행기 안에 넣는다:** 주문 경로와 섞인다. 앱 역할(ADR 0036)상 연구·후보 고르기는 Research, 실행은 Agent다.
- **LLM 하나에 네 가지를 다 맡긴다:** 근거와 추론이 섞이고 재현이 안 된다. 계산은 코드로, LLM은 뉴스 분류처럼 규칙이 안 되는 곳에만 쓴다.
- **희석 분석을 SIW에서 다시 구현한다:** 분석기와 결과가 갈라진다. 분석기 계약을 그대로 읽는다.
- **FINRA 주간 자료로 다크풀 구간을 낸다:** 가격이 없어 지지·저항을 낼 수 없다.
- **유료 다크풀 피드:** 결제·구독은 사용자 승인 없이 하지 않는다. 필요해지면 따로 검토한다.

## Consequences

- 후보 하나에 외부 요청이 늘어난다(Alpaca 체결·뉴스, 분석기의 SEC 요청). 요청 상한과 캐시가 필요하다.
- 희석 결과는 몇 분 늦게 채워질 수 있다. 장 시작 직전 편입 종목은 C 없이 판단해야 할 수 있다.
- 장외 체결 가격대는 다크풀만의 가격대가 아니다. 그 한계를 화면에 늘 적는다.

## Revisit when

- S2에서 Alpaca 체결 거래소 코드·자료량을 잰 뒤.
- Alpaca News 권한·약관을 확인한 뒤. → 2026-11-09 확인(§3). 요금제·`created_at` 뜻·Benzinga 조건은 미확인으로 남았다. 낱말 목록 첫 판을 사용자가 검토한 뒤, 그리고 S1 카드 연결 모양이 정해진 뒤 다시 본다.
- 분석기 세션이 회신한 뒤.
- 유료 다크풀·뉴스 피드를 검토할 때.
