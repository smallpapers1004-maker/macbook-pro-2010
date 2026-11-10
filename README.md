# 맥북 2010 살리기

2010년형 맥북 프로를 DietPi와 CasaOS 기반 홈 서버로 재활용하는 프로젝트입니다. 초기 설치, MPD 음악 재생, 한국어 음성 비서, 웹 리모컨, 자동 절전과 CPU 클럭 관리 파일을 담았습니다.

| 구성 | 파일 |
| --- | --- |
| 설치 전 준비와 실행 순서 | [실행 체크리스트](docs/01_Macbook_2010_Action_Checklist.md) |
| 음악 스트리밍과 기타 CasaOS 앱 | [확장 제안서](docs/02_Macbook_2010_Expansion_Proposals.md) |
| DietPi 서버 초기 설정 | [setup_macbook_server.sh](setup_macbook_server.sh) |
| CasaOS 웹 리모컨 | [web_remote.py](web_remote.py) · [설치 안내](INSTALL.ko.md) |
| 한국어 음성 비서 | [소스 및 설치 안내](macbook-assistant/README.md) · [ZIP](macbook-assistant.zip) |
| 자동 절전 가드 | [auto_sleep_guard.py](auto_sleep_guard.py) · [설치 안내](auto-sleep-guard-dietpi/INSTALL.ko.md) · [ZIP](auto-sleep-guard-dietpi.zip) |
| CPU 클럭 관리 | [smc_boost_manager.py](smc_boost_manager.py) |

초기 설치 스크립트는 같은 디렉터리의 `macbook-assistant.zip`과 `web_remote.py`를 찾아 배치합니다. 저장소를 내려받은 뒤 최상위 디렉터리에서 `bash setup_macbook_server.sh --help`로 설치 옵션을 확인할 수 있습니다.

실행 체크리스트에 나오는 음성 비서와 웹 리모컨은 위 표에서 소스와 설치 안내를 확인할 수 있습니다.
