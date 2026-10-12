# Stock Intelligence Workstation — Data Provider Evaluation v1.5

- **검증 기준일:** 2026-10-12
- **범위:** 미국 주식 1분봉·실시간 시세·실제 체결 내역·공시/뉴스·OTC/ATS 공개 데이터
- **검증 수준:** 공식 공개 문서의 지원 기능과 공개 가격을 조사한 결과. 사용자 계정의 권한, 실제 응답, 약관상 보관/백테스팅 권한 및 최종 청구액은 **아직 검증되지 않았다**.

## 1. Executive recommendation — 2026-10-12 decision update

- **확정된 정책:** 유료 API의 **Alpaca와 Massive를 동등한 미선정 후보**로 유지한다. 지금 공급업체를 결정하거나 어느 한쪽을 과거 데이터/실시간 데이터 전용으로 고정하지 않는다.
- **검증 순서:** (1) 토스·한국투자·키움 + Alpaca·Massive 모두 같은 Adapter/fixture 계약으로 구현 → (2) 사용자가 허용한 읽기 전용 권한과 시험용 유료 접근권으로 실제 1분봉·실시간 제공 범위 비교 → (3) 이력/결측/세션/피드 범위/라이선스/비용을 비교한 뒤 실제 사용 시점에 선택한다.
- **유료 API 후보:** Massive의 요금제별 이력·지연·트레이드/호가 기능과 Alpaca의 Basic/Algo Trader Plus 및 해당 데이터 피드 범위를 각각 평가한다. 가격 및 상세 기능은 기준일 문서상의 값이며, 가입 시 재확인해야 한다.
- **구성 가능성:** 단일 유료 API, 서로 다른 데이터 범주별 API 사용, 명시적으로 검증된 병행 구성을 모두 허용하되 사용자 승인 없이 결제하지 않는다.
- **정밀 Tick/L2 확장:** Databento 미국주식은 venue/dataset 단위로 점진적 도입. 과거 조회 비용, 거래소 사용권, 피드 범위를 조회 견적 없이는 정액으로 가정하지 않는다.
- **공개 자료:** SEC EDGAR API/공시 RSS 우선, FINRA OTC/ATS weekly summary 활용; 기업 IR 사이트는 공식 피드/허용된 HTML 수집으로 보완. FINRA **주간 집계는 실시간 다크풀 체결 신호가 아니다**.
- **실제 체결 원장:** 해당 증권사 본인 계좌 API가 최우선. API가 제공하지 않는 기록은 CSV/Excel로 가져와 대조한다.

## 2. 유료 시장 데이터 서비스: 문서 확인

| 공급업체                 | 공식 확인된 기능                                                    | 공개 가격 (USD/월, 기준일) | 적합한 용도                            | 남은 검증                            |
| ------------------------ | ------------------------------------------------------------------- | -------------------------: | -------------------------------------- | ------------------------------------ |
| Massive Stocks Basic     | 미국주식 aggregate minute 제공, **EOD 이용**, 2년 이력, 5 calls/min |                         $0 | 시험용·과거 EOD 확인                   | 과거 1분봉 접근 시점/빈도 및 이용권  |
| Massive Stocks Starter   | minute aggregates, WebSockets/flat files, **15분 지연**, 5년 이력   |                        $29 | 장기 1분봉 캐시·저비용 백테스트        | 빈 봉, 가격보정, 백테스트 저장권     |
| Massive Stocks Developer | 위 기능 + trades, **15분 지연**, 10년 이력                          |                        $79 | 더 긴 이력, Tick 트레이드 실험         | quote 미포함, 개인 이용권 범위       |
| Massive Stocks Advanced  | 실시간, 20년 이상 이력, trades+quotes                               |                       $199 | 전문 실시간 차트·Tick/quote 연구       | 비전문 개인 자격, 라이선스 범위      |
| Alpaca Basic             | IEX 실시간, 통합 SIP 최신 15분 제한, 미국주식 바 이력(2016년부터)   |                         $0 | 무료 기능 검증 / IEX 한정 시세         | IEX와 전체 미국시장 봉 차이          |
| Alpaca Algo Trader Plus  | 통합 미국 주식 실시간 데이터, bars, since-2016 history              |                        $99 | 역사적 데이터 및 실시간 기능 평가 대상 | 실제 권한·거래소/백테스트 보관 계약  |
| Databento US Equities    | dataset 별 OHLCV-1m, trades, quote/order book 등                    |            **변동형/견적** | Tick/L2, venue별 미시구조              | 데이터셋 범위·거래소 fee·용량별 계산 |

**주의:** Massive 상품 설명의 `unlimited API calls`는 무제한 데이터 권리·무제한 재배포와 동의어가 아니다. 무료 Basic에서도 minute 집계 기능이 표시되지만 실시간 접근은 EOD로 제한된다. Alpaca Basic의 IEX 시세를 전체 거래소 SIP 시세와 동일한 것으로 비교하거나 혼합해서는 안 된다. Databento 단일 거래소 피드를 전국 단위 집계와 동일시해서도 안 된다.

### Official reference links

- Massive stock pricing: https://massive.com/pricing?product=stocks
- Massive aggregate API / empty-trade minutes: https://www.massive.com/docs/rest/stocks/aggregates/custom-bars
- Alpaca subscription/coverage: https://docs.alpaca.markets/us/docs/about-market-data-api
- Alpaca feed distinctions: https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data
- Databento US equities: https://www.databento.tech/catalog/us-equities
- Databento historical/usage: https://databento.com/pricing/

## 3. 증권사 API: 공식 문서 확인

| 증권사                | 미국주식 분봉                                                                   | 주문/체결·계좌                           | 기타 공식 근거                                                                                         | 검증 상태                                             |
| --------------------- | ------------------------------------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| **토스증권**          | **1m/1d 캔들** 지원, 최대 **200봉/요청**, `before` 기반 페이징, adjusted 플래그 | 계좌·주문·주문이력·보유자산 API          | Open API 2026-08-26 정식 출시, 시세/체결 WebSocket                                                     | 문서 지원 확인; 이력 최대치와 응답은 미시험           |
| **한국투자증권(KIS)** | `해외주식분봉조회`, 샘플의 최대 **120레코드/요청**, `NMIN`, `PINC`, `KEYB`      | 해외주식 주문체결내역·잔고·일별거래 내역 | 해외주식 시세/실시간 체결가 WebSocket                                                                  | 문서·공식 샘플 확인; 소급 기간/세션/사용 권한 미시험  |
| **키움증권**          | 미국주식 API `차트` 그룹 지원(분봉 TR 상세는 가입/실제 명세 확인 필요)          | 미국주식 계좌·주문·실시간시세            | 미국주식 조회 일반 **5회/초**(피크 **3회/초**), 차트 그룹 총 20회/초와 더 엄격한 계좌 한도를 함께 적용 | 공식 차트 그룹·율 제한 확인; 상세 TR/이력 실측 미완료 |
| **미래에셋증권**      | **확인되지 않음**                                                               | 미국주식 개인용 오픈 API 범위 미확인     | 검색되는 `digitalx.miraeasset.com`은 가상자산 서비스 문서이므로 미국주식 증권 API 근거가 아님          | 보류                                                  |
| **Webull API**        | 별도 API로 미국주식 OHLCV/tick 제공, 시세 구독 필요                             | Webull 계좌 기반 거래/관리 API           | Webull 플랫폼 자체 API 신청/권한 체계                                                                  | **미래에셋 계좌와 호환된다는 근거 없음**; 별도 옵션   |

**우선순위 의미:** 토스·KIS·키움이 미국주식 분봉 API를 공개했다는 사실은 확인됐지만, 수년치 전 종목 분봉 저장·백테스트용 적법한 사용권까지 검증된 것은 아니다. 계좌 체결과 차트 봉은 *별개의 데이터 계약*으로 관리한다.

### Official reference links

- Toss release: https://p.tossinvest.com/ko/news-room/detail?id=52615
- Toss OpenAPI Market Data (200 candles, pagination): https://developers.tossinvest.com/docs/market-data
- Toss source-of-truth OpenAPI: https://developers.tossinvest.com/llms.txt
- KIS official API catalog: https://apiportal.koreainvestment.com/apiservice-category
- KIS official examples: https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/overseas_stock/overseas_stock_functions.py
- Kiwoom official US market/limits: https://openapi.kiwoom.com/intro?dummyVal=0
- Kiwoom US chart categories: https://openapi1.kiwoom.com/guide/apiguide?dummyVal=0
- Webull API market docs: https://developer.webull.com/apis/docs/market-data-api/getting-started/

## 4. 공식/웹 자료 수집 가능성

| 소스                   | 수집 경로                                                     | 저장/활용안                                                 | 주의                                                                                          |
| ---------------------- | ------------------------------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| SEC EDGAR              | `data.sec.gov` Submissions, XBRL CompanyFacts, EDGAR RSS/원문 | 뉴스 에이전트의 공시·재무 이벤트, 뉴스 사이클 유사기업 분석 | 공개 무인증 API; 요청 식별 User-Agent·공정 접근 제한(현재 **최대 10/s**), 발행/취득 시각 구분 |
| FINRA OTC Transparency | FINRA API `otcMarket/weeklySummary`, `weeklySummaryHistoric`  | 주간 ATS/비-ATS 종목·업체별 거래량/거래수 추세              | 데이터 공개 지연·주간 집계, 주문 방향/즉시 매수세를 나타내지 않음                             |
| 기업 IR                | 공식 IR 사이트·RSS·공시 연계                                  | 보도자료·실적 발표·투자자자료 메타데이터                    | 도메인별 약관/robots/저작권/수집 허용 여부 별도 확인                                          |
| 금융 뉴스 웹사이트     | 우선 라이선스 API/RSS, 허용 시 HTML                           | 제목·시각·URL·허용된 짧은 발췌 및 요약                      | 유료벽/CAPTCHA 우회 금지, 본문 재배포·장기 저장 금지 가능                                     |

### Official reference links

- SEC data APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC fair access: https://www.sec.gov/about/developer-resources
- SEC RSS resources: https://www.sec.gov/about/developer-resources
- FINRA developer docs: https://developer.finra.org/docs

## 5. 가격 시나리오 (단순 기본료, 2026-10-12 공개 가격 기준)

| 구성안                    |        유료 시장 데이터 기본료/월 | 적합성                       | 판단                                      |
| ------------------------- | --------------------------------: | ---------------------------- | ----------------------------------------- |
| Phase 0 계약 검증         |      **$0** (합성 fixture만 사용) | 스키마·UI/API 상태 계약 검증 | 외부 Provider·증권사 호출 없음            |
| 예시 A: 과거 1분봉        |         **$29** (Massive Starter) | 최근 5년의 1분봉·지연 시세   | Massive 플랜 예시, 채택 결정 아님         |
| 예시 B: 과거 1분봉+trades |       **$79** (Massive Developer) | 10년 1분봉 + trades          | 선택지 중 하나, 우선순위 없음             |
| 예시 C: 통합 시세         | **$99** (Alpaca Algo Trader Plus) | 통합 미국주식 실시간 데이터  | Alpaca 플랜 예시, 채택 결정 아님          |
| 예시 D: 장기+실시간       |       **$199** (Massive Advanced) | 20년+ 실시간 + trades/quotes | 선택지 중 하나, 우선순위 없음             |
| Tick/L2 확장              |                        **미산정** | Databento venue별 확대       | 데이터 종류·월 예상 GB·라이선스 견적 선행 |

**가격 해석:** 가격은 각 서비스의 일반 공개 월정액 또는 시작 가격이며, 할인·세금·환율·증권사 시세구독료·데이터 재배포/저장 사용권·거래소 라이선스·Databento 사용량은 별도다. 증권사 API를 이용한다고 모든 데이터가 공짜라고 가정하지 않는다. 각 플랜은 비교용 단독 예시이며 선택·병행 모두 미정이다. 병행 비용을 산정할 때에만 해당 플랜 요금을 합산한다. 예산이 아직 확정되지 않았으므로 임의 지출을 집행하지 않는다.

## 6. Phase 1 provider Proof of Concept (유료 결제 전)

- **테스트 종목:** AAPL, NVDA, 거래 드문 미국 소형주 1개(시범 계정에서 조회 가능한 종목으로 선정).
- **기간:** 최근 5거래일, 30거래일, 1년 전 날짜, 5년 전 날짜(해당 플랜/계정이 허용하는 경우).
- **기본 세션:** 정규장(09:30–16:00 ET), 프리/애프터는 별도 케이스. 미국 DST 및 반장 포함.

1. Broker read-only 로그인/토큰/권한, 정확한 API TR ID·파라미터·샘플 payload/오류코드와 데이터 보관 약관 확인.
2. 1m 봉 페이징·시간 정렬·중복 제거·최대 과거 시점 측정. 토스 `nextBefore`/KIS `KEYB` 등 각기 다른 페이징을 Adapter 계약으로 감싼다.
3. 1분봉 정규장 390개 구간과 결측/무거래 구간 구분, 수정주가 true/false, 기업행동 전후, 사후 정정·동일 봉 재조회 비교.
4. Paid vendor는 테스트 키로 데이터셋 제한·개인 저장·오프라인 분석·백테스팅·캐시 보존권과 요청 지연을 검증한다.
5. 3개 소스 교차검증: 동일 시각/세션/Raw 조건에서 OHLCV 비교; 오차 발견 시 `SourceConflict`로 기록하고 자동 splice 금지.
6. 비용 측정: 1종목×30거래일, 관심종목 20개×30거래일, 과거 1년 대상 종목 수 별 요청수/GB/쿼터 계산.
7. `CoverageManifest`(품질, 결측, 조정, 소스, 리비전, 사용권, point-in-time)를 고정하고, 동일 입력을 두 번 수집·정규화했을 때 canonical dataset hash와 coverage 결과가 같은지 검증한다. 공급업체가 사후 정정을 반환하면 새 리비전으로 분리되고 차이가 설명되어야 한다.
8. **Phase 1 Pass gate:** 1) 지원·이력·저장권이 확인된 실제 API 응답, 2) 페이징/재시도/충돌 테스트, 3) 사용량·비용 상한, 4) 동일 입력의 pinned dataset/manifest 재현성. 미달이면 해당 공급업체를 canonical로 승격하지 않는다. 동일 데이터로 2회 백테스트 결과가 같은지는 백테스트 엔진이 구현되는 Phase 5B의 별도 합격 조건이다.

## 7. Decisions and open questions

- **확정:** Alpaca와 Massive는 유료 API 후보로 함께 유지하며 **미리 선정하지 않는다**. 어느 공급업체도 과거 1분봉 또는 실시간 시세의 독점 기본값으로 정하지 않는다.
- **진행:** 증권사 및 두 유료 API의 기능 계약을 독립적으로 실증하고, 계정/플랜 사용 권한이 없는 공급업체는 `not_tested`로 남긴다. 공급업체 실제 도입은 데이터 요구와 검증 결과·예산이 명확해질 때 결정한다.
- **Canonical series**는 종목·기간·세션·데이터 지연 요구에 따라 소스 별도 지정. 백테스트는 무결성 검증된 단일 리비전/명시된 세그먼트를 사용; 소스 전환은 명시적 경고.
- 부가 데이터 계층은 PriceBar와 구분한 Event/Disclosure/ATSWeeklySummary로 저장.
- 권한 없는 비공식 수집, 보안 우회, 사용자 개인 계좌정보를 웹 수집으로 대체하는 기능은 금지.
- 아직 구입/가입/호출하지 않았으며 브로커 사용자 계정에 실제 접근하지 않았다.
