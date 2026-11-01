# ADR 0045 — SIW Agent의 Linux 실행: systemd 사용자 서비스

- **Status:** accepted
- **Date:** 2026-11-01
- **Related:** [ADR 0036](0036-desktop-app-roles.md)(앱 역할), [ADR 0037](0037-paper-order-execution.md) §6(상주 실행기), [ADR 0014](0014-provider-key-registry.md)(키체인)

## Context

사용자가 SIW Agent 전용 미니PC(GMKtec K12 후보)를 검토하다가, macOS를 유지하는 대신 **Linux로 이식**하기로 결정했다(2026-11-01). 지금까지 Agent는 macOS에서만 동작했다.

- 상주 실행기 설치·정지가 `launchctl`과 LaunchAgent에 묶여 있었다.
- 설정·로그 경로가 `~/Library` 아래였다.
- CI는 Tauri를 빌드하지 않았고, Rust 테스트도 돌리지 않았다.

## Decision

대상은 **Ubuntu 24.04 데스크톱(amd64)** 하나다. SIW Research는 Mac에 남고 이번 범위가 아니다.

| 항목      | macOS                                  | Linux                                                  |
| --------- | -------------------------------------- | ------------------------------------------------------ |
| 상주 실행 | LaunchAgent (`launchd.rs`)             | systemd 사용자 서비스 (`systemd.rs`)                   |
| 서비스    | `~/Library/LaunchAgents/<label>.plist` | `~/.config/systemd/user/<label>.service`               |
| 재시작    | `KeepAlive`(실패 시), 30초 간격        | `Restart=on-failure`, `RestartSec=30`                  |
| 설정 파일 | `~/Library/Application Support/...`    | `$XDG_CONFIG_HOME/com.pensivetraveler.siw.agent/`      |
| 로그      | `~/Library/Logs/siw-agent`             | `$XDG_STATE_HOME/siw-agent`                            |
| 키        | macOS 키체인                           | Secret Service(gnome-keyring), `keyring` 패키지 그대로 |
| 배포      | `tauri build`(`.app`)                  | `tauri build --bundles deb`(저장소가 있는 그 기기에서) |

- **화면과 명령은 그대로다.** Tauri 명령 4개(`runner_state`·`install`·`stop`·`uninstall`)와 API, 킬 스위치의 두 경로(DB 정지 상태, 서비스 정지)는 OS와 무관하게 같다.
- **설치는 여전히 사용자가 버튼을 눌렀을 때만** 일어난다. 유닛 파일에는 저장소 경로와 실행 명령만 있고 키는 없다.
- **정지의 의미도 같다.** `systemctl --user stop`은 `launchctl bootout`처럼 다음 로그인까지 멈춘다. 다시 로그인했을 때 주문을 막는 것은 DB의 정지 상태다.

### 재부팅 뒤에는 로그인이 필요하다(linger를 켜지 않는다)

실행기는 데모 키를 키링에서 읽고, 키링은 사용자가 로그인해야 열린다. 그래서 `loginctl enable-linger`와 자동 로그인을 쓰지 않는다.

- macOS의 사용자 LaunchAgent도 로그인 뒤에 뜨므로 동작이 같다.
- 정전이나 재부팅 뒤 사용자가 로그인할 때까지 실행기는 멈춰 있고 **주문도 나가지 않는다.** 주문이 나가는 쪽보다 안전한 실패다.
- 비밀번호 없는 키링이나 평문 파일은 쓰지 않는다. 키를 키체인에만 두는 규칙(ADR 0036)을 어기기 때문이다.

### 대안

- **Windows:** 파일 잠금(`msvcrt`) 경로가 미검증이고, 프로세스 그룹 정지(`killpg`)와 서비스 관리를 새로 짜야 한다. 자동 업데이트 재부팅도 무인 운영에 불리하다.
- **macOS 유지(Mac mini):** 이식이 필요 없지만 사용자가 Linux를 골랐다.
- **시스템 서비스(root):** 키링이 사용자 세션에 있어 키를 읽을 수 없고, 권한만 커진다.
- **TPM 등으로 키를 암호화해 무인 시작:** 가능하지만 키 보관 방식을 바꾸는 별도 결정이다. 모의투자 단계에는 필요하지 않다.

## 검증

- **Ubuntu 24.04 컨테이너(systemd 실행, 2026-11-01):**
  - `tauri build --bundles deb`: 성공, 2.92 MiB.
  - `cargo test`: 7개 통과. 진짜 사용자 서비스의 설치→실행→정지→재설치→제거 시험 1개 통과(경로에 공백 포함, 가짜 `uv`).
  - 실행기 `--once`: `idle`로 정상 종료.
  - 앱(xvfb): API 자동 기동, `/health` 200, 화면 렌더링, 앱 종료 때 띄운 API 정지.
  - 키링: 열림은 저장·읽기·삭제 성공. Secret Service 없음은 `NoKeyringError`, 잠김(표시 장치 없음)은 `KeyringLocked`로, 둘 다 `SecretStorageError`가 됐다.
- **CI:** `agent-linux` 작업이 빌드, Rust 테스트, 서비스 시험을 매번 돌린다. 필수 검사(`verify`)와는 별도다.
- **미검증:**
  - 실제 미니PC와 Ubuntu 데스크톱(Wayland, 실제 GPU의 WebKitGTK).
  - **잠긴 키링에서 데스크톱에 표시 장치가 있는 경우:** 컨테이너에서는 비밀번호 창을 띄우지 못해 바로 실패했다. 실제 데스크톱에서는 실행기가 키를 읽을 때 창이 뜨고 응답을 기다릴 수 있다. 로그인 비밀번호로 키링이 열리는 일반 로그인에서는 잠기지 않아야 한다.
  - 진짜 사용자 세션에서 로그인 직후 실행기와 키링의 시작 순서. 먼저 뜨면 키 읽기가 실패하고 다음 회차에 다시 시도한다(코드 경로상).
  - 재부팅 뒤 실제 로그인으로 실행기가 다시 뜨는지. 컨테이너 시험은 `enable`까지 확인했다.
  - 키움 모의 서버에 Linux에서 호출(코드는 같다. IP 등록이 필요할 수 있다).

## Consequences

- **기기 준비:** Tauri 필수 패키지(`libwebkit2gtk-4.1-dev`, `build-essential`, `curl`, `wget`, `file`, `libxdo-dev`, `libssl-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`), Rust, uv, Node, 저장소 복제. 앱은 실행 시 저장소가 필요하므로 그 기기에서 빌드한다.
- **한글 글꼴:** 글꼴이 없으면 화면의 한글이 네모로 나온다(컨테이너에서 확인). `.deb`의 의존성에 `fonts-noto-cjk`를 넣었다.
- **데이터와 키는 옮겨지지 않는다.** 미니PC의 키 화면에서 데모 키를 다시 등록한다.
- **아직 없는 것:**
  - Mac의 Research에서 승인한 전략 버전을 미니PC로 옮기는 경로. 네트워크 파일시스템의 SQLite 공유는 지원하지 않는다.
  - 모니터 없이 운영할 때의 원격 화면·킬 스위치.
  - 보호 주문(ADR 0044)을 켠다면, 재부팅 뒤 로그인 전까지 감시가 멈추는 문제.

## Revisit when

- 실계좌 단계(ADR 0036 단계 2)를 검토하거나 보호 주문을 켜서, 재부팅 뒤 로그인을 기다리는 시간이 위험이 될 때.
- 실제 미니PC 시험에서 위 미검증 항목이 다르게 나올 때.
