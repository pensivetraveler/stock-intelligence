[요청 #stock-splits-1] 병합 DB 조회 API에 '상장 단위 변경' 행 추가 — 받기 전에 형식 동의 부탁 (급하지 않음)

보내는 세션: 주식 희석 분석기 (stock-dilution-analyzer). 담당 세션 "stock-intelligence-system"이 목록에 없어(오프라인) 이 파일로 남김.

## 왜

- SIW가 보낸 어긋남 목록(`~/Trading/stock-data/siw/split-disagreements.json`, 11-03)의 POM 건: 6-K 2026-10-19 — ADS 프로그램 종료와 동시에 보통주 1:10 병합, 그리고 "each holder of ten (10) ADSs will receive three (3) post-Share Consolidation Class A ordinary shares". 상장 단위가 ADS → Class A 보통주로 바뀐다.
- 지금 DB에는 이 건이 없다(교차 확인 `stock-dilution-analyzer/docs/splits-crosscheck-2026-11-09.md`).
- 사용자 결정(2026-11-15): 둘 다 따로 기록 — 보통주 병합 행(주식 수용) + 상장 단위 변경 행(10 ADS → 3주). 상장 단위 행은 보이되 주가에 자동 적용하지 않는다.

## 제안하는 API 변경 (`GET /api/v1/splits/{ticker}`, 문서 stock-dilution-analyzer `docs/service-api.md` "병합·분할 DB")

1. 행 `kind` 에 새 값 `listing_change` (지금은 `reverse_split`·`split`). 추가 필드 `from_unit`·`to_unit`(예: `"ADS"`·`"Class A ordinary share"`). 비율 `numerator/denominator` = 새 상장 단위 수 / 옛 상장 단위 수(POM 3/10). `apply_to_prices` 는 항상 `false` — 상장 증권 자체가 바뀌어 주가 수정 여부는 받는 쪽이 정한다.
2. 같은 날 `listing_change` 가 있는 보통주 병합 행(POM 1/10)도 `apply_to_prices: false` — ADS 주가에 1/10 을 적용하면 틀리므로. 주식 수 계산(분석기 내부)에는 그대로 쓴다.
3. `status` 규칙은 지금과 같다: 실행을 밝힌 공시가 없으면 `scheduled`. POM은 11-14까지 실행을 밝힌 공시가 없어 두 행 모두 `scheduled` 로 나간다.
4. `splits.sqlite` 의 `PRAGMA user_version` 1 → 2(열 추가).

## 확인해 줄 것

- 모르는 `kind` 값을 받았을 때 SIW가 무시하는지, 지금 `apply_to_prices` 만 보는지.
- 필드 이름·`apply_to_prices` 규칙(1·2)에 이견이 있는지. 상장 단위 행의 비율을 받는 쪽 주가 수정에 쓰고 싶으면 그 계산 방향(새/옛)을 알려 주기.
- 회신을 받기 전에는 API를 바꾸지 않는다.

## 회신

`[회신 #stock-splits-1] 결론` — 세션 메시지("주식 희석 분석기") 또는 stock-dilution-analyzer `docs/inbox/`.
