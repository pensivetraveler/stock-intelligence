# 런북 — SIW Agent 미니PC 설치 (Ubuntu 24.04)

- **기준 결정:** [ADR 0045](../decisions/0045-agent-on-linux.md)(Linux 실행, systemd 사용자 서비스)
- **작성:** 2026-11-20
- **기기:** GMKtec K12. 지금 512GB SSD와 16GB RAM이고, 2026-12에 2TB SSD와 16GB RAM을 더할 예정이다.
- **네트워크:** iMac과 같은 사설 LAN

이 문서는 순서와 확인 항목이다. 실제 기기에서 확인한 결과는 마지막 절의 표와 `docs/PROJECT_STATE.md`에 적는다.

## 0. 안전 원칙

- 자동 로그인과 `loginctl enable-linger`를 쓰지 않는다. 실행기는 로그인해야 열리는 키링에서 키를 읽는다. 재부팅 뒤 로그인할 때까지 실행기는 멈추고 주문도 나가지 않는다(ADR 0045). 로그인은 기기 앞이나 원격 로그인(2a절)으로 사람이 비밀번호를 넣어 한다.
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

여기까지 되면 이후 단계는 iMac의 세션이 SSH로 같이 진행할 수 있다. 앱 화면 확인과 키 등록은 기기 앞이나 원격 화면(2a절)에서 한다.

## 2a. 원격 화면 (사용자)

GNOME 원격 데스크톱(RDP)의 두 기능을 포트를 나눠 함께 쓴다. **xrdp는 설치하지 않는다.** 3389를 두고 충돌한다(2026-11-20 실제로 겪음).

| 기능                         | 붙는 화면                      | 포트 | 쓰는 때                                                   |
| ---------------------------- | ------------------------------ | ---- | --------------------------------------------------------- |
| 원격 로그인(시스템 서비스)   | GDM 로그인 화면 → 원격 세션    | 3389 | 재부팅 뒤 기기 앞에 가지 않고 로그인할 때, 평소 원격 작업 |
| 데스크톱 공유(사용자 서비스) | 기기 앞에서 이미 로그인한 세션 | 3390 | 기기 앞에서 로그인했을 때 그 화면을 볼 때                 |

1. 방화벽에 3389·3390을 LAN에만 연다.

   ```bash
   sudo ufw allow from 192.168.0.0/24 to any port 3389 proto tcp && sudo ufw allow from 192.168.0.0/24 to any port 3390 proto tcp
   ```

2. 데스크톱 공유를 3390으로 옮긴다: `grdctl rdp set-port 3390 && systemctl --user restart gnome-remote-desktop.service`
3. 기기 앞(또는 3390)에서 **설정 → 시스템 → 원격 데스크톱 → 원격 로그인** 탭을 잠금 해제하고 켠다. 접속용 사용자 이름·비밀번호는 여기서 정한다. 명령줄(`grdctl --system rdp set-credentials`)은 비밀번호가 셸 기록에 남으므로 쓰지 않는다.
4. iMac에서 접속한다. **Mac용 Windows App은 원격 로그인에 쓸 수 없다.** 서버가 연결을 넘겨준 두 번째 연결의 인증에서 실패해 검은 화면이 된다(미니PC 로그 `NTLM ... MIC verification failed`, 계정을 "필요할 때 묻기"로 바꿔도 같음, 2026-11-22). FreeRDP를 쓴다. Intel Mac에서는 Homebrew가 완성본을 주지 않아 MacPorts로 설치했다(의존 패키지는 완성본, FreeRDP 본체만 소스 빌드).

   ```bash
   sudo /opt/local/bin/port -N install FreeRDP
   ```

   ```bash
   /opt/local/bin/sdl3-freerdp /v:<미니PC IP> /u:<원격 로그인 사용자> /cert:tofu /dynamic-resolution /size:1600x1000
   ```

   비밀번호는 FreeRDP 창이 묻는다. Domain은 비운다. 연결이 넘겨질 때마다 창이 다시 열려 모니터를 옮겨 다닐 수 있다(고장 아님). 이어서 GDM에서 Ubuntu 계정으로 로그인한다.

알아 둘 것:

- 원격 로그인은 **기기 앞의 로컬 세션이 없을 때** 쓴다. 같은 사용자가 기기 앞에서 로그인해 있으면 3389 접속이 검은 화면이 된다.
- FreeRDP 창을 닫아도 원격 세션은 남고, 다시 접속하면 같은 바탕화면으로 돌아온다. 창을 닫는 것과 로그아웃은 다르다. 로그아웃하면 실행기도 멈춘다.
- 데스크톱 공유는 **화면이 잠긴 세션에 새 접속을 받지 않는다**(로그 `Session creation inhibited`). SSH에서 `loginctl list-sessions`로 세션 번호를 찾아 `loginctl unlock-session <번호>`로 풀 수 있다. 그동안 기기 모니터 화면도 풀려 있다.
- 이전 실패 시도에서 남은 빈 로그인 화면 세션(gdm, `c2` 같은 번호)은 해가 없고 재부팅 때 사라진다.

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

   공백이 든 값은 큰따옴표로 감싼다(예: `SIW_SEC_USER_AGENT="이름 메일"`). 수집기 유닛은 `uv run --env-file .env`로 읽는데, uv는 따옴표 없는 공백 값에서 `Failed to parse environment file` 경고만 내고 **그 줄부터 끝까지 모든 변수를 버린다**(2026-11-25 미니PC uv 0.13.9 실측). `set -a; . ./.env`를 쓰는 스크립트는 같은 줄에서 종료 127로 끝난다. 확인: `grep -nE '^[A-Z_]+=[^"#]*[[:space:]]' .env`가 아무것도 내지 않아야 한다.

6. API가 쓸 Python 환경을 미리 만든다. 앱이 처음 뜰 때 기다리는 시간이 줄어든다.

   ```bash
   cd ~/stock-intelligence && uv sync
   ```

2026-11-20 실측: Ubuntu 24.04.5(커널 7.0), Rust 1.100, uv 0.13.9, Node 22.23.2(nvm), pnpm 11.25.0에서 `tauri build --bundles deb` 71초, 결과 `SIW Agent_0.4.0-dev_amd64.deb`(2.93 MiB, 패키지 이름 `siw-agent`, 실행 파일 `/usr/bin/stock-intelligence-workstation`).

## 4. 첫 실행 (기기 앞에서)

1. SIW Agent를 열고 API가 자동으로 뜨는지 확인한다.
2. 키는 Mac에서 옮겨지지 않는다. SIW Agent의 **"키" 탭**에서 **데모(모의) 키부터** 등록한다(PR #209, ADR 0036 2026-11-20 개정). 키 탭이 없는 이전 빌드라면 미니PC에서 저장소의 Research 웹을 임시로 띄워(`cd ~/stock-intelligence/apps/desktop && pnpm exec vite --host 127.0.0.1 --port 5173`) Firefox로 `http://127.0.0.1:5173`의 키 화면을 쓰고, 등록 뒤 끈다.
3. 실행기 설치 버튼을 눌러 systemd 사용자 서비스로 등록한다. 모의 실행기는 `com.pensivetraveler.siw.agent.runner.service`, 실계좌 실행기는 `com.pensivetraveler.siw.agent.live-runner.service`다. 서비스 파일은 `~/.config/systemd/user/`, 로그는 `$XDG_STATE_HOME/siw-agent`(보통 `~/.local/state/siw-agent`)에 있다.
4. 키움 모의 호출이 되는지 본다. 같은 공유기 뒤라 공인 IP가 iMac과 같으면 등록한 IP가 그대로 통할 가능성이 크지만 확인 전이다.

## 4a. 장중 수집기 (ADR 0059 1단계)

`watch-condition`(키움 실시간 조건검색)과 `watch-halts`(Alpaca SIP 거래정지)를 이 기기의 systemd 사용자 서비스로 돌린다. 규칙은 실행기와 같다. 로그인해야 키링이 열리고, linger는 쓰지 않으며, 유닛 파일에는 키가 없다.

**같은 계좌로 두 기기에서 동시에 돌리지 않는다.** 키움과 Alpaca는 실시간 연결을 하나만 받아, 두 번째 연결이 열리면 먼저 열린 쪽이 끊긴다(ADR 0051).

1. **키 (기기 앞이나 원격 화면에서):** SIW Agent "키" 탭에서 다음을 등록한다.
   - **키움 실계좌 키**: `watch-condition`은 실계좌 키로 조건검색을 구독한다(조회만 하고 주문은 없다). 키움에 등록한 IP에서만 통한다.
   - **Alpaca 키**: `watch-halts`가 쓴다.
2. **Mac의 수집기를 멈춘다 (iMac 터미널):** plist는 남겨 둔다. 되돌릴 때 다시 올린다.

   ```bash
   launchctl bootout gui/$(id -u)/com.siw.collector.watch-condition; launchctl bootout gui/$(id -u)/com.siw.collector.watch-halts
   ```

3. **미니PC에 설치한다 (SSH 가능):** 데이터 폴더는 `.env`의 `SIW_DATA_DIR`이다. 키 탭이 쓰는 API와 같은 곳이어야 키 목록을 읽는다. 먼저 `print`로 유닛을 확인하고 설치한다.

   ```bash
   cd ~/stock-intelligence && git pull && uv sync && uv run python -m services.collector.systemd print
   ```

   ```bash
   cd ~/stock-intelligence && uv run python -m services.collector.systemd install
   ```

   - 유닛: `~/.config/systemd/user/com.pensivetraveler.siw.collector.watch-condition.service`, `…watch-halts.service`
   - 로그: `~/.local/state/siw-collector/watch-condition.log`, `watch-halts.log`
   - 상태는 `uv run python -m services.collector.systemd status`로 본다. 장 밖에서는 로그에 `"status":"waiting"`이 찍힌다.
   - 키가 없거나 키링이 잠겨 있으면 수집기가 종료되고, 60초 뒤 다시 시작한다(`Restart=always`).

4. **첫 장 세션 확인:** 다음 날 프리마켓부터 기록이 쌓였는지 미니PC에서 본다. 날짜는 뉴욕 기준이다.

   ```bash
   cd ~/stock-intelligence && uv run python -m services.collector export-condition --date <YYYY-MM-DD> --data-dir data | head -40
   ```

5. 정상이면 Mac의 LaunchAgent plist를 지운다(`~/Library/LaunchAgents/com.siw.collector.watch-*.plist`). Mac Research로 기록을 가져오는 경로는 ADR 0059 2단계다. 그때까지 Mac 화면에는 미니PC 기록이 보이지 않는다.

**되돌리기:** 미니PC에서 `uv run python -m services.collector.systemd uninstall`을 실행하고, Mac에서 다시 올린다.

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.siw.collector.watch-condition.plist; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.siw.collector.watch-halts.plist
```

## 5. 실기기 확인표 (ADR 0045 미검증 항목)

| 항목                                                                              | 결과                                                                               | 날짜       |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ---------- |
| Wayland·실제 GPU에서 화면 렌더링, 한글 표시                                       | 통과(Wayland, AMD Phoenix3, 사용자 눈 확인)                                        | 2026-11-20 |
| 로그인 직후 실행기와 키링의 시작 순서, 키 읽기 성공                               | 미실행                                                                             |            |
| 키링이 잠겼을 때 비밀번호 창 동작                                                 | 미실행                                                                             |            |
| 재부팅 → 로그인 뒤 실행기 자동 재기동                                             | 미실행                                                                             |            |
| 재부팅 → 원격 로그인(3389, FreeRDP), 키링 열림, 끊어도 세션 유지·같은 세션 재접속 | 통과(원격 Wayland 세션, 키링 `Locked=false`). 실행기 미설치 상태라 재기동은 미확인 | 2026-11-23 |
| 앱 종료 때 앱이 띄운 API 정지                                                     | 통과(종료 뒤 uv·uvicorn 프로세스 사라짐, 재실행 때 새로 뜸)                        | 2026-11-20 |
| Linux에서 키움 모의 서버 호출(IP 등록 포함)                                       | 통과(키 탭 등록 키를 키링에서 읽어 `mockapi.kiwoom.com` 토큰 발급, 공인 IP는 iMac과 같음) | 2026-11-28 |
| 킬 스위치: 화면 버튼, SSH의 `systemctl --user stop`                               | SSH stop 통과(1초, `paper runner stopped`). 화면 버튼 통과(`POST /v1/paper/kill` 200, 상태 `halted=true`, 실행기 heartbeat 계속). **문제:** 앱 "설치하고 시작"의 `restart`가 한 번 멈춰 SIGTERM 90초 시간 초과 뒤 SIGKILL, 그동안 앱 "응답 없음"(아래 주) | 2026-11-28 |
| 장중 수집기 설치, 미니PC 공인 IP에서 키움 실계좌 조건검색 구독                    | 설치 통과(두 유닛 active, 키링에서 키 읽고 `waiting`). 실계좌 구독은 첫 장 세션에서 확인 | 2026-11-28 |
| 미니PC 수집기로 첫 장 세션 기록(프리마켓~애프터마켓)                              | 미실행                                                                             |            |

주(2026-11-28): systemd 기본 `KillMode=control-group`은 SIGTERM을 `uv`와 Python 자식 모두에 보내고, `uv`가 받은 신호를 자식에게 다시 넘긴다. 그래서 Python은 SIGTERM을 두 번 받는다. 신호 처리기가 `threading.Event.set()`을 부르는 지금 코드(모의·실계좌 실행기, 수집기)는 두 번째 신호가 첫 처리기 안에서 끼어들면 Event 잠금에서 교착한다. Mac 재현: SIGTERM 세 번이면 200회 중 15회 멈춤, 한 번이면 0회. 멈추면 `TimeoutStopSec`(90초) 뒤 SIGKILL로 끝나고 종료 기록이 남지 않는다. macOS launchd는 주 프로세스에만 보내 해당하지 않는다. 고칠 때까지 SSH 킬 스위치가 늦으면 90초를 기다리거나 `systemctl --user kill -s KILL <유닛>`을 쓴다.

## 6. 2026-12 하드웨어 증설 (사용자)

전원을 끄고 전원선을 뺀 뒤 작업한다. 실행기가 돌고 있으면 앱에서 먼저 정지한다.

- **RAM:** K12의 출고 램 구성(한 개 또는 두 개)은 확인하지 않았다. 슬롯이 둘 다 차 있으면 16GB를 더하려면 교체해야 한다. 증설 뒤 `free -h`로 용량을 확인한다.
- **2TB SSD:** OS는 512GB에 그대로 둔다. 2TB는 ext4로 포맷해 `/data`처럼 붙이고, `/etc/fstab`에는 UUID로 적는다. 시세 저장(`SIW_DATA_DIR`) 등을 옮길지는 그때 정한다. 옮기기 전의 데이터 경로와 백업을 먼저 확인한다.
- 증설 뒤 5절의 재부팅 → 로그인 항목을 한 번 더 확인한다.

## 7. 실계좌 운영 전에 남은 일

- Mac의 SIW Research에서 승인한 전략 버전을 미니PC로 옮기는 경로가 없다. 네트워크 파일시스템으로 SQLite를 공유하는 방식은 지원하지 않는다.
- 원격 화면은 2a절(원격 로그인)로 생겼다. 원격 킬 스위치는 없다. 당장은 SSH에서 `systemctl --user stop com.pensivetraveler.siw.agent.runner.service com.pensivetraveler.siw.agent.live-runner.service`가 대안이다. 다시 로그인했을 때 주문을 막는 것은 DB의 정지 상태다.
- ADR 0047(실계좌, 주문마다 승인)이 열렸으므로 ADR 0045의 재검토 조건에 해당한다. 특히 재부팅 뒤 로그인 전까지 보호 주문(ADR 0044) 감시가 멈추는 문제를 다시 본다.
