# Pine 확인: TradingView 대조와 실제 스크립트 변환율

두 가지 일을 사용자가 TradingView에서 하고, 결과를 세션에 주면 분석한다. 코딩 Agent는 TradingView에 로그인하거나 스크립트를 긁어 오지 않는다(AGENTS.md, 저작권).

## 1. Pine 템플릿을 TradingView에서 돌려 백테스트와 비교(ADR 0056 ⑤)

`GET /v1/backtests/strategies/{id}/pine`이 쓰는 템플릿(`services/backtest/pine_template.py`, v1)을 TradingView에서 실행한 적이 없다. 로컬에 있는 실제 백테스트 하나를 기준으로 삼는다.

### 기준 실행

- 실행 `run-98194b2769bd`(로컬 `data/backtests.sqlite3`, 2026-11-15), 엔진 `siw-backtest-1`, 체결 `next-open-slippage-v1`
- 종목 한미반도체 `042700`(KRX), 일봉, 2025-11-14 ~ 2026-11-13, 분할 수정가
- 봉 출처 `toss-kr`, 범위 `toss-kr-krx-nxt-integrated`(KRX와 NXT 통합), 244봉, 빠진 봉 0
- 전략(돈치안 돌파):
  - 진입: 종가 > 전날까지 20일 최고가
  - 청산: 종가 < 전날까지 10일 최저가
  - 자본 1,000만 원의 10%, 수수료·슬리피지 0, 포지션 1개
- 결과: 거래 3건, 순수익률 +8.32%, 최종 자본 10,832,100원

### TradingView에서 할 일

1. 차트를 `KRX:042700`, 일봉(1D)으로 연다. 설정의 "배당 조정"은 끈다(엔진은 분할만 수정했다).
2. Pine 편집기에 아래 스크립트를 붙여 넣고 "차트에 추가"를 누른다. 템플릿에 기간 두 줄(`inWindow`)만 손으로 더했다. 엔진은 2025-11-14부터의 봉만 봤고, TradingView는 그 전 봉으로도 거래하기 때문이다.
3. 전략 속성에서 초기 자본 10,000,000, 통화 KRW, 수수료 0, 슬리피지 0으로 둔다.
4. "전략 테스터 → 거래 목록"의 각 거래에서 진입·청산 날짜, 가격, 수량, 손익을 적어 세션에 준다(화면 사진도 된다).

```pine
//@version=6
// SIW Research rules strategy, Pine template v1 (ADR 0056).
// For a side-by-side check on TradingView; set fees and slippage to the run's.
// max_positions 1 is the engine's portfolio limit; this chart holds one symbol.
strategy("Donchian 20/10 breakout (SIW run-98194b2769bd)", overlay=true, pyramiding=1, default_qty_type=strategy.percent_of_equity, default_qty_value=10, process_orders_on_close=false)

// Added by hand for this check: the engine saw bars from 2025-11-14 to 2026-11-13 only.
inWindow = time >= timestamp("Asia/Seoul", 2025, 11, 14, 0, 0) and time < timestamp("Asia/Seoul", 2026, 11, 14, 0, 0)

s1 = ta.highest(high, 20)
s2 = ta.lowest(low, 10)

entrySignal = (close > s1[1]) and inWindow
exitSignal = (close < s2[1])
flat = strategy.position_size == 0

if entrySignal and flat
    strategy.entry("L", strategy.long)
if exitSignal and not flat
    strategy.close("L")
```

### 엔진 쪽 값(비교 대상)

날짜는 한국 시간 기준 봉 날짜다. 신호는 그 봉 종가에서, 체결은 다음 봉 시가다.

| #   | 진입 신호     | 진입 체결     | 진입가  | 수량 | 청산 신호     | 청산 체결     | 청산가  | 손익     | 수익률  |
| --- | ------------- | ------------- | ------- | ---- | ------------- | ------------- | ------- | -------- | ------- |
| 1   | 2025-12-26 금 | 2025-12-29 월 | 136,200 | 7    | 2026-03-26 목 | 2026-03-27 금 | 274,500 | +968,100 | +101.5% |
| 2   | 2026-04-27 월 | 2026-04-28 화 | 376,000 | 2    | 2026-05-15 금 | 2026-05-18 월 | 341,000 | −70,000  | −9.3%   |
| 3   | 2026-09-08 화 | 2026-09-09 수 | 250,000 | 4    | 2026-10-13 화 | 2026-10-14 수 | 233,500 | −66,000  | −6.6%   |

### 비교 항목과 미리 아는 차이

먼저 거래 수와 진입·청산 날짜를 본다. 그다음 가격, 수익률 순으로 본다. 수량과 원 단위 손익은 마지막에 본다.

- **봉 출처:** 엔진 봉은 KRX와 NXT 통합 시세다. TradingView의 `KRX:042700`은 KRX 정규장만일 수 있다. 시가(체결가)와 고가·저가(돈치안 값)가 달라질 수 있고, 그러면 날짜가 하루 밀리거나 거래가 생기고 없어진다. 가장 먼저 의심할 차이다.
- **수량:** 엔진은 정수 주식만 산다(10%인 100만 원 ÷ 136,200 → 7주). TradingView는 계산 기준(신호 봉 종가인지)과 소수 수량 처리가 다를 수 있다. 그래서 수량과 원 단위 손익보다 수익률을 먼저 맞춘다.
- **기간 끝:** 2026-11-13에 포지션이 열려 있으면 엔진은 거래로 세지 않는다. TradingView는 열린 거래로 보인다.
- **수정가:** 엔진은 2026-11-15에 알던 분할만 반영했다. 이 기간에 의심되는 분할은 없었다(`suspected_split_dates` 비어 있음).

## 2. 실제 TradingView 스크립트로 변환율 다시 재기(ADR 0058 4단계)

지금 변환율(14개 중 1개)은 앱 대화의 스크립트로만 쟀다. 사용자가 평소 쓰는 지표로 다시 잰다.

1. TradingView Pine 편집기에서 스크립트 전체를 복사한다. 공개 스크립트도 본인이 차트에 쓰는 것이면 된다. 지표(`indicator(...)`)가 대상이고, 전략(`strategy(...)`)은 따로 센다.
2. 원래 체크아웃(워크트리 아님)의 `data/pine-samples/<이름>/`에 한 스크립트당 파일 하나로 둔다. 확장자는 `.pine`, 인코딩은 UTF-8이다. 예: `data/pine-samples/tradingview/squeeze-momentum.pine`. `data/`는 커밋되지 않는다(저장소가 공개다).
   - 세션에 코드를 붙여 넣어도 된다. 그러면 세션이 같은 위치에 파일로 저장한다.
   - `//@version` 줄이 없으면 TradingView가 v1으로 보는 옛 스크립트일 수 있다. 그대로 넣는다.
3. 세션이 같은 측정 명령으로 다시 잰다(2026-11-25 `local-assistant`로 실행 확인).

```bash
cd apps/desktop && SIW_PINE_SAMPLES=<원래 체크아웃>/data/pine-samples/<이름> npx vitest run src/pine-convert.survey.test.ts
```

결과(변환된 수, 막는 기능 순위)는 ADR 0058 "4단계 결과"와 `docs/PROJECT_STATE.md`에 더한다. 변환율이 절반에 못 미치면 ADR 0058 "다시 볼 조건"대로 해석기 최적화나 Worker를 다시 본다.
