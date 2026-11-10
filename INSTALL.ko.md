# 맥북 2010 오디오 웹 리모컨

`web_remote.py` 하나에 FastAPI 서버와 두 화면이 들어 있습니다. `/`는 스마트폰용 리모컨이며, `/frame`은 가로형 전체 화면 액자입니다. 리모컨 키를 입력하면 MPD 재생, 대기열, 음원 DB 새로고침, 음성 메모, 맥북 상태와 전원 버튼을 사용할 수 있습니다. 액자는 시계, 부산 날씨, 곡 정보와 실제 MPD 오디오 파형을 표시합니다. 기존 설치를 업데이트하는 경우 [CasaOS 대시보드 등록](#5-casaos-대시보드-등록)부터 진행하세요.

## 1. DietPi에 프로그램 설치

맥북에 `web_remote.py`를 복사하고 파일이 있는 디렉터리에서 실행하세요. Python 3.10 이상이 필요합니다. 아래 명령은 기존 MPD 서비스와 `macbook-assistant`의 메모 경로를 사용합니다.

```bash
sudo apt-get update
sudo apt-get install python3-venv openssl sudo mpd mpc ethtool
getent passwd macbook-assistant >/dev/null ||
  sudo useradd --system --user-group --shell /usr/sbin/nologin macbook-assistant
sudo install -d -m 0755 /opt/macbook-web /opt/macbook-assistant
sudo install -m 0755 web_remote.py /opt/macbook-web/web_remote.py
sudo python3 -m venv /opt/macbook-web/.venv
sudo /opt/macbook-web/.venv/bin/python -m pip install \
  'fastapi>=0.110,<1' 'pydantic>=2,<3' 'uvicorn>=0.27,<1' \
  'python-mpd2>=3.1,<4' 'psutil>=5.9,<8'
sudo install -d -o macbook-assistant -g macbook-assistant -m 0700 /var/lib/macbook-assistant
sudo test -e /opt/macbook-assistant/memo.json ||
  sudo test -L /opt/macbook-assistant/memo.json ||
  sudo ln -s /var/lib/macbook-assistant/memo.json /opt/macbook-assistant/memo.json
```

기존 음성 비서와 웹 서비스는 같은 `macbook-assistant` 계정으로 실행합니다. 두 프로그램이 실제 메모 경로에 같은 `memo.json.lock`을 사용하므로, 음성으로 메모를 추가하는 동안 웹에서 저장해도 쓰기가 서로 덮어쓰이지 않습니다. 웹에 목록을 띄워둔 사이 메모가 바뀌면 삭제 전에 목록을 갱신합니다.

이미 일반 파일로 존재하는 `/opt/macbook-assistant/memo.json`이나 커스텀 `MEMO_PATH`를 쓰는 경우, 두 서비스가 같은 경로를 보도록 맞추세요. 프로그램 디렉터리에 파일을 직접 저장한 경우 음성 비서를 잠시 멈추고 백업한 다음, 메모를 `/var/lib/macbook-assistant/memo.json`으로 옮겨 소유자를 `macbook-assistant:macbook-assistant`, 권한을 `0600`으로 설정하세요. 원래 경로에 위 심볼릭 링크를 만든 뒤 음성 비서를 재시작하면 됩니다. 기존 상태 디렉터리에 이미 메모가 있다면 두 파일을 확인한 후 옮기세요.

## 2. MPD 광출력과 액자 파형

`mpc status`로 MPD가 응답하는지 확인하세요. 기존 Mini-TOSLINK ALSA 출력은 유지합니다. 광출력에 하드웨어 믹서가 없다면 **기존 ALSA 출력 블록 안에** `mixer_type "software"`를 설정하면 웹 볼륨 슬라이더를 쓸 수 있습니다. [MPD 출력 설정](https://mpd.readthedocs.io/en/stable/mpd.conf.5.html)

실제 파형을 표시하려면 먼저 아래 설정으로 FIFO를 만들고, `mpd --version`의 출력 플러그인에 `fifo`가 있는지 확인합니다. 이 설정은 기본 MPD 계정이 `mpd`인 경우입니다.

```bash
sudo tee /etc/tmpfiles.d/macbook-audio.conf >/dev/null <<'EOF'
d /run/macbook-audio 0755 root root -
p /run/macbook-audio/pcm 0660 mpd macbook-assistant -
EOF
sudo systemd-tmpfiles --create /etc/tmpfiles.d/macbook-audio.conf
mpd --version
```

`sudoedit /etc/mpd.conf`로 아래 출력 블록을 **추가**하세요. PCM은 서버 내부 FIFO에서 파형 막대로 변환됩니다. 브라우저는 가벼운 막대 값만 받습니다. [MPD FIFO 플러그인](https://mpd.readthedocs.io/en/stable/plugins.html#fifo)

```text
audio_output {
    type        "fifo"
    name        "MacBook Now Playing PCM"
    path        "/run/macbook-audio/pcm"
    format      "16000:16:1"
    mixer_type  "none"
}
```

```bash
sudo systemctl enable mpd.service
sudo systemctl restart mpd.service
mpc outputs
```

`MacBook Now Playing PCM`이 비활성화로 나오면 `mpc enable <출력번호>`로 켜세요. FIFO를 설정하기 전에도 재생 정보, 액자 시계, 날씨를 사용할 수 있습니다.

## 3. 리모컨 키와 화면 백라이트 설정

최초 한 번만 아래 명령으로 키를 생성하세요. 스마트폰 첫 접속 때 표시된 `REMOTE_KEY`의 등호 뒤 값을 입력합니다.

```bash
sudo install -d -m 0755 /etc/macbook-web
sudo sh -c 'umask 077; set -C; { printf "REMOTE_KEY="; openssl rand -hex 32; } > /etc/macbook-web/web.env'
sudo cat /etc/macbook-web/web.env
ls /sys/class/backlight
```

옵션은 `sudoedit /etc/macbook-web/web.env`로 추가하세요. 아래는 예시입니다. 부산이 기본값입니다.

```ini
CITY_NAME=부산
CITY_LAT_LON=35.1796,129.0756
HEALTH_DISK_PATH=/
# BACKLIGHT_DEVICE=gmux_backlight
# MPD_PASSWORD=여기에_MPD_암호
# CASAOS_ORIGINS=http://192.168.0.10
```

`web_remote.py`는 모든 IPv4 인터페이스의 `8080` 포트에서 실행됩니다. 이 구성은 신뢰하는 집 내부 네트워크에서 사용하고, 공유기에서 8080 포트를 인터넷으로 포워딩하지 마세요. 메모, 시스템 상태, 제어 API는 리모컨 키가 필요하고, 액자에 필요한 곡 정보·날씨·파형은 내부 네트워크에서 바로 열립니다. 원격지에서 접속해야 한다면 HTTPS나 개인 VPN을 통해 접속하세요.

`ls /sys/class/backlight` 결과에서 실제 내장 디스플레이를 제어하는 장치 이름을 `BACKLIGHT_DEVICE`에 넣습니다. 장치가 하나라면 자동 선택됩니다. 두 개 이상이면 이름을 지정해야 합니다. 아래 예시의 `gmux_backlight`를 실제 이름으로 바꿔서 udev 규칙을 적용하세요. 이 규칙은 선택한 장치의 `brightness` 속성에만 서비스 계정 그룹의 쓰기 권한을 부여합니다. [Linux 백라이트 속성](https://cdn.kernel.org/doc/html/latest/gpu/backlight.html)

```bash
sudo tee /etc/udev/rules.d/90-macbook-web-backlight.rules >/dev/null <<'EOF'
ACTION=="add", SUBSYSTEM=="backlight", KERNEL=="gmux_backlight", RUN+="/usr/bin/chgrp macbook-assistant /sys%p/brightness", RUN+="/usr/bin/chmod g+w /sys%p/brightness"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=backlight
sudo udevadm settle
sudo -u macbook-assistant test -w /sys/class/backlight/gmux_backlight/brightness &&
  echo '백라이트 쓰기 가능'
```

마지막 검사의 장치 이름도 같은 값으로 바꾸세요. 프로그램은 끌 때 `brightness=0`을 쓰고, 켤 때 이전 밝기를 복원합니다. 서버를 재시작한 뒤 이전 밝기를 모르면 최대 밝기의 60%에서 켭니다. 실제 화면이 완전히 꺼지는지는 MacBook의 백라이트 드라이버에서 확인하세요. 장치가 보이지 않으면 리모컨에 설정 필요 상태가 표시됩니다.

## 4. 절전 권한과 systemd 자동 실행

`command -v systemctl`과 `command -v sudo`로 각각 `/usr/bin/systemctl`, `/usr/bin/sudo`를 확인하세요. `sudo visudo -f /etc/sudoers.d/macbook-web`로 아래 한 줄을 등록합니다. 웹 프로그램에서 실행하는 명령은 `/usr/bin/sudo -n /usr/bin/systemctl suspend`입니다. [Debian sudoers 매뉴얼](https://manpages.debian.org/bookworm/sudo/sudoers.5.en.html)

```sudoers
macbook-assistant ALL=(root) NOPASSWD: /usr/bin/systemctl suspend
```

```bash
sudo chmod 0440 /etc/sudoers.d/macbook-web
sudo visudo -c
sudo -l -U macbook-assistant /usr/bin/systemctl suspend
```

마지막 명령은 정책 확인용이며 맥북을 절전시키지 않습니다. 다음으로 서비스 파일을 등록하세요.

```bash
sudo tee /etc/systemd/system/macbook-web.service >/dev/null <<'EOF'
[Unit]
Description=MacBook MPD web remote and Now Playing frame
Wants=mpd.service
After=network.target mpd.service

[Service]
Type=simple
User=macbook-assistant
Group=macbook-assistant
WorkingDirectory=/opt/macbook-web
EnvironmentFile=/etc/macbook-web/web.env
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/macbook-web/.venv/bin/python /opt/macbook-web/web_remote.py
Restart=on-failure
RestartSec=5
StateDirectory=macbook-assistant
StateDirectoryMode=0700
UMask=0077
ProtectSystem=full
NoNewPrivileges=no

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now macbook-web.service
sudo systemctl status macbook-web.service
sudo journalctl -u macbook-web.service -n 40 --no-pager
```

웹 서비스는 `sudo -n`이 동작하도록 `NoNewPrivileges=no`를 사용합니다. 기존 음성 비서 서비스에는 해당 설정을 변경할 필요가 없습니다. `web.env`를 수정했다면 `sudo systemctl restart macbook-web.service`로 반영하세요.

## 5. CasaOS 대시보드 등록

기존 웹 리모컨을 업데이트하려면 내려받은 `web_remote.py`가 있는 폴더에서 다음 명령을 실행하세요. 서비스의 환경 파일에 저장된 기존 리모컨 키를 그대로 사용합니다.

```bash
sudo install -m 0755 web_remote.py /opt/macbook-web/web_remote.py
sudo systemctl restart macbook-web.service
sudo ss -ltnp 'sport = :8080'
```

CasaOS를 기본 HTTP 포트 80에 두고 웹 리모컨은 `0.0.0.0:8080`에서 실행합니다. 기존 `web.env`의 `REMOTE_HOST`, `REMOTE_PORT` 항목은 지워도 됩니다. 다른 Docker 컨테이너나 서비스가 호스트의 8080을 이미 사용 중이면, 그 서비스의 포트 매핑을 확인한 뒤 8080을 비워 주세요.

CasaOS 대시보드의 **앱 → + → 외부 링크 추가**에서 아래 값을 입력하고 **연결**을 누르세요. 예시의 IP는 맥북의 LAN IP로 바꿉니다. [CasaOS 외부 링크 설정](https://github.com/IceWhaleTech/CasaOS-UI/blob/main/src/components/Apps/ExternalLinkPanel.vue)

| CasaOS 입력 항목 | 값 예시 |
| --- | --- |
| 웹 주소 | `http://192.168.0.10:8080/` |
| 앱 이름 | `맥북 오디오 리모컨` |
| 아이콘 URL | `http://192.168.0.10:8080/casaos-icon.svg` |

리모컨의 **CasaOS 대시보드** 버튼은 현재 페이지 호스트의 HTTP 80번 포트로 이동합니다. **액자 모드** 버튼은 같은 웹 리모컨의 `/frame`을 엽니다. 대시보드와 리모컨을 오갈 때는 두 주소에 같은 LAN 호스트 이름이나 IP를 사용하세요.

아이콘으로 페이지를 여는 데는 CORS 설정이 필요하지 않습니다. CasaOS 페이지의 스크립트에서 리모컨 API를 직접 호출할 경우 `/etc/macbook-web/web.env`에 실제 CasaOS 출처를 추가하고 웹 리모컨을 재시작하세요. 로컬호스트의 HTTP 기본 포트 출처는 기본 허용됩니다. 여러 주소는 쉼표로 구분합니다. 보호된 API에는 계속 `X-Remote-Key`가 필요합니다. [FastAPI CORS 설정](https://fastapi.tiangolo.com/tutorial/cors/)

```ini
CASAOS_ORIGINS=http://192.168.0.10,http://macbook.local
```

CasaOS Files에서 `/DATA/Media/Music`에 음원을 올린 뒤 리모컨의 **MPD 음원 DB 새로고침**을 누르세요. MPD의 `music_directory`가 이 경로를 가리키거나 해당 폴더가 라이브러리에 연결되어 있어야 합니다. 화면의 작업 번호는 스캔 접수 번호입니다. 스캔이 끝나면 새 곡을 라이브러리에서 찾을 수 있고, 재생하려면 MPD 대기열에 추가하면 됩니다. [MPD update 명령](https://mpd.readthedocs.io/en/stable/protocol.html#update)

## 6. 접속과 실제 장비 점검

| 화면 | 주소와 사용법 |
| --- | --- |
| 스마트폰 리모컨 | `http://맥북-LAN-IP:8080/`에서 `REMOTE_KEY` 입력 |
| 맥북 액자 | 맥북 브라우저에서 `http://127.0.0.1:8080/frame`을 열고 전체 화면 버튼 선택 |
| 브라우저 키오스크 | 그래픽 데스크톱 세션에서 `chromium --kiosk http://127.0.0.1:8080/frame` 실행 |

`hostname -I`로 맥북의 LAN 주소를 확인할 수 있습니다. DietPi가 콘솔 전용으로 설치됐다면 맥북 내장 화면에서 액자를 띄우려면 그래픽 세션과 브라우저가 필요합니다. 다른 기기에서 액자를 열 때는 LAN 주소를 사용하세요. 시계는 화면을 띄운 기기의 시간대를 따릅니다. 날씨는 Open-Meteo에서 가져오며 성공 후 15분간 서버에 캐시합니다. 연결이 끊기면 이전 관측 시각을 함께 표시합니다. [Open-Meteo API](https://open-meteo.com/en/docs)

1. MPD 대기열의 곡을 재생하고 이전·다음·일시 정지와 볼륨을 확인합니다. 액자에서 **MPD · 실시간 파형**이 표시되면 FIFO가 동작 중입니다.
2. 웹에서 메모를 추가하고 음성 비서에서 읽어 봅니다. 음성으로 추가한 뒤 웹 목록에서도 확인합니다.
3. 팬과 온도가 비어 있으면 `sudo modprobe applesmc`, `sudo modprobe coretemp` 후 센서 노출 여부를 확인합니다. CPU 온도는 `coretemp`를 우선하고 없으면 Apple SMC의 CPU 센서를 사용합니다. SSD 수치는 `HEALTH_DISK_PATH`가 속한 파일시스템의 사용률입니다.
4. 스마트폰에서 백라이트를 끈 뒤 다시 켜고, 정상적으로 밝기가 복원되는지 확인합니다.
5. 유선 이더넷에서 `ip -br link`로 인터페이스 이름을 찾고, 예시의 `enp2s0`을 바꿔 `sudo ethtool enp2s0`을 실행합니다. `Supports Wake-on:`에 `g`가 있으면 `sudo ethtool -s enp2s0 wol g`로 매직 패킷을 활성화하고 `Wake-on: g`인지 확인합니다. 절전 버튼을 누른 뒤 같은 LAN의 WOL 앱에서 유선 MAC 주소로 매직 패킷을 보내 검증하세요. 먼저 전원 버튼으로도 깨어나는지 확인해 두면 WOL 테스트에 실패했을 때 복귀할 수 있습니다. 재부팅 뒤 WOL 설정이 유지되는지는 DietPi의 네트워크 설정에 따라 다릅니다. [ethtool 매뉴얼](https://man7.org/linux/man-pages/man8/ethtool.8.html)

절전 버튼은 systemd에 절전을 요청합니다. WOL 성공 여부와 1~2W 소비전력 달성 여부는 해당 맥북과 유선 랜의 절전 상태에 달려 있습니다. 실제 소비전력은 어댑터 쪽 전력계에서 확인하세요. [systemctl 매뉴얼](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html)
