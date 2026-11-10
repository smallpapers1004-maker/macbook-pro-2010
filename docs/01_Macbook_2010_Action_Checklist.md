# 구형 맥북 프로 2010 만능 홈 서버 구축 실행 체크리스트 (DietPi + CasaOS 웹 UI 에디션)

배터리와 기존 저장장치가 탈거된 **맥북 프로 2010형(MacBookPro7,1 등)**에 초경량 [DietPi](https://github.com/MichaIng/DietPi)를 깔고, 그 위에 아이패드·앱스토어처럼 직관적인 웹 그래픽 환경인 **[CasaOS](https://github.com/icewhaletech/casaos)**를 얹어 **저전력 홈 서버, 광출력 네트워크 DAC, 그리고 [Google AI Studio](https://aistudio.google.com/docs/libraries) 기반 AI 음성 비서**로 구축하는 단계별 행동 지침서입니다.

복잡한 파란색 텍스트 화면(TUI)을 붙잡고 씨름할 필요 없이, **초기 설치만 끝나면 내 스마트폰이나 메인 PC 웹 브라우저에서 아이콘 클릭만으로 파일 탐색기와 서버 앱을 관리**할 수 있습니다 ([CasaOS on DietPi 구축 사례](https://github.com/IceWhaleTech/CasaOS/discussions/1948)).

## 전체 프로젝트 진행 순서 요약

| 단계 | 작업 구간 | 핵심 행동 목표 | 예상 소요 시간 |
| :--- | :--- | :--- | :--- |
| **Phase 0** | 사전 준비물 확보 | SATA SSD, USB 메모리, 유선 LAN, 광케이블 등 필수 부품 점검 | 15분 |
| **Phase 1** | 하드웨어 조립 및 배치 | SSD 장착, MagSafe 단자 물리적 고정, 광케이블/랜선 직결 | 30분 |
| **Phase 2** | [DietPi](https://github.com/MichaIng/DietPi) 설치 및 드라이버 패치 | USB 부팅 설치, GRUB 절전 패치(`nouveau`), [mbpfan](https://github.com/linux-on-mac/mbpfan) 설정 | 45분 |
| **Phase 3** | **[CasaOS](https://github.com/icewhaletech/casaos) 웹 그래픽 UI & DAC 구축** | **한 줄 명령어로 CasaOS 설치, 웹 브라우저 파일 공유, 광출력 오디오 세팅** | **25분** |
| **Phase 4** | 원격 전원 제어 (WOL/플러그) | 유선 LAN 매직 패킷(Wake-on-LAN) 또는 스마트 플러그 자동 부팅 구성 | 20분 |
| **Phase 5** | AI 음성 비서 배포 | [Google Gen AI SDK](https://github.com/googleapis/python-genai) 및 [edge-tts](https://github.com/rany2/edge-tts) 연동 음성 비서 상시 서비스화 | 40분 |

## Phase 0. 사전 준비물 체크리스트

작업 시작 전 아래 준비물이 모두 구비되었는지 먼저 확인합니다.

- [ ] **2.5인치 SATA SSD (120GB~240GB 이상)**: 탈거된 드라이브 자리에 장착할 부팅 및 데이터 저장용 SSD와 십자(Phillips #00) 및 별(Torx T6) 드라이버를 준비합니다.
- [ ] **USB 메모리 (8GB 이상) & 다른 PC**: [DietPi](https://github.com/MichaIng/DietPi) 설치 이미지를 굽기 위한 공 USB 메모리와 이미지 플래싱용 PC를 준비합니다.
- [ ] **기가비트 유선 LAN 케이블 (필수)**: 맥북 프로 2010의 유선 랜(`Broadcom BCM5764M`)은 리눅스 기본 `tg3` 드라이버로 설치 즉시 인식되지만, 내장 Wi-Fi(`Broadcom BCM4322`)는 기본 이미지에서 바로 잡히지 않으므로 최초 설치 및 WOL 구동을 위해 유선 랜선을 반드시 준비합니다 ([Debian Wiki - MacBookPro7,1](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1), [DietPi Community Forum](https://dietpi.com/forum/t/installing-dietpi-on-macbook-pro-2012-as-native-pc-x86-64-does-not-detect-integrated-wifi-adapter/25152)).
- [ ] **오디오 연결 케이블**:
  - 외장 DAC/앰프 광입력 사용 시: **Mini-TOSLINK(3.5mm 광단자) to TOSLINK(각형 광단자)** 광케이블을 준비합니다.
  - USB DAC 사용 시: DAC 규격에 맞는 USB A to B/C 케이블을 준비합니다.
- [ ] **MagSafe 단자 고정용 벨크로 타이/케이블 클립**: 배터리가 없는 상태이므로 전원선이 빠지면 즉시 전원이 차단됩니다. 전원선을 물리적으로 고정할 테이프나 클립을 준비합니다.
- [ ] **(선택) Wi-Fi 스마트 플러그**: 절전 모드(WOL) 대신 완전 전원 차단 후 원격 부팅(Auto-Boot) 방식을 원할 경우 준비합니다.

## Phase 1. 하드웨어 장착 및 물리적 안전 배치

배터리 탈거 상태의 맥북 프로 2010을 서버실처럼 안정적인 물리 환경으로 세팅하는 단계입니다.

- [ ] **Step 1-1. 2.5인치 SATA SSD 장착**:
  - 맥북 하판 나사 10개를 분리하고 하판 커버를 엽니다.
  - 기존 하드디스크 브래킷에 2.5인치 SATA SSD를 고정하고 SATA 데이터/전원 커넥터를 연결한 뒤 하판을 다시 닫습니다.
- [ ] **Step 1-2. 설치 위치 선정 및 방열 공간 확보**:
  - 공유기 및 오디오 DAC 근처의 평평하고 통풍이 잘되는 선반에 배치합니다.
  - 알루미늄 하판 전체가 방열판 역할을 하므로, 바닥에 두꺼운 천을 깔지 않고 살짝 공기가 통하도록 고무 받침대나 노트북 스탠드를 활용합니다.
- [ ] **Step 1-3. 유선 LAN 및 오디오 케이블 연결**:
  - 맥북 좌측 기가비트 이더넷 포트에 공유기에서 나온 유선 LAN 케이블을 꽂습니다.
  - 3.5mm 오디오 포트에 Mini-TOSLINK 광케이블(또는 USB 포트에 USB DAC)을 연결합니다.
- [ ] **Step 1-4. MagSafe 전원선 물리적 고정 (필수 안전 조치)**:
  - MagSafe 어댑터를 연결한 뒤, 발에 걸리거나 애완동물·청소 등으로 자석 단자가 분리되지 않도록 선반 기둥이나 벽면에 케이블 클립·벨크로로 단단히 고정합니다.

## Phase 2. 초경량 OS(DietPi) 설치 및 맥북 전용 드라이버·커널 최적화

부팅 후 RAM 점유율이 100MB 미만인 데비안 기반 초경량 배포판 [DietPi](https://github.com/MichaIng/DietPi)를 밑바탕으로 깔고, 맥북 프로 2010 특유의 그래픽 절전 버그 및 무선 드라이버 이슈를 해결합니다.

- [ ] **Step 2-1. 부팅 USB 제작**:
  - 다른 PC에서 [DietPi 공식 문서](https://dietpi.com/docs/software/)의 **Native PC (UEFI / BIOS CSM x86_64)** 설치 이미지를 내려받아 Rufus 또는 BalenaEtcher로 USB 메모리에 플래싱합니다.
- [ ] **Step 2-2. 맥북 USB 부팅 및 OS 설치 (EFI 부팅 우회법 포함)**:
  - 맥북에 유선 LAN과 USB를 꽂고 전원을 켠 직후 키보드의 `Option(Alt)` 키를 길게 눌러 부팅 디스크 선택 화면을 띄웁니다.
  - `EFI Boot`(USB 메모리)를 선택해 내장 SATA SSD에 [DietPi](https://github.com/MichaIng/DietPi)를 설치합니다.
  - **만약 구형 맥 EFI 호환성 문제로 DietPi USB가 부팅되지 않는다면**: 순정 데비안 최소 설치 이미지([Debian netinst ISO](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1))로 먼저 기본 데비안을 설치한 뒤, 터미널에서 공식 변환 스크립트([PREP_SYSTEM_FOR_DIETPI.sh](https://github.com/MichaIng/DietPi/blob/master/PREP_SYSTEM_FOR_DIETPI.sh))를 실행해 DietPi로 전환합니다 ([DietPi GitHub Issue #1755](https://github.com/MichaIng/DietPi/issues/1755)).
- [ ] **Step 2-3. 공유기에서 맥북 IP 고정 (DHCP Reservation)**:
  - 공유기 관리자 페이지(예: `192.168.0.1`)에 접속해 맥북 유선 랜의 MAC 주소를 확인하고 고정 내부 IP(예: `192.168.0.100`)를 할당합니다. 이후 모든 작업은 내 주력 PC나 스마트폰에서 `ssh root@192.168.0.100`으로 접속해 복사·붙여넣기로 편하게 진행합니다.
- [ ] **Step 2-4. GRUB 커널 파라미터 패치 (절전 모드 먹통 & DMA 오류 해결 — 필수)**:
  - 맥북 프로 2010의 Nvidia GeForce 320M 오픈소스 `nouveau` 드라이버가 최신 커널에서 절전 모드(`suspend`) 진입·복귀 시 멈추는 현상과 브로드컴 DMA 에러(`b43-phy0 ERROR: Fatal DMA error`)를 막기 위해 [Debian Wiki - MacBookPro7,1](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1) 권장 옵션을 추가합니다.
  ```bash
  sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="init_on_alloc=0 intel_iommu=off /' /etc/default/grub
  sudo update-grub
  ```
- [ ] **Step 2-5. 상판 닫힘(Clamshell) 절전 진입 방지 설정**:
  - 맥북 상판을 닫아도 서버가 잠들지 않도록 아래 명령어로 `/etc/systemd/logind.conf`를 수정합니다.
  ```bash
  sudo sed -i 's/#HandleLidSwitch=suspend/HandleLidSwitch=ignore/g' /etc/systemd/logind.conf
  sudo sed -i 's/HandleLidSwitch=suspend/HandleLidSwitch=ignore/g' /etc/systemd/logind.conf
  sudo systemctl restart systemd-logind
  ```
- [ ] **Step 2-6. 화면 백라이트 완전 소등 및 Headless 디스플레이 설정**:
  - `dietpi-config` → `Display Options` → `Resolution` → `Headless`를 선택하거나 콘솔 절전(`setterm --blank 1`)을 적용해 모니터 출력 전력과 노이즈를 차단합니다 ([DietPi NAA Guide](https://sites.google.com/view/audiostuff/audio-pc/naa/dietpi-naa)).
- [ ] **Step 2-7. 맥북 전용 팬 제어 데몬 설치**:
  - 리눅스 환경에서 맥북 온도에 맞춰 쿨링팬 속도를 정숙하고 안전하게 제어하는 오픈소스 데몬 [mbpfan](https://github.com/linux-on-mac/mbpfan)을 설치합니다.
  ```bash
  sudo apt update && sudo apt install -y mbpfan
  sudo systemctl enable --now mbpfan
  ```
- [ ] **Step 2-8. (선택) 내장 Wi-Fi(`Broadcom BCM4322`) 드라이버 설치 또는 비활성화**:
  - **유선 LAN 전용 운용 시 (권장)**: 오디오 노이즈 감소와 전력 절감을 위해 Wi-Fi를 켜지 않고 그대로 둡니다 ([DietPi NAA Guide](https://sites.google.com/view/audiostuff/audio-pc/naa/dietpi-naa)).
  - **Wi-Fi가 필요한 경우**: 기본 상태에서는 비자유 펌웨어 누락으로 `WiFi : Not Found`가 뜨므로 ([DietPi Community Forum](https://dietpi.com/forum/t/installing-dietpi-on-macbook-pro-2012-as-native-pc-x86-64-does-not-detect-integrated-wifi-adapter/25152)), [Debian Wiki - wl](https://wiki.debian.org/wl) 가이드에 따라 브로드컴 공식 `wl` 드라이버(`broadcom-sta-dkms`)를 설치합니다.
  ```bash
  sudo apt update
  sudo apt install -y linux-headers-amd64 broadcom-sta-dkms wireless-tools
  sudo modprobe -r b44 b43 b43legacy ssb brcmsmac bcma
  sudo modprobe wl
  ```

## Phase 3. CasaOS 그래픽 웹 대시보드 & 광출력 네트워크 DAC 구축

불친절한 텍스트 UI 대신 직관적인 **[CasaOS](https://github.com/icewhaletech/casaos)** 웹 환경을 올리고, 광출력 하이파이 오디오 데몬을 연결하는 핵심 단계입니다.

- [ ] **Step 3-1. CasaOS 원클릭 설치 (웹 그래픽 UI 및 Docker 자동 구성)**:
  - SSH 터미널에 아래 공식 설치 명령어 한 줄만 복사해 붙여넣습니다 ([CasaOS GitHub](https://github.com/icewhaletech/casaos)). Docker 엔진과 그래픽 웹 대시보드가 한 번에 자동 설치됩니다.
  ```bash
  curl -fsSL https://get.casaos.io | sudo bash
  ```
- [ ] **Step 3-2. 스마트폰/PC 웹 브라우저에서 CasaOS 접속 및 파일 공유 설정**:
  - 주력 PC나 스마트폰 브라우저 주소창에 `http://192.168.0.100`(맥북 IP)을 입력해 접속하고 관리자 계정을 생성합니다.
  - 화면의 **`Files(파일)`** 앱 아이콘을 클릭하면 윈도우 탐색기나 맥 Finder처럼 마우스 드래그 앤 드롭으로 맥북 SSD에 음악·문서 파일을 바로 올릴 수 있으며, 폴더 우클릭 → **`Share(공유)`** 버튼만 누르면 복잡한 터미널 설정 없이도 네트워크 파일 공유(Samba)가 즉시 생성됩니다 ([CasaOS GitHub](https://github.com/icewhaletech/casaos)).
- [ ] **Step 3-3. 하이파이 오디오 데몬(MPD & Shairport Sync) 설치**:
  - 맥북의 3.5mm 광출력(Mini-TOSLINK) 단자와 직결되는 오디오 플레이어는 컨테이너보다 네이티브 ALSA 직결이 음질과 지연시간 면에서 가장 유리하므로, 터미널에서 [MPD](https://github.com/musicplayerdaemon/mpd)와 [Shairport Sync](https://github.com/mikebrady/shairport-sync)(AirPlay)를 설치합니다 ([DietPi Software 카탈로그](https://dietpi.com/docs/software/) 번호 `47`(MPD), `37`(Shairport Sync) 또는 `apt` 설치).
  ```bash
  sudo /boot/dietpi/dietpi-software install 47 37
  ```
- [ ] **Step 3-4. 사운드카드 및 3.5mm 광출력(S/PDIF) 음소거 해제**:
  - 맥북 프로 2010의 내장 오디오(`Nvidia MCP89 / Cirrus Logic CS4206`)는 `snd-hda-intel` 드라이버로 즉시 자동 인식됩니다 ([Debian Wiki - MacBookPro7,1](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1)).
  - Mini-TOSLINK 광케이블 사용 시 터미널에서 `alsamixer`를 실행한 뒤, `S/PDIF` 또는 `IEC958` 항목으로 이동해 `M` 키를 눌러 음소거(`MM` → `00`)를 해제합니다(포트 안쪽에서 붉은색 광신호 불빛이 나오는지 확인).
  - 설정 값을 영구 저장합니다:
  ```bash
  sudo alsactl store
  ```
- [ ] **Step 3-5. 스마트폰 AirPlay 및 CasaOS 파일 업로드 테스트**:
  - 아이폰/맥에서 AirPlay 스피커 목록에 `DietPi`가 뜨는지 확인하고 음악을 재생해 외장 DAC에서 소리가 나는지 확인합니다.
  - [CasaOS](https://github.com/icewhaletech/casaos) 웹 화면의 `Files` 앱이나 SMB 공유 폴더를 통해 음악 폴더에 음원 파일이 잘 올라가는지 테스트합니다.

## Phase 4. 원격 전원 제어 환경 구성 (택 1 또는 병행)

평소 전기요금을 아끼면서 필요할 때만 스마트폰으로 맥북을 깨우는 설정입니다.

### 옵션 A. 절전 모드(Suspend to RAM) + Wake-on-LAN (음악 감상용 강력 추천)

- [ ] **Step 4A-1. 네트워크 인터페이스 확인 및 ethtool 설치**:
  ```bash
  sudo apt install -y ethtool
  ip link show
  ```
  유선 랜 인터페이스 이름(예: `eth0` 또는 `enp3s0`)을 메모합니다.
- [ ] **Step 4A-2. 부팅 시 WOL 자동 활성화 등록**:
  - `/etc/network/interfaces` 또는 systemd 서비스에 `ethtool -s [인터페이스명] wol g` 명령을 등록하여 재부팅 후에도 매직 패킷 수신(`Wake-on: g`)이 유지되게 합니다.
- [ ] **Step 4A-3. 절전 진입 및 3초 기상 테스트**:
  - Phase 2의 Step 2-4(`init_on_alloc=0`)가 적용된 상태에서 `sudo systemctl suspend`를 입력해 맥북이 절전 모드(소비전력 1~2W)로 정상 진입하는지 확인합니다 ([Debian Wiki - MacBookPro7,1](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1)).
  - 공유기 관리 앱(ipTIME 등)이나 스마트폰 WOL 앱에서 매직 패킷을 전송해 2~3초 만에 즉시 깨어나 [CasaOS](https://github.com/icewhaletech/casaos) 웹 화면과 오디오가 즉시 복귀하는지 확인합니다.

### 옵션 B. 와이파이 스마트 플러그 + 자동 부팅 (장기 외출/완전 차단용)

- [ ] **Step 4B-1. 안전한 종료 후 전원 차단 규칙 숙지**:
  - [CasaOS](https://github.com/icewhaletech/casaos) 웹 화면 우측 상단 설정 또는 스마트폰 SSH에서 시스템을 먼저 정상 종료(`sudo poweroff`)한 뒤, 30초 후 스마트 플러그 앱에서 전원을 OFF 합니다.
- [ ] **Step 4B-2. 스마트 플러그 ON 자동 부팅 테스트**:
  - 배터리가 제거된 맥북 프로 2010은 MagSafe 전원이 인가되면 전원 버튼을 누르지 않아도 자동으로 켜집니다(Cold Boot). 스마트 플러그를 켜서 정상 부팅 및 [CasaOS](https://github.com/icewhaletech/casaos) 웹 접속이 되는지 확인합니다.

## Phase 5. Gemini AI 음성 비서 구축 및 CasaOS 대시보드 연동

맥북 내장 마이크와 광출력 스피커를 [Google Gen AI Python SDK](https://github.com/googleapis/python-genai) 및 마이크로소프트 [edge-tts](https://github.com/rany2/edge-tts)와 결합하여 나만의 한국어 음성 비서를 완성합니다.

- [ ] **Step 5-1. 무료 API 키 발급**:
  - [Google AI Studio](https://aistudio.google.com/docs/libraries)에 구글 계정으로 로그인하여 Gemini API 키를 발급받고 안전하게 복사해 둡니다.
- [ ] **Step 5-2. 내장 마이크 녹음 감도 점검**:
  - `alsamixer`에서 `F4`(Capture)를 눌러 내장 마이크(`Capture`)가 활성화(`CAPTUR`)되어 있고 게인이 적절한지 확인한 뒤, 아래 명령어로 3초 녹음 테스트를 수행합니다.
  ```bash
  arecord -d 3 -f cd test_mic.wav && aplay test_mic.wav
  ```
- [ ] **Step 5-3. Python 가상환경 및 필수 오픈소스 패키지 설치**:
  - 오디오 입출력 시스템 패키지와 함께 공식 [Google Gen AI Python SDK (`google-genai`)](https://github.com/googleapis/python-genai), 한국어 음성 합성 모듈 [edge-tts](https://github.com/rany2/edge-tts), 로컬 호출어 감지 엔진 [openWakeWord](https://github.com/dscripka/openWakeWord), [MPD](https://github.com/musicplayerdaemon/mpd) 제어용 [python-mpd2](https://github.com/Mic92/python-mpd2)를 설치합니다.
  ```bash
  sudo apt install -y python3-venv python3-pip portaudio19-dev mpv alsa-utils
  mkdir -p /opt/macbook-assistant && cd /opt/macbook-assistant
  python3 -m venv venv
  source venv/bin/activate
  pip install google-genai edge-tts openwakeword sounddevice numpy scipy python-mpd2 requests fastapi uvicorn psutil
  ```
- [ ] **Step 5-4. 음성 비서 및 웹 리모컨 스크립트 배포**:
  - 함께 제공된 세 번째 문서(`03_OpenSource_Program_Prompts.md`)의 **프롬프트 1(`assistant.py`)**과 **프롬프트 2(`web_remote.py`)**를 생성하고 `systemd` 서비스로 등록합니다.
- [ ] **Step 5-5. CasaOS 홈 화면에 '맥북 오디오 & 절전 리모컨' 아이콘 등록**:
  - [CasaOS](https://github.com/icewhaletech/casaos) 웹 화면에서 `+` 버튼 → **`Add External Link (외부 링크 추가)`**를 눌러 `http://192.168.0.100:8080`(`web_remote.py` 주소)을 아이콘으로 등록하면, [CasaOS](https://github.com/icewhaletech/casaos) 메인 화면에서 모든 기능을 원클릭으로 제어할 수 있습니다.

## 장애 대응 및 드라이버 트러블슈팅 빠른 점검표

| 증상 | 주요 원인 | 즉시 해결 행동 |
| :--- | :--- | :--- |
| `dietpi-config`에서 `WiFi : Not Found`가 뜸 | 브로드컴 `BCM4322` 독점 펌웨어가 기본 이미지에 미포함됨 ([DietPi Community Forum](https://dietpi.com/forum/t/installing-dietpi-on-macbook-pro-2012-as-native-pc-x86-64-does-not-detect-integrated-wifi-adapter/25152)) | 유선 LAN 사용 시 무시(권장). Wi-Fi 필요 시 `sudo apt install broadcom-sta-dkms` 후 `modprobe wl` 실행 ([Debian Wiki - wl](https://wiki.debian.org/wl)) |
| `systemctl suspend` 실행 시 멈추거나 깨어나지 못함 | GeForce 320M `nouveau` 드라이버와 커널 메모리 초기화 옵션 충돌 ([Debian Wiki - MacBookPro7,1](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1)) | `/etc/default/grub`에 `init_on_alloc=0 intel_iommu=off` 추가 후 `sudo update-grub` 및 재부팅 |
| DietPi USB가 맥북 EFI 부팅 화면에서 멈춤 | 일부 구형 맥의 비표준 EFI 펌웨어 호환성 이슈 ([DietPi GitHub Issue #1755](https://github.com/MichaIng/DietPi/issues/1755)) | 순정 데비안 `netinst` ISO로 설치 후 [PREP_SYSTEM_FOR_DIETPI.sh](https://github.com/MichaIng/DietPi/blob/master/PREP_SYSTEM_FOR_DIETPI.sh) 실행 |
| 광케이블을 꽂았는데 DAC에 소리가 안 남 | ALSA에서 S/PDIF 디지털 출력이 기본 음소거(`MM`) 상태임 | `alsamixer` 실행 → `S/PDIF` 항목에서 `M` 키 눌러 `00`으로 변경 후 `sudo alsactl store` |
| 상판을 닫았더니 CasaOS 접속과 음악이 끊김 | `systemd-logind`가 상판 닫힘을 절전 명령으로 처리함 | `/etc/systemd/logind.conf`에서 `HandleLidSwitch=ignore` 설정 후 데몬 재시작 |
| 완전 종료(`poweroff`) 후 WOL로 안 켜짐 | 구형 맥북은 완전 종료(S5) 상태에서 LAN WOL을 지원하지 않음 | 평상시에는 `sudo systemctl suspend`(S3 절전)를 사용하거나 스마트 플러그 재인가로 부팅 |
| 음성 비서가 내 목소리를 못 알아들음 | 상판이 완전히 닫혀 내장 마이크가 차폐되었거나 Capture 볼륨이 낮음 | 상판 틈을 5mm가량 살짝 띄우는 고무 범퍼를 붙이거나 `alsamixer` Capture 볼륨 상향 |
