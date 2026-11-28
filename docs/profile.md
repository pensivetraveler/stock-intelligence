---
profile_format: 1
id: stock-intelligence
purpose: "미국 주식 중심의 로컬 우선 차트·매매 복기·백테스트·전략 연구 워크스테이션. 모멘텀 종목을 고르고(조건검색·뉴스 재료·희석·장외 체결) 가격대와 매수·매도 스크립트를 만들어 SIW Agent 모의투자로 넘긴다. 매매 기법 자료를 기법 카드와 규칙 엔진 전략으로 옮겨 검증한다."
milestone: "ADR 0057 종목 선정 7단계: 1~4단계 장중 확인 → 5단계 가격대·6단계 스크립트 → 7단계 모의투자 연결 → 주 1회 희석 검수 → 조건 시뮬레이터"
updated: 2026-11-28
kl:
  recommend: on
  tags: [매매기법]
  categories: [주식, tools, lessons]
  keywords:
    [
      모멘텀 매매,
      단타,
      눌림목,
      돌파 매수,
      트레일링 스톱,
      손절,
      분할 익절,
      차트 지표,
      거래량,
      백테스트,
      Pine Script,
      주식 희석,
      장외 체결,
      다크풀,
      공매도,
      뉴스 재료,
      거래 정지,
      미국 주식 시장 구조,
      키움 API,
      Alpaca,
    ]
  exclude: []
---

# stock-intelligence (SIW, Stock Intelligence Workstation)

M7 추천 파일럿 세 곳 가운데 하나다(2026-11-17 사용자 Q9). 앱은 둘로 나뉜다. Electron(SIW Research)은 연구·백테스트 앱이고, Tauri(SIW Agent)는 승인된 전략을 실행하는 앱이다(ADR 0036). 주문은 모의투자가 먼저이며, 실계좌 주문은 사용자가 주문마다 승인한다(ADR 0047).

추천받고 싶은 자료:

- 매매 기법(영상·책·글). 이미 태그 `매매기법` 구독으로 기법 카드 목록에 들어오고 있다(`services/technique_cards/library.py`). 추천은 이 태그 밖에서 쓸 만한 것을 찾는 용도다.
- 종목 선정 근거. 급등 재료와 저영향 재료, 희석 공시 읽는 법, 장외 체결·공매도 자료 해석.
- 시장 구조와 데이터. 거래 시간·halt·SIP, 분봉·틱 품질, 수정주가.
- 도구와 교훈. 백테스트의 미래 데이터 누출, 지표 구현, 증권사 API 운용 사례.

빼는 것은 따로 두지 않았다. 글자가 깨진 자료(예: 11-25 KDI PDF)는 도서관 수용 단계에서 걸러진다고 들었다.

형식: ai-collab `ai-collab-structure.md` §6-6(v0.84). 고치는 세션은 "SIW 총괄"이고, ADR 0057 착수 순서의 단계가 바뀌면 `milestone`·`updated`도 함께 고친다. 계좌·보유 종목·키 같은 내용은 이 파일에 쓰지 않는다.
