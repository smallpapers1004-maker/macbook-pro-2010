# 구형 맥북 프로 2010 하드웨어 잠재력 200% 활용 확장 제안서 (DietPi + CasaOS 에디션)

초경량 [DietPi](https://github.com/MichaIng/DietPi) 위에 오픈소스 개인 클라우드 웹 대시보드인 **[CasaOS](https://github.com/icewhaletech/casaos)**를 얹으면, 터미널 명령어를 일일이 입력할 필요 없이 **웹 브라우저의 앱스토어(App Store)에서 스마트폰 앱을 깔듯 클릭 한 번으로 다양한 서버 기능을 확장**할 수 있습니다 ([CasaOS on DietPi 구축 사례](https://github.com/IceWhaleTech/CasaOS/discussions/1948)).

기본 구성([DietPi](https://github.com/MichaIng/DietPi) + [CasaOS](https://github.com/icewhaletech/casaos) + 광출력 네트워크 DAC + Gemini 음성 비서)이 차지하는 메모리는 **약 300MB~400MB 수준**에 불과합니다. 맥북 프로 2010형이 가진 **4GB~8GB RAM, x86_64 호환성, 내장 iSight 웹캠, 내장 마이크, 기가비트 유선 랜, 3.5mm 광출력, 알루미늄 유니바디 방열 구조**를 활용해, CPU 클럭이 800MHz~1.2GHz로 제한된 상태에서도 **거뜬히 동시에 돌아가는 7가지 맞춤형 확장 아이디어**를 제안합니다.

## 1. 맥북 프로 2010 + CasaOS 조합의 시너지

일반적인 저가형 미니 PC나 라즈베리 파이와 달리, 구형 맥북 프로 2010에 [CasaOS](https://github.com/icewhaletech/casaos)를 올리면 하드웨어와 소프트웨어의 장점이 결합됩니다.

| 내장 자산 / 플랫폼 | 일반 미니 PC / 순정 CLI 대비 강점 | 추천 확장 활용 분야 |
| :--- | :--- | :--- |
| **[CasaOS](https://github.com/icewhaletech/casaos) 웹 그래픽 대시보드** | 맥북 모니터는 꺼서 전력을 아끼고, 내 폰/PC에서는 아이패드 같은 그래픽 UI로 관리 | 웹 파일 탐색기, 드래그 앤 드롭 업로드, 원클릭 Docker 앱스토어 |
| **x86_64 아키텍처 + 4~8GB RAM** | ARM 호환성 문제 없이 [CasaOS](https://github.com/icewhaletech/casaos) 앱스토어의 모든 표준 앱 즉시 구동 | [AdGuard Home](https://github.com/AdguardTeam/Adguardhome), [Vaultwarden](https://github.com/dani-garcia/vaultwarden) 등 다중 경량 서비스 동시 구동 |
| **내장 iSight 카메라 & 마이크** | 별도 USB 웹캠이나 마이크 구매 없이 영상·음성 입력 즉시 가능 | [go2rtc](https://github.com/AlexxIT/go2rtc) 기반 초저지연 반려동물/현관 홈캠 및 양방향 인터콤 |
| **3.5mm Mini-TOSLINK 광출력** | 컴퓨터 내부 전기 노이즈를 100% 절연하는 순수 디지털 광신호 전송 | [Navidrome](https://github.com/navidrome/navidrome) + [MPD](https://github.com/musicplayerdaemon/mpd) + [Shairport Sync](https://github.com/mikebrady/shairport-sync) 하이파이 오디오 센터 |
| **기가비트 유선 LAN + 내장 화면** | 유선 LAN으로 안정적 통신 및 WOL 지원, 필요 시 상판만 열면 보조 모니터로 변신 | [Tailscale](https://github.com/tailscale/tailscale) 외부 접속 VPN 게이트웨이 & [UpSnap](https://github.com/seriousm4x/upsnap) 집안 PC 전원 허브 |

## 2. CasaOS 원클릭 추천 확장 시나리오 7선

### 제안 1. 나만의 프라이빗 Spotify 스트리밍 서버 ([Navidrome](https://github.com/navidrome/navidrome))
* **구상 배경**: 기본 가이드의 [MPD](https://github.com/musicplayerdaemon/mpd)와 [Shairport Sync](https://github.com/mikebrady/shairport-sync)는 집 안에서 거실 DAC로 음악을 듣기에 좋지만, 외출했을 때는 집 맥북의 SATA SSD에 담긴 음원을 들을 수 없습니다.
* **핵심 가치**: 초경량 오픈소스 음악 서버 [Navidrome](https://github.com/navidrome/navidrome)을 설치하면, [CasaOS](https://github.com/icewhaletech/casaos)의 `Files` 앱으로 업로드한 음악 폴더를 자동 스캔하여 웹 브라우저와 스마트폰 앱(Subsonic 호환 앱: Symfonium, Play:Sub, Amperfy 등)에서 **나만의 Spotify처럼 언제 어디서나 스트리밍**할 수 있습니다.
* **설치 방법**: [CasaOS](https://github.com/icewhaletech/casaos) 앱스토어(또는 [DietPi Software](https://dietpi.com/docs/software/))에서 아이콘 클릭 한 번으로 설치.
* **리소스 점유**: 아이들 RAM 약 30~50MB, CPU 점유율 1% 미만.

### 제안 2. 집안 전체 기기 광고·추적기 차단 DNS 서버 ([AdGuard Home](https://github.com/AdguardTeam/Adguardhome))
* **구상 배경**: 맥북이 공유기에 유선 LAN으로 연결되어 상시(또는 재택 시간 동안) 켜져 있다면, 집안 네트워크의 문지기 역할을 맡기기에 최적입니다.
* **핵심 가치**: [CasaOS](https://github.com/icewhaletech/casaos) 기본 앱스토어 인기 1위 앱인 [AdGuard Home](https://github.com/AdguardTeam/Adguardhome)을 원클릭 설치하고 공유기의 기본 DNS를 맥북 IP로 지정하면, 별도 광고 차단 앱을 깔기 어려운 **스마트 TV, 아이폰, 태블릿, 가족 스마트폰의 웹 배너 광고와 개인정보 추적 도메인을 한 번에 차단**해 줍니다.
* **설치 방법**: [CasaOS](https://github.com/icewhaletech/casaos) 앱스토어에서 `AdGuard Home` 클릭 설치.
* **리소스 점유**: RAM 약 40~60MB, CPU 점유율 1% 미만.

### 제안 3. 내장 iSight 웹캠을 활용한 초저지연 홈캠 & 반려동물 모니터 ([go2rtc](https://github.com/AlexxIT/go2rtc))
* **구상 배경**: 맥북 상단 베젤에 기본 내장된 iSight 카메라(`[uvcvideo]` 기본 드라이버로 자동 인식, [Debian Wiki - MacBookPro5,1](https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/5-1))를 상판만 덮어두어 놀리는 것은 아깝습니다. 맥북을 세로 거치대에 꽂아 상판을 살짝 열어두면 훌륭한 IP 카메라가 됩니다.
* **핵심 가치**: Go 기반 초경량 스트리밍 엔진 [go2rtc](https://github.com/AlexxIT/go2rtc)를 [CasaOS](https://github.com/icewhaletech/casaos)에 올리고 비디오 장치(`/dev/video0`)를 연결하면, **CPU 점유율 거의 없이 WebRTC, RTSP, HLS, 심지어 아이폰 애플 홈(HomeKit)으로 실시간 중계**할 수 있습니다.
* **AI 결합 아이디어**: 현관문 소리나 움직임이 감지되었을 때 웹캠 스냅샷 1장을 찍어 [Google Gen AI SDK](https://github.com/googleapis/python-genai)로 전송한 뒤, *"택배 기사님이 문 앞에 상자를 두고 가셨습니다"*라고 텔레그램 알림을 보내는 지능형 AI 비전 홈캠으로 진화시킬 수 있습니다.
* **리소스 점유**: RAM 약 20~30MB.

### 제안 4. 포트포워딩 없는 외부 접속 VPN ([Tailscale](https://github.com/tailscale/tailscale)) & 집안 PC 원격 부팅 허브 ([UpSnap](https://github.com/seriousm4x/upsnap))
* **구상 배경**: 외부에서 집에 있는 맥북 [CasaOS](https://github.com/icewhaletech/casaos) 대시보드나 파일 서버에 접속하려고 공유기 포트포워딩을 열면 보안 위험이 커집니다. 또한 고성능 데스크톱 PC를 하루 종일 켜두기는 전기요금이 부담스럽습니다.
* **핵심 가치**:
  1. 제로 컨피그 WireGuard 메시 VPN인 [Tailscale](https://github.com/tailscale/tailscale)을 설치하면 공유기 설정을 건드리지 않고도 밖에서 집 맥북의 [CasaOS](https://github.com/icewhaletech/casaos) 화면에 내부망처럼 안전하게 접속할 수 있습니다.
  2. 여기에 초경량 Wake-on-LAN 웹 앱인 [UpSnap](https://github.com/seriousm4x/upsnap)을 [CasaOS](https://github.com/icewhaletech/casaos) 컨테이너로 띄워두면, **15W짜리 저전력 맥북이 '집안의 전원 스위치 허브'가 되어 외부에서 스마트폰으로 500W짜리 메인 데스크톱 PC를 필요할 때만 켜고 원격 작업을 수행**할 수 있습니다.
* **리소스 점유**: [Tailscale](https://github.com/tailscale/tailscale) + [UpSnap](https://github.com/seriousm4x/upsnap) 합산 RAM 약 55MB.

### 제안 5. 무설치 P2P 자동 백업 ([Syncthing](https://github.com/syncthing/syncthing)) & 초경량 비밀번호 금고 ([Vaultwarden](https://github.com/dani-garcia/vaultwarden))
* **구상 배경**: 무거운 클라우드 스토리지(Nextcloud 등)는 구형 CPU에 부담을 주지만, [CasaOS](https://github.com/icewhaletech/casaos) 기본 파일 관리자에 특화된 경량 오픈소스 2종을 더하면 속도와 안정성을 모두 잡을 수 있습니다.
* **핵심 가치**:
  1. 실시간 P2P 파일 동기화 도구 [Syncthing](https://github.com/syncthing/syncthing)을 [CasaOS](https://github.com/icewhaletech/casaos) 앱스토어에서 설치하면, 스마트폰으로 찍은 사진이나 주력 노트북의 작업 문서 폴더가 집 와이파이에 연결되는 순간 맥북 SATA SSD로 자동 백업됩니다.
  2. Rust 언어로 작성된 Bitwarden 호환 서버 [Vaultwarden](https://github.com/dani-garcia/vaultwarden)을 함께 구동하면, 유료 구독 없이도 모든 기기에서 비밀번호와 OTP를 안전하게 자동 완성할 수 있습니다.
* **리소스 점유**: [Syncthing](https://github.com/syncthing/syncthing) RAM 약 40MB + [Vaultwarden](https://github.com/dani-garcia/vaultwarden) RAM 약 25MB.

### 제안 6. 24시간 서비스 감시 ([Uptime Kuma](https://github.com/louislam/uptime-kuma)) & AI 연동 시스템 모니터 ([Glances](https://github.com/nicolargo/glances))
* **구상 배경**: [CasaOS](https://github.com/icewhaletech/casaos) 기본 위젯에서도 CPU와 RAM 상태를 예쁘게 보여주지만, 장애 알림과 AI 음성 브리핑을 연동하면 관리가 한결 편해집니다.
* **핵심 가치**:
  1. [CasaOS](https://github.com/icewhaletech/casaos) 앱스토어에서 [Uptime Kuma](https://github.com/louislam/uptime-kuma)를 원클릭 설치하면 내 블로그, 회사 서버, 집 공유기, 스마트 기기들의 생존 여부를 1분 단위로 체크하여 장애 발생 시 텔레그램/디스코드로 즉시 알림을 줍니다.
  2. 시스템 모니터링 도구 [Glances](https://github.com/nicolargo/glances)를 연동하면 맥북의 CPU 클럭, 온도, 쿨링팬 RPM을 API로 조회할 수 있어, Gemini 음성 비서에게 *"지금 맥북 서버 온도랑 상태 어때?"*라고 물어보면 음성으로 브리핑해 줍니다.
* **리소스 점유**: 합산 RAM 약 100~130MB.

### 제안 7. 거실 오디오 비주얼라이저 & 나우 플레잉(Now Playing) 스마트 액자
* **구상 배경**: 맥북을 오디오 랙이나 거실 선반 위에 두고 쓴다면, 알루미늄 유니바디 감성과 13인치/15인치 디스플레이를 인테리어 소품으로 활용할 수 있습니다.
* **핵심 가치**: [Shairport Sync](https://github.com/mikebrady/shairport-sync)와 [MPD](https://github.com/musicplayerdaemon/mpd) 상태 정보를 연동하여, **음악이 재생될 때만 화면 백라이트가 자동으로 켜지면서 현재 재생 중인 곡의 앨범 커버, 가사, 스펙트럼 비주얼라이저, 현재 날씨를 전체 화면으로 띄워주는 '스마트 오디오 액자'**로 만듭니다. 음악이 멈추면 1분 뒤 자동으로 화면 백라이트를 완전히 꺼서 소비전력을 다시 15W 수준으로 낮춥니다.
* **리소스 점유**: 경량 프레임버퍼/웹 키오스크 구동 시 RAM 약 80~150MB.

## 3. DietPi + CasaOS 기반 리소스 및 전력 영향 비교표

맥북 프로 2010 기본 사양(RAM 4GB 기준, 배터리 탈거 시 CPU 800MHz~1.2GHz)에서 [CasaOS](https://github.com/icewhaletech/casaos)와 모든 확장 서비스를 동시에 켰을 때의 리소스 계산표입니다.

| 구성 항목 | 핵심 오픈소스 | 예상 RAM 사용량 | 평균 CPU 부하 | 추가 전력 소모 |
| :--- | :--- | ---: | ---: | :--- |
| **기반 OS + 웹 그래픽 UI + 오디오** | [DietPi](https://github.com/MichaIng/DietPi) + **[CasaOS](https://github.com/icewhaletech/casaos)** + [MPD](https://github.com/musicplayerdaemon/mpd) + [Shairport Sync](https://github.com/mikebrady/shairport-sync) | 약 260 MB | 1~3% | 기준점 (15~18W) |
| **AI 한국어 음성 비서 데몬** | [openWakeWord](https://github.com/dscripka/openWakeWord) + [Google Gen AI SDK](https://github.com/googleapis/python-genai) + [edge-tts](https://github.com/rany2/edge-tts) | 약 120 MB | 3~6% | +0.5W 미만 |
| **제안 1. 개인 음원 스트리밍** | [Navidrome](https://github.com/navidrome/navidrome) | 약 45 MB | 1% 미만 | 무시 가능 |
| **제안 2. 네트워크 광고 차단 DNS** | [AdGuard Home](https://github.com/AdguardTeam/Adguardhome) | 약 50 MB | 1% 미만 | 무시 가능 |
| **제안 3. 내장 웹캠 스트리머** | [go2rtc](https://github.com/AlexxIT/go2rtc) | 약 25 MB | 2~5% (시청 시) | 시청 시 +1~2W |
| **제안 4. 외부 VPN & WOL 허브** | [Tailscale](https://github.com/tailscale/tailscale) + [UpSnap](https://github.com/seriousm4x/upsnap) | 약 55 MB | 1% 미만 | 무시 가능 |
| **제안 5. 파일 동기화 & 암호 금고** | [Syncthing](https://github.com/syncthing/syncthing) + [Vaultwarden](https://github.com/dani-garcia/vaultwarden) | 약 65 MB | 1~2% | 동기화 시 일시 증가 |
| **제안 6. 서버 상태/장애 감시** | [Uptime Kuma](https://github.com/louislam/uptime-kuma) + [Glances](https://github.com/nicolargo/glances) | 약 120 MB | 1~2% | 무시 가능 |
| **총합 (전체 동시 구동 시)** | **CasaOS 올인원 만능 홈 서버** | **약 740 MB / 4,096 MB** | **아이들 10~15% 내외** | **총 16W~20W 유지** |

> **핵심 인사이트**: [CasaOS](https://github.com/icewhaletech/casaos)의 직관적인 그래픽 웹 UI를 올리고 위 7가지 제안을 **전부 동시에 돌려도 전체 4GB RAM의 18% 수준(약 740MB)만 사용**합니다. 맥북 본체 모니터는 꺼진 상태(Headless)로 돌아가므로 데스크톱 OS를 깔았을 때보다 전력과 발열은 아끼면서, 내 스마트폰과 PC에서는 최고로 편리한 그래픽 환경을 누릴 수 있습니다.

## 4. CasaOS 앱스토어 기반 단계별 도입 추천 코스

1. **1주차 (CasaOS 웹 파일 서버 & 하이파이 오디오 완성 코스)**:
   - [DietPi](https://github.com/MichaIng/DietPi) 위에 **[CasaOS](https://github.com/icewhaletech/casaos)**를 설치하고, 네이티브 광출력 오디오([MPD](https://github.com/musicplayerdaemon/mpd) + [Shairport Sync](https://github.com/mikebrady/shairport-sync))에 **[Navidrome](https://github.com/navidrome/navidrome)(제안 1)**과 **[Syncthing](https://github.com/syncthing/syncthing)(제안 5)**을 앱스토어에서 클릭해 설치합니다.
   - 이제 스마트폰이나 PC 브라우저에서 [CasaOS](https://github.com/icewhaletech/casaos) `Files` 앱으로 음원과 사진을 드래그 앤 드롭으로 올리고, 집에서는 광출력 DAC로, 밖에서는 앱으로 음악을 듣습니다.
2. **2주차 (스마트홈 광고 차단 & 원격 전원 허브 코스)**:
   - [CasaOS](https://github.com/icewhaletech/casaos) 앱스토어에서 **[AdGuard Home](https://github.com/AdguardTeam/Adguardhome)(제안 2)**과 **[UpSnap](https://github.com/seriousm4x/upsnap) + [Tailscale](https://github.com/tailscale/tailscale)(제안 4)**을 클릭해 추가합니다.
   - 집안 와이파이 광고가 사라지고, 외부에서도 안전하게 내 [CasaOS](https://github.com/icewhaletech/casaos) 대시보드에 접속해 집 데스크톱 PC까지 원격으로 켜고 끄는 인프라가 완성됩니다.
3. **3주차 (AI 음성 비서 & 내장 웹캠 융합 코스)**:
   - Gemini 음성 비서(`assistant.py`)와 웹 리모컨(`web_remote.py`)을 구동해 [CasaOS](https://github.com/icewhaletech/casaos) 홈 화면에 커스텀 앱 아이콘으로 등록하고, 필요시 **[go2rtc](https://github.com/AlexxIT/go2rtc)(제안 3)**로 내장 웹캠까지 연동합니다.
