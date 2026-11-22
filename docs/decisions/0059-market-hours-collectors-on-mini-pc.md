# ADR 0059 — 장중 수집기를 미니PC로 옮김

- **Status:** accepted (방향). 2026-11-23 사용자가 "지금 미니PC로 이전 착수"를 골랐다. 아래 구현 단계는 단계마다 사용자 승인을 받은 뒤에 구현한다.
- **Date:** 2026-11-23
- **Related:** [ADR 0045](0045-agent-on-linux.md)(Linux 실행, systemd 사용자 서비스, 로그인해야 열리는 키링), [ADR 0051](0051-kiwoom-realtime-condition-watch.md)(`watch-condition`), [ADR 0057](0057-selection-flow-seven-stages.md)(`watch-halts`, 7단계 흐름), [ADR 0036](0036-desktop-app-roles.md)(키는 기기마다 등록), [런북](../runbooks/agent-mini-pc.md)

## Context

### 장중 기록 공백 (대시보드 항목 261)

- 미국 정규장은 KST 23:30–06:00이다(11월 기준). `watch-condition`은 뉴욕 04:00–20:00, 즉 KST 18:00–다음 날 10:00에 기록한다.
- 이 Mac(iMac)은 밤에 꺼져 있었다. `pmset -g log`와 `last`를 보면 11-17·11-18·11-19에 소프트웨어 종료가 있었고, 11-19 17:01에 꺼진 뒤 11-20 10:29에 켜졌다. 11-18·11-19 장 기록은 없고, 11-17 기록은 00:57 KST에 끊겼다.
- Mac 설정: `autorestart 0`, `sleep 1`. 잠자기를 막는 것은 열려 있는 앱(Claude 등)뿐이다.

### 지금의 수집기

- `com.siw.collector.watch-condition`(키움 실시간 조건검색)과 `com.siw.collector.watch-halts`(Alpaca SIP 상태)는 macOS LaunchAgent다. `~/.local/share/siw/collector-app`의 배포본을 `KeepAlive`로 돌리고, `--data-dir`은 Mac 저장소의 `data/`다.
- 둘 다 키를 키체인에서 읽는다(`CredentialStore` + `KeyringSecretBackend`). `watch-condition`은 키움 키를, `watch-halts`는 Alpaca 키를 쓴다.
- 기록은 `SIW_DATA_DIR/screeners/kiwoom_condition_watch.sqlite3`와 `sip_status_watch.sqlite3`에 덧붙인다. Mac의 Research와 API(`services/screening/`)가 이 파일을 읽는다.
- Linux용 서비스 파일은 없다. ADR 0045가 미니PC로 옮긴 것은 SIW Agent와 실행기뿐이다.
- 키움은 계좌 하나에 WebSocket 연결을 하나만 허용한다. 다른 연결이 열리면 먼저 열린 연결이 닫힌다(ADR 0051, 2026-11-05 실측).

### 사용자 결정 (2026-11-23)

선택지는 (1) 당분간 Mac을 장중에 켜 두고 이전은 나중에, (2) 지금 미니PC로 이전 착수, (3) 장중 공백 감수였다. 사용자는 (2)를 골랐다.

## Decision

### 1. 장중 수집기는 미니PC에서 돈다

- `watch-condition`과 `watch-halts`를 미니PC(Ubuntu 24.04)의 **systemd 사용자 서비스**로 돌린다. ADR 0045의 실행기와 같은 규칙을 따른다.
  - 설치는 사용자가 실행했을 때만 한다.
  - 유닛 파일에는 경로와 명령만 넣고 키는 넣지 않는다.
  - linger와 자동 로그인은 쓰지 않는다. 재부팅하면 로그인할 때까지 수집이 멈추고, 그 구간은 기존 규칙대로 `interrupted`·빈틈으로 남는다.
- 키는 미니PC의 SIW Agent "키" 탭(PR #209)에서 다시 등록한다. Mac 키체인에서 옮기지 않는다.
- **두 기기에서 동시에 돌리지 않는다.** 같은 키움 계좌로 두 연결이 열리면 서로를 끊는다. 미니PC에서 첫 세션을 정상으로 기록한 것을 확인한 뒤에 Mac LaunchAgent를 내린다. 내리기 전까지는 미니PC 쪽을 시험할 때만 Mac 쪽을 멈춘다.

### 2. 기록은 Mac이 미니PC에서 가져온다

- 미니PC가 원본이다. Mac은 읽기 전용 사본을 둔다.
- 방식 초안: Mac이 SSH로 미니PC에서 SQLite 백업 사본(`sqlite3 .backup`)을 만들게 한 뒤 받아 온다. 네트워크 파일시스템으로 SQLite를 공유하지 않고(ADR 0045와 같은 이유), 미니PC API를 LAN에 열지 않는다.
- 사본에는 받아 온 시각과 원본 기기를 남긴다. 사본이 오래됐으면 화면에서 알 수 있어야 한다.
- 언제 받아 올지(Mac이 켜질 때, 화면을 열 때, 주기적으로)는 2단계에서 정한다.

### 3. 구현 단계 (단계마다 사용자 승인)

1. **Linux 수집기 서비스:** systemd 사용자 유닛 설치·정지 명령, 미니PC 배포본 위치, `SIW_DATA_DIR`. 미니PC에 키움·Alpaca 키를 등록하고, 키움 호출이 미니PC 공인 IP에서 되는지 확인한다. 첫 장 세션을 미니PC에서 기록한 뒤 Mac LaunchAgent를 내린다.
   - 2026-11-23 사용자 승인("ADR 0059 1단계 착수해줘")으로 구현했다. `python -m services.collector.systemd install|start|stop|uninstall|status|print`가 유닛 두 개(`com.pensivetraveler.siw.collector.watch-condition`·`watch-halts`)를 쓴다.
   - 배포본은 미니PC의 저장소 복제본(`~/stock-intelligence`)이다. Mac처럼 별도 배포본을 두지 않는다. 그 기기는 개발에 쓰지 않기 때문이다.
   - 데이터 폴더는 `--data-dir`, 없으면 `.env`의 `SIW_DATA_DIR`(저장소 기준)이다. 키 탭이 쓰는 API와 같은 키 목록을 읽어야 하기 때문이다.
   - `Restart=always`, 60초 간격으로, Mac LaunchAgent의 `KeepAlive`·`ThrottleInterval 60`과 같다. 로그인 직후 키링보다 먼저 떠서 실패해도 다시 시작한다.
   - `watch-condition`은 **키움 실계좌 키**로 구독한다(조회만 한다). 미니PC에 실계좌 키를 등록해야 한다.
   - 순서와 되돌리기는 [런북](../runbooks/agent-mini-pc.md) 4a절에 있다.
2. **Mac으로 가져오기:** 2절 방식을 구현하고, 사본의 신선도를 표시한다.
3. **흐름과 실행:** 선정 흐름 3~7단계(ADR 0057)와 Agent 실행을 장중에 미니PC에서 돌릴지는 따로 정한다. Research에서 승인한 전략을 미니PC로 옮기는 경로(ADR 0045의 남은 일)와 묶여 있다.

## 고려한 대안

- **Mac을 장중에 켜 둠:** 개발이 필요 없고 바로 공백을 막는다. 대신 전력을 쓰고, 사용자가 끄면 다시 공백이 생긴다. 사용자가 고르지 않았다.
- **장중 공백 감수:** 실시간 조건검색과 거래정지 감시가 빠져 ADR 0057의 1·3단계가 비게 된다.
- **미니PC가 Mac에 기록을 밀어 넣음:** Mac은 밤에 꺼져 있어 밀어 넣을 곳이 없다. Mac 쪽에 받는 서비스도 열어야 한다.

## Consequences

- 이전을 마칠 때까지 Mac이 꺼져 있는 장은 계속 비어 있다.
- 미니PC가 꺼지거나 로그인 전이면 기록이 없다. 정전 뒤 자동 켜짐은 런북 1절에 있다. 재부팅 뒤 로그인은 원격 로그인으로 할 수 있다(런북 2a절, PR #217).
- Mac의 Research는 사본을 읽으므로, 가져오기 전에는 지난밤 장이 보이지 않는다.
- 키움 계좌의 연결 하나를 미니PC 수집기가 장중 내내 쓴다. Mac에서 1회 조건검색(ADR 0048)을 돌리면 미니PC 구독이 끊기고, 수집기가 다시 연결한다. 이 동작은 지금 Mac 한 대에서도 같다.

## 다시 볼 조건

- 미니PC가 장중에 자주 꺼지거나 로그인이 늦어 빈틈이 커지는 경우
- 키움이 미니PC의 IP를 받아들이지 않는 경우
- 공용 데이터 공간(ADR 0046)이 기기 사이 공유를 다루게 되는 경우
