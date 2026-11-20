# 런북 — SIW Agent 미니PC 설치 (Ubuntu 24.04)

- **기준 결정:** [ADR 0045](../decisions/0045-agent-on-linux.md)(Linux 실행, systemd 사용자 서비스)
- **작성:** 2026-11-20
- **기기:** GMKtec K12. 지금 512GB SSD와 16GB RAM이고, 2026-12에 2TB SSD와 16GB RAM을 더할 예정이다.
- **네트워크:** iMac과 같은 사설 LAN

이 문서는 순서와 확인 항목이다. 실제 기기에서 확인한 결과는 마지막 절의 표와 `docs/PROJECT_STATE.md`에 적는다.

## 0. 안전 원칙

- 자동 로그인과 `loginctl enable-linger`를 쓰지 않는다. 실행기는 로그인해야 열리는 키링에서 키를 읽는다. 재부팅 뒤 로그인할 때까지 실행기는 멈추고 주문도 나가지 않는다(ADR 0045).
- 키·계좌번호는 앱의 키 화면에만 넣는다. 셸 기록, 파일, 이 문서에 남기지 않는다.
- 로컬 API는 `127.0.0.1:8765`에만 연다. 방화벽을 바꿔 LAN에 열지 않는다.
- 코딩 Agent(SSH 접속 세션 포함)는 실계좌 주문을 내거나 실계좌 엔드포인트를 시험하지 않는다(AGENTS.md).

## 1. OS 설치 (사용자)

1. iMac에서 Ubuntu 24.04 LTS **데스크톱** amd64 ISO를 ubuntu.com에서 받아 USB 설치 디스크를 만든다. Server판이나 다른 배포판은 화면(WebKitGTK)과 키링 경로를 다시 시험해야 하므로 쓰지 않는다.
2. K12 BIOS에서 확인한다.
   - USB 부팅 순서
   - 정전 뒤 자동 켜짐(Restore on AC Power Loss → Power On). 켜져도 로그인 전까지 실행기는 멈춰 있다.
3. 설치 프로그램에서 고른다.
   - 디스크: 지금 있는 512GB에 설치한다. 출고 때 깔린 Windows는 지워진다.
   - 디스크 암호화(LUKS): 선택이다. 켜면 부팅할 때마다 기기 앞에서 암호를 넣어야 한다. 로그인도 기기 앞에서 해야 하므로 추가 부담은 작다.
   - 계정: 로그인 비밀번호를 정하고, **자동 로그인은 끈다.** 키링이 이 비밀번호로 열린다.
4. 설치 뒤 업데이트한다.

   ```bash
   sudo apt update && sudo apt full-upgrade -y
   ```

5. 자동 절전을 끈다. 장중에 잠들면 실행기도 멈춘다. 화면 잠금은 켜 둬도 된다.

   ```bash
   gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
   ```

6. 시간 동기화를 확인한다. `System clock synchronized: yes`와 `NTP service: active`가 나와야 한다.

   ```bash
   timedatectl
   ```

## 2. 네트워크와 SSH (사용자)

1. 유선 LAN에 연결하고, 공유기에서 이 기기의 MAC 주소로 **DHCP 고정 IP를 예약**한다.
2. SSH 서버를 설치한다.

   ```bash
   sudo apt install -y openssh-server
   ```

3. iMac에서 공개키를 복사한 뒤 접속을 확인한다. 주소와 계정은 실제 값으로 바꾼다.

   ```bash
   ssh-copy-id <계정>@<미니PC IP>
   ```

4. 키 접속이 되면 비밀번호 로그인을 끈다.

   ```bash
   echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/10-no-password.conf && sudo systemctl restart ssh
   ```

5. 방화벽으로 LAN의 SSH만 허용한다. 대역은 실제 LAN 대역으로 바꾼다.

   ```bash
   sudo ufw allow from 192.168.0.0/24 to any port 22 proto tcp && sudo ufw enable
   ```

여기까지 되면 이후 단계는 iMac의 세션이 SSH로 같이 진행할 수 있다. 앱 화면 확인과 키 등록은 기기 앞에서 한다.

## 3. 개발 도구와 빌드

CI의 `agent-linux` 작업(`.github/workflows/ci.yml`)과 같은 구성이다. 앱은 실행할 때 저장소가 필요하므로 이 기기에서 빌드한다.

1. 시스템 패키지:

   ```bash
   sudo apt install -y git libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev fonts-noto-cjk
   ```

2. 도구: 각 공식 설치 방법을 따른다.
   - Rust: rustup
   - Python: uv(`.python-version` = 3.10)
   - Node `22.23.2`, pnpm `11.25.0`(CI와 같은 버전)
3. 저장소 복제와 빌드:

   ```bash
   git clone https://github.com/pensivetraveler/stock-intelligence.git ~/stock-intelligence
   ```

   ```bash
   cd ~/stock-intelligence && pnpm install --frozen-lockfile && cd apps/desktop && pnpm exec tauri build --bundles deb
   ```

4. 만들어진 `.deb`를 설치한다(`apps/desktop/src-tauri/target/release/bundle/deb/`).

   ```bash
   sudo apt install ./apps/desktop/src-tauri/target/release/bundle/deb/*.deb
   ```

5. `.env`를 만든다. 없으면 API가 `SIW_DATA_DIR` 없이 떠서 모의투자 API가 모두 503(`paper_storage_unconfigured`)을 돌려준다. 키는 `.env`에 넣지 않는다(키 화면 → 키링).

   ```bash
   cd ~/stock-intelligence && cp .env.example .env && chmod 600 .env
   ```

6. API가 쓸 Python 환경을 미리 만든다. 앱이 처음 뜰 때 기다리는 시간이 줄어든다.

   ```bash
   cd ~/stock-intelligence && uv sync
   ```

2026-11-20 실측: Ubuntu 24.04.5(커널 7.0), Rust 1.100, uv 0.13.9, Node 22.23.2(nvm), pnpm 11.25.0에서 `tauri build --bundles deb` 71초, 결과 `SIW Agent_0.4.0-dev_amd64.deb`(2.93 MiB, 패키지 이름 `siw-agent`, 실행 파일 `/usr/bin/stock-intelligence-workstation`).

## 4. 첫 실행 (기기 앞에서)

1. SIW Agent를 열고 API가 자동으로 뜨는지 확인한다.
2. 키는 Mac에서 옮겨지지 않는다. 키 화면에서 **데모(모의) 키부터** 등록한다.
3. 실행기 설치 버튼을 눌러 systemd 사용자 서비스로 등록한다. 모의 실행기는 `com.pensivetraveler.siw.agent.runner.service`, 실계좌 실행기는 `com.pensivetraveler.siw.agent.live-runner.service`다. 서비스 파일은 `~/.config/systemd/user/`, 로그는 `$XDG_STATE_HOME/siw-agent`(보통 `~/.local/state/siw-agent`)에 있다.
4. 키움 모의 호출이 되는지 본다. 같은 공유기 뒤라 공인 IP가 iMac과 같으면 등록한 IP가 그대로 통할 가능성이 크지만 확인 전이다.

## 5. 실기기 확인표 (ADR 0045 미검증 항목)

| 항목                                                | 결과                                                        | 날짜       |
| --------------------------------------------------- | ----------------------------------------------------------- | ---------- |
| Wayland·실제 GPU에서 화면 렌더링, 한글 표시         | 통과(Wayland, AMD Phoenix3, 사용자 눈 확인)                 | 2026-11-20 |
| 로그인 직후 실행기와 키링의 시작 순서, 키 읽기 성공 | 미실행                                                      |            |
| 키링이 잠겼을 때 비밀번호 창 동작                   | 미실행                                                      |            |
| 재부팅 → 로그인 뒤 실행기 자동 재기동               | 미실행                                                      |            |
| 앱 종료 때 앱이 띄운 API 정지                       | 통과(종료 뒤 uv·uvicorn 프로세스 사라짐, 재실행 때 새로 뜸) | 2026-11-20 |
| Linux에서 키움 모의 서버 호출(IP 등록 포함)         | 미실행                                                      |            |
| 킬 스위치: 화면 버튼, SSH의 `systemctl --user stop` | 미실행                                                      |            |

## 6. 2026-12 하드웨어 증설 (사용자)

전원을 끄고 전원선을 뺀 뒤 작업한다. 실행기가 돌고 있으면 앱에서 먼저 정지한다.

- **RAM:** K12의 출고 램 구성(한 개 또는 두 개)은 확인하지 않았다. 슬롯이 둘 다 차 있으면 16GB를 더하려면 교체해야 한다. 증설 뒤 `free -h`로 용량을 확인한다.
- **2TB SSD:** OS는 512GB에 그대로 둔다. 2TB는 ext4로 포맷해 `/data`처럼 붙이고, `/etc/fstab`에는 UUID로 적는다. 시세 저장(`SIW_DATA_DIR`) 등을 옮길지는 그때 정한다. 옮기기 전의 데이터 경로와 백업을 먼저 확인한다.
- 증설 뒤 5절의 재부팅 → 로그인 항목을 한 번 더 확인한다.

## 7. 실계좌 운영 전에 남은 일

- Mac의 SIW Research에서 승인한 전략 버전을 미니PC로 옮기는 경로가 없다. 네트워크 파일시스템으로 SQLite를 공유하는 방식은 지원하지 않는다.
- 원격 화면·원격 킬 스위치가 없다. 당장은 SSH에서 `systemctl --user stop com.pensivetraveler.siw.agent.runner.service com.pensivetraveler.siw.agent.live-runner.service`가 대안이다. 다시 로그인했을 때 주문을 막는 것은 DB의 정지 상태다.
- ADR 0047(실계좌, 주문마다 승인)이 열렸으므로 ADR 0045의 재검토 조건에 해당한다. 특히 재부팅 뒤 로그인 전까지 보호 주문(ADR 0044) 감시가 멈추는 문제를 다시 본다.
