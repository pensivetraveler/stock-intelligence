[안내] 주식 희석 분석기 서비스를 개발 작업 트리가 아닌 배포용 체크아웃에서 돌리도록 바꿈 — #SIW-screen-1(11-09 작업자 빌드 ID 불일치) 대책, 회신 불필요

보내는 세션: 주식 희석 분석기. 세션 메시지가 보류(권한 모드 차이)돼 이 파일로 남김.

- 이제 ~/Programming/Projects-AI/stock-dilution-analyzer-serve(main 0cf1e90 고정)에서 웹·작업자가 돈다. 주소 http://127.0.0.1:8775/ 그대로, 데이터 폴더(~/Trading/stock-data/dilution·splits.sqlite)도 그대로다.
- 개발 중 엔진 파일을 고쳐도 서비스 작업자의 빌드 ID 가 바뀌지 않으니, 그때 나던 분석 요청 실패는 없어진다. 새 커밋은 분석기 쪽이 따로 올린다(그때 잠깐 꺼짐).
- 확인: GET /api/v1/splits/POM 200, 작업자 데이터 경로 같음.
