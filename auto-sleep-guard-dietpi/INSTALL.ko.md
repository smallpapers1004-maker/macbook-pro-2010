# DietPi 맥북 자동 절전

배터리를 제거한 MacBookPro7,1에서 유선 Wake-on-LAN(WOL)을 유지하면서, 오디오와 서버 접속이 없는 때에 Suspend to RAM(S3)을 요청하는 Python 3 데몬입니다. Python 표준 라이브러리만 사용합니다.

## 1. 부팅 설정 확인

```sh
cat /proc/cmdline
cat /sys/power/state /sys/power/mem_sleep
ip -br link
```

- 데몬은 시작 시와 절전 요청 직전에 `/proc/cmdline`의 `init_on_alloc=0`을 확인합니다. 옵션이 없거나 확인할 수 없으면 `CRITICAL` 로그를 남기고 절전을 보류합니다. GRUB을 쓰는 DietPi에서는 `sudoedit /etc/default/grub`으로 `GRUB_CMDLINE_LINUX_DEFAULT`의 따옴표 안에 기존 옵션을 유지하며 `init_on_alloc=0`을 추가합니다. 이후 `sudo update-grub`과 재부팅을 하고 `/proc/cmdline`을 다시 확인합니다. 다른 부트로더를 사용한다면 해당 부트로더의 커널 명령줄에 적용합니다. [Debian의 MacBookPro7,1 설치 문서](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1)
- `/sys/power/state`에 `mem`, `/sys/power/mem_sleep`에 선택된 `[deep]`이 표시되어야 합니다. `deep`이 목록에 있고 선택만 안 되어 있으면 커널 명령줄에 `mem_sleep_default=deep`을 추가할 수 있습니다. `deep`이 제공되지 않으면 이 데몬은 절전을 보류합니다. [Linux 커널의 시스템 절전 상태 문서](https://docs.kernel.org/admin-guide/pm/sleep-states.html)
- 유선 인터페이스 이름과 MAC 주소를 확인합니다. 아래의 `enp2s0`은 예시입니다.

## 2. 설치와 감시 확인

ZIP을 푼 디렉터리에서 실행합니다.

```sh
sudo apt install python3 iproute2 ethtool conntrack
sudo install -Dm755 auto_sleep_guard.py /usr/local/sbin/auto_sleep_guard.py
sudo install -Dm644 auto-sleep-guard.service /etc/systemd/system/auto-sleep-guard.service
sudo install -Dm755 auto-sleep-guard-resume /lib/systemd/system-sleep/auto-sleep-guard-resume
sudo install -Dm644 70-auto-sleep-guard.conf /etc/systemd/sleep.conf.d/70-auto-sleep-guard.conf
sudo test -e /etc/default/auto-sleep-guard || sudo install -m644 auto-sleep-guard.env /etc/default/auto-sleep-guard
sudoedit /etc/default/auto-sleep-guard
sudo systemctl daemon-reload
```

`SLEEP_GUARD_INTERFACE`를 실제 유선랜 이름으로 바꿉니다. `70-auto-sleep-guard.conf`는 시스템 전체에서 일반 suspend가 `mem`만 시도하도록 설정합니다. 이미 절전 설정을 수정했다면 `systemd-analyze cat-config systemd/sleep.conf`에서 뒤의 파일이 `SuspendState=mem`을 덮어쓰거나, 최신 systemd의 `MemorySleepMode=`가 다른 모드를 선택하는지 확인합니다.

기존 서비스를 업데이트할 때는 아래 세 명령으로 새 스크립트를 설치하고 재시작합니다. 기존 설정 파일의 유선랜 이름, 유휴 시간, 시간대 설정은 계속 사용됩니다.

```sh
sudo apt install conntrack
sudo install -m755 auto_sleep_guard.py /usr/local/sbin/auto_sleep_guard.py
sudo systemctl restart auto-sleep-guard.service
```

**절전 없이 동작 확인**은 로컬 콘솔에서 실행합니다. 재생을 시작하거나 다른 기기에서 SSH로 접속하고, CasaOS 또는 공개 컨테이너 포트에도 연결해 각각의 보류 로그를 확인합니다. SSH 안에서 실행하면 그 SSH 연결도 감지됩니다.

```sh
sudo python3 /usr/local/sbin/auto_sleep_guard.py --interface enp2s0 \
  --dry-run --poll-seconds 5 --idle-minutes 1
```

**실제 WOL 확인**은 1단계에서 `init_on_alloc=0`과 `[deep]`을 확인하고 맥북 전원 버튼에 접근할 수 있을 때 진행합니다. 유선 케이블을 연결한 상태에서 아래 명령을 실행한 뒤, 같은 LAN의 다른 기기에서 맥북 유선 MAC 주소로 매직 패킷을 보냅니다. `ethtool`의 `Wake-on: g`는 드라이버가 설정을 보고한다는 뜻입니다. 배터리를 제거한 실제 기기가 S3에서 깨어나는지는 이 시험으로 확인합니다.

```sh
sudo ethtool -s enp2s0 wol g
sudo ethtool enp2s0 | grep -E 'Supports Wake-on:|Wake-on:'
sudo sync
sudo systemctl suspend
```

## 3. 서비스 시작

```sh
sudo systemctl enable --now auto-sleep-guard.service
sudo journalctl -fu auto-sleep-guard.service
```

설정 변경 후에는 `sudo systemctl restart auto-sleep-guard.service`를 실행합니다. 중지하려면 `sudo systemctl disable --now auto-sleep-guard.service`를 실행합니다.

| 설정 | 동작 |
| --- | --- |
| `SLEEP_GUARD_IDLE_MINUTES=30` | 기준 샘플을 잡은 뒤, 모든 검사에서 연속 30분 유휴로 판정될 때 절전 시도 |
| `SLEEP_GUARD_TRAFFIC_BYTES_PER_SECOND=50000` | 유선랜 또는 `docker0` 중 어느 하나의 RX·TX 합계가 직전 샘플 이후 평균 50 KB/s 이상이면 유휴 시간 초기화 |
| `SLEEP_GUARD_TCP_PORTS=22,445` | SSH·SMB 포트 설정. CasaOS `80`, 웹 리모컨 `8080`, Navidrome `4533`, Syncthing `8384/22000`은 기존 설정에서도 자동으로 추가됩니다. SSH 또는 SMB 포트를 변경했거나 앱에 다른 공개 포트를 쓴다면 이 목록에 해당 포트를 지정합니다. |
| `SLEEP_GUARD_HOURS_MODE=off` | 시간대 제한 해제. `block`은 지정 시간대 안에서 절전 차단. `allow-only`는 지정 시간대 안에서만 절전 허용 |
| `SLEEP_GUARD_ACTIVE_HOURS=19:00-23:00` | DietPi의 시스템 현지 시각 기준. 자정을 넘기는 `23:00-07:00`도 지원. 시작 포함, 종료 제외 |

데몬은 60초마다 검사합니다. 호스트 TCP 연결은 `ss`, Docker가 목적지 NAT로 공개한 TCP 연결은 `conntrack`에서 IPv4·IPv6의 변환 전 목적지 포트로 확인합니다. `docker0`이 없으면 해당 인터페이스의 트래픽 검사는 건너뜁니다. Docker 소켓이나 브리지가 있을 때 연결 추적 조회에 실패하면 절전을 보류합니다. 유선랜과 `docker0`의 전송량은 각각 계산합니다. 다른 Docker 브리지의 바이트 카운터는 감시 범위에 포함되지 않습니다. [Docker 포트 공개 문서](https://docs.docker.com/engine/network/port-publishing/)

ALSA 재생, 연결된 세션, 임계치를 넘은 트래픽, 시간대 차단 또는 감시 오류가 발견되면 유휴 시간이 초기화됩니다. 절전 전에는 WOL을 조회하고 필요하면 활성화한 뒤 다시 검증합니다. `sync` 이후 WOL과 활동을 재확인하고 커널 파라미터를 다시 읽은 뒤 `systemctl suspend`를 요청합니다. 복귀 훅은 데몬을 즉시 깨우고, 데몬은 커널 시계로 실제 복귀를 확인한 뒤 기상 시각을 기록하고 유휴 시간과 트래픽 기준을 초기화합니다.

재생과 TCP 세션은 검사 시점에 관찰합니다. 검사 사이에 끝난 짧은 활동은 빠질 수 있습니다. 시간대 정책은 절전 **진입**에만 적용됩니다. 이미 잠든 서버는 시간대가 바뀌어도 자동으로 기상하지 않으며, 일반 웹 요청이나 음성 비서 호출도 이 데몬에서 기상 신호로 사용하지 않습니다.

1~2 W는 실제 벽전력으로 확인할 값입니다. 절전 상태로 30일 내내 머물면 0.72~1.44 kWh이고, 실제 월 비용은 깨어 있는 시간과 적용 요금에 따라 달라집니다.
