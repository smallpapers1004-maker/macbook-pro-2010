# 맥북 한국어 음성 비서

DietPi x86_64에서 실행하는 systemd 데몬입니다. 기본 호출어는 **“Hey Jarvis”**입니다. 비프음이 끝나면 한국어로 요청하세요. 대기 중에는 내장 마이크의 16kHz 모노 입력을 openWakeWord와 Silero VAD가 로컬에서 확인합니다. 호출어 이후의 발화만 WAV로 Gemini에 전송합니다. 답변 텍스트는 Edge TTS로 전송해 한국어 MP3를 만들고, 도착한 오디오부터 mpv로 재생합니다. 오디오 파일은 디스크에 저장하지 않습니다. 메모를 읽어 달라고 하면 최근 메모 내용이 Gemini에 전달됩니다.

## 1. 파일과 모델 설치

DietPi의 Python 3.11~3.13을 대상으로 합니다. 다음 명령은 압축을 푼 `macbook-assistant` 폴더 안에서 실행합니다. root로 접속했다면 `sudo`를 생략하세요.

```bash
sudo apt update
sudo apt install python3 python3-venv libportaudio2 alsa-utils mpv lm-sensors
# MPD를 아직 설치하지 않은 경우
sudo apt install mpd mpc

id macbook-assistant >/dev/null 2>&1 || sudo useradd --system --user-group \
    --groups audio --home-dir /opt/macbook-assistant --shell /usr/sbin/nologin macbook-assistant
sudo install -d -m 0755 /opt/macbook-assistant
sudo install -m 0644 assistant.py voice_tools.py youtube_music.py requirements.txt /opt/macbook-assistant/
sudo test -e /opt/macbook-assistant/.env || sudo install -m 0640 \
    -o root -g macbook-assistant .env.example /opt/macbook-assistant/.env
sudoedit /opt/macbook-assistant/.env

sudo python3 -m venv /opt/macbook-assistant/.venv
sudo /opt/macbook-assistant/.venv/bin/python -m pip install -r /opt/macbook-assistant/requirements.txt
sudo /opt/macbook-assistant/.venv/bin/python -m pip install --no-deps openwakeword==0.6.0
sudo env OPENBLAS_NUM_THREADS=1 /opt/macbook-assistant/.venv/bin/python \
    /opt/macbook-assistant/assistant.py --download-models
```

`.env`에 `GEMINI_API_KEY`를 설정하세요. `GEMINI_MODEL` 기본값은 요청에 맞춰 `gemini-2.5-flash`입니다. Google은 이 모델에 대한 신규 사용을 제한한다고 안내합니다. 새 키에서 모델 오류가 발생하면 오디오 입력과 함수 호출을 지원하는 `gemini-3.5-flash-lite`처럼 계정에서 사용할 수 있는 모델 ID로 바꾸세요.

openWakeWord 0.6.0의 Linux 패키지 메타데이터는 TFLite도 요구하지만 이 앱은 ONNX만 사용합니다. 위 두 pip 명령은 ONNX에 필요한 의존성을 설치한 뒤 openWakeWord를 추가합니다. `--download-models`는 공식 릴리스의 ONNX 자산만 내려받고 간단한 추론을 실행합니다. 기본 호출어 대신 커스텀 모델을 쓰려면 `WAKE_WORD_MODEL`에 openWakeWord용 `.onnx` 파일의 절대 경로를 적으세요. 시스템 서비스에서 읽을 수 있도록 파일을 `/opt/macbook-assistant/` 아래에 두고 모델 명령을 다시 실행하세요.

## 2. 광출력과 마이크 설정

```bash
aplay -l
arecord -l
aplay -L
```

`asound.conf.example`의 출력 주소 `hw:0,1`을 실제 S/PDIF 카드와 장치로, 입력 주소 `hw:0,0`을 내장 마이크 캡처 주소로 바꿉니다. 카드 번호가 재부팅 때 달라지면 `hw:CARD=카드ID,DEV=장치번호` 형식을 사용하세요. 기존 ALSA 설정이 있으면 예제의 PCM 정의를 `/etc/asound.conf`에 합치세요. S/PDIF 스위치가 음소거 상태라면 `alsamixer`에서 해당 카드의 IEC958 스위치를 켜세요.

`/etc/mpd.conf`에서 광출력에 사용할 `audio_output` 블록을 아래처럼 설정합니다. MPD와 비서가 모두 `spdif_shared`를 써야 음악 재생 중에도 비프음과 답변이 같은 DAC에 출력됩니다.

```text
audio_output {
    type        "alsa"
    name        "Shared S/PDIF"
    device      "spdif_shared"
    format      "48000:16:2"
    mixer_type  "software"
}
```

```bash
sudo systemctl restart mpd
mpc status
sudo -u macbook-assistant /opt/macbook-assistant/.venv/bin/python \
    /opt/macbook-assistant/assistant.py --list-devices
sudo -u macbook-assistant /opt/macbook-assistant/.venv/bin/python \
    /opt/macbook-assistant/assistant.py --play-test
```

`--list-devices`에 표시된 `assistant_mic` 또는 16kHz 모노를 지원하는 입력의 번호를 `.env`의 `AUDIO_DEVICE_INDEX`에 적고, 다시 실행해 입력 설정 검사를 통과하는지 확인하세요. 음악을 재생한 상태에서도 `--play-test`의 비프음이 들려야 두 프로세스가 광출력을 함께 쓸 수 있습니다.

`VOLUME_BACKEND=auto`는 ALSA의 `Master`와 `Headphone`을 모두 조절하고, 둘 다 실패하면 MPD 볼륨을 변경합니다. 광출력 음량에 ALSA 믹서가 영향을 주지 않는 장치라면 `VOLUME_BACKEND=mpd`로 설정하세요. 답변 음량은 `TTS_VOLUME`으로 따로 조절합니다. 답변 재생 중 MPD 볼륨은 기존 값의 70%로 낮아지고, 재생 종료 후 복구됩니다. 그 사이 MPD 볼륨을 직접 조정한 경우 새 값을 유지합니다.

## 3. 서비스 시작

다음 링크로 메모를 요청한 경로에서 열 수 있게 하고, 실제 쓰기는 systemd가 관리하는 전용 상태 디렉터리에 맡깁니다. 기존 메모 파일이 있다면 먼저 백업한 후 전용 상태 디렉터리로 이동하고 서비스 계정에 소유권을 부여하세요.

```bash
sudo test -e /opt/macbook-assistant/memo.json || \
    sudo test -L /opt/macbook-assistant/memo.json || \
    sudo ln -s /var/lib/macbook-assistant/memo.json /opt/macbook-assistant/memo.json
sudo install -m 0644 macbook-assistant.service /etc/systemd/system/macbook-assistant.service
sudo systemctl daemon-reload
sudo systemctl enable --now macbook-assistant.service
sudo journalctl -u macbook-assistant.service -f
```

설정을 바꾼 뒤에는 `sudo systemctl restart macbook-assistant.service`를 실행하세요. 노트북 덮개를 닫아도 계속 구동해야 하는 경우에는 DietPi의 절전 설정도 확인하세요.

## 4. CasaOS 연동

### 음악 폴더

`/etc/mpd.conf`의 `music_directory`가 `/DATA/Media/Music`을 가리키거나 해당 폴더에 연결되어 있어야 합니다. MPD 서비스 계정에는 음원 읽기와 상위 디렉터리 접근 권한이 필요합니다. MPD 설정을 바꿨다면 `sudo systemctl restart mpd`를 실행하세요.

CasaOS **Files**에서 업로드를 마친 뒤 “음악 틀어 줘” 또는 “이 곡 찾아 틀어 줘”라고 말하면 MPD 라이브러리를 갱신하고 스캔이 끝난 뒤 재생이나 검색을 진행합니다. 일반 재생은 기존 대기열을 사용하고, 대기열이 비어 있으면 음악 폴더의 음원을 넣습니다. 새로 올린 특정 곡을 바로 들으려면 곡명을 말하세요. 큰 라이브러리에서 약 10초 안에 스캔이 끝나지 않으면 비서가 잠시 뒤 다시 요청해 달라고 안내합니다.

### Docker 상태 브리핑

CasaOS와 로컬 Docker가 설치된 서버에서 아래를 실행하세요. 기존 설치라면 다음 절의 업데이트 명령으로 Python 파일과 의존성을 먼저 갱신하세요. 압축을 푼 폴더에서 실행하세요.

```bash
sudo install -m 0644 macbook-assistant-docker.socket macbook-assistant-docker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now macbook-assistant-docker.socket
sudo systemctl restart macbook-assistant.service
```

비서가 서버 상태를 조회할 때만 로컬 소켓이 고정된 `docker ps` 명령을 실행합니다. 비서 계정은 조회 결과만 받고, `docker` 그룹에 가입할 필요가 없습니다. 결과에는 같은 Docker 호스트에 있는 다른 앱의 컨테이너도 포함됩니다. 서버 상태를 물으면 컨테이너 이름과 상태가 Gemini에 전달됩니다.

오디오 없이 브리핑 경로를 점검하려면 다음 명령을 실행하세요.

```bash
sudo -u macbook-assistant env PYTHONPATH=/opt/macbook-assistant \
    /opt/macbook-assistant/.venv/bin/python -c 'from voice_tools import docker_status; print(docker_status())'
```

## 5. 유튜브 음악과 3.5mm 아날로그 출력

### 기존 설치 업데이트

압축을 푼 폴더에서 아래 명령을 실행하세요.

```bash
sudo install -m 0644 assistant.py voice_tools.py youtube_music.py requirements.txt /opt/macbook-assistant/
sudo /opt/macbook-assistant/.venv/bin/python -m pip install -r /opt/macbook-assistant/requirements.txt
sudo systemctl restart macbook-assistant.service
```

### 유튜브 검색과 보관함

yt-dlp가 유튜브 재생 정보를 해석할 때 사용할 JavaScript 런타임이 필요합니다. 이 앱은 기본으로 Deno를 사용합니다. 공식 yt-dlp 안내에서 요구하는 Deno 2.3 이상을 시스템 PATH에 설치하거나, Node.js 22 이상을 설치하고 `.env`에 `YTDLP_JS_RUNTIME=node`를 설정하세요. 바이너리를 별도 경로에 두었다면 `YTDLP_JS_RUNTIME=node:/usr/local/bin/node`처럼 적고 서비스 계정에서 실행되는지 확인하세요. `requirements.txt`가 yt-dlp의 EJS 구성요소도 설치합니다.

공개 검색은 유튜브 로그인 없이 사용할 수 있습니다. 내 좋아요와 재생목록을 쓰려면 [ytmusicapi 브라우저 인증 안내](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html)에 따라 본인 계정의 `browser.json`을 만든 뒤 맥북에 복사하세요.

```bash
sudo install -m 0640 -o root -g macbook-assistant browser.json /opt/macbook-assistant/browser.json
```

`browser.json`에는 로그인 세션 정보가 들어 있습니다. 파일을 공개 저장소나 채팅에 올리지 마세요. 비서는 보관함을 조회할 때만 이 파일을 읽으므로 교체 후 바로 다음 요청에 반영됩니다. 인증 파일은 보관함 조회에 사용되며, 각 곡은 일반 유튜브 재생 URL로 열립니다. 로그인이 필요한 곡은 보관함에 있더라도 재생되지 않을 수 있습니다.

유튜브 음악은 호출어 감지부터 답변 종료까지 볼륨 30%로 재생되고 이후 100%로 돌아갑니다. 보관함은 한 번에 최대 100곡을 대기열에 넣고 기본으로 섞습니다. 목록 순서대로 들으려면 음성 요청에 “순서대로”를 붙이세요. 유튜브 일시정지·재개·이전·다음·종료는 공개 검색과 보관함 재생 모두에 적용됩니다.

### 3.5mm 헤드폰 단자와 RCA 연결

3.5mm–RCA 케이블을 사용할 경우 `aplay -l`에서 아날로그 재생 장치를 확인하세요. `asound.conf.example`의 `analog_mix` 장치 주소를 맞춰 `/etc/asound.conf`에 적용하고, `.env`에서 `ALSA_PCM=analog_shared`와 `VOLUME_BACKEND=alsa`를 설정하세요. MPD도 이 단자로 내보내려면 `mpd.conf`의 `device`를 `analog_shared`로 지정하고 MPD와 비서 서비스를 재시작하세요.

볼륨을 지정하면 `Master`와 `Headphone` 각각에 같은 퍼센트와 `unmute`를 적용합니다. 믹서가 다른 카드에 있으면 `amixer -c 카드번호 scontrols`로 확인하고 `.env`에 `ALSA_CONTROL_DEVICE=hw:카드번호`를 설정하세요.

## 6. 사용과 점검

| 비프음 뒤 말할 문장 | 동작 |
|---|---|
| “음악 틀어 줘” | 음악 폴더를 스캔한 뒤 대기열에서 재생. 비어 있으면 라이브러리에서 음원을 추가 |
| “아이유 노래 찾아 틀어 줘” | 음악 폴더를 스캔한 뒤 검색해 일치하는 첫 곡을 재생 |
| “음악 일시 정지해 줘” / “지금 무슨 곡이야?” | 일시 정지 또는 재생 정보 확인 |
| “유튜브에서 아이유 음악 틀어 줘” | 유튜브 검색 결과 재생 |
| “유튜브 뮤직 내 재생목록 읽어 줘” | 내 재생목록 이름을 최대 25개 읽음 |
| “유튜브 뮤직 내 좋아요 틀어 줘” | 내 좋아요 트랙을 대기열에 넣어 재생 |
| “유튜브 뮤직 내 출근길 목록 순서대로 틀어 줘” | 지정한 재생목록을 목록 순서로 재생 |
| “볼륨 40퍼센트로 맞춰 줘” | 설정된 ALSA Master·Headphone 또는 MPD 볼륨 변경 |
| “우유 사기라고 메모해 줘” | 시간과 함께 저장 |
| “최근 메모 읽어 줘” / “우유가 들어간 메모 삭제해 줘” | 최근 5개 조회 또는 명시한 키워드로 삭제 |
| “부산 날씨 알려 줘” | 현재 기온과 날씨, 가까운 정시까지의 강수 확률 |
| “맥북 상태 알려 줘” | CPU 최고 온도, RAM 사용률, SSD 사용량, Docker 컨테이너 이름과 상태 |

- 대기 중 호출어는 80ms 단위로 확인합니다. 호출어 감지에는 Silero VAD 임계값 0.5를 사용합니다. 비프음 이후에는 RMS로 발화 시작과 1.5초 침묵을 판별하고, 녹음은 최대 10초입니다. 응답이 겹쳐 들리면 먼저 재생이 끝난 뒤 호출하세요.
- 마이크가 잘 잡히는데 발화 시작이 누락되면 `SPEECH_RMS`를 낮추고, 배경 음악에 녹음이 계속 유지되면 값을 높여 보세요. 호출어 민감도는 별도의 `WAKE_WORD_THRESHOLD`를 조절합니다.
- 새 음악이 검색되지 않으면 업로드 완료 여부와 MPD의 폴더 접근 권한을 확인하세요. 온도만 표시되지 않으면 `sudo modprobe coretemp` 후 `sensors`를 확인하세요.
- 2010년형 CPU에서 모델 설치 직후 `--check-model`을 별도로 실행하면 오디오 장치나 API 키 없이 로컬 추론을 점검할 수 있습니다. `Illegal instruction`이 발생하면 사용한 ONNX Runtime 또는 NumPy 휠의 CPU 호환성을 확인하세요.
- 네트워크 장애로 Gemini 또는 TTS를 사용할 수 없으면 해당 요청을 종료하고 다음 호출어를 기다립니다. 이미 도구가 실행됐을 가능성이 있어 실패한 Gemini 요청 전체를 자동 재실행하지 않습니다.

## 공식 문서

- [Google Gen AI SDK](https://googleapis.github.io/python-genai/) · [Gemini 모델 목록](https://ai.google.dev/gemini-api/docs/models)
- [openWakeWord](https://github.com/dscripka/openWakeWord) · [Silero VAD](https://github.com/snakers4/silero-vad)
- [edge-tts](https://github.com/rany2/edge-tts) · [mpv](https://mpv.io/manual/stable/) · [ALSA PCM 플러그인](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html)
- [MPD](https://mpd.readthedocs.io/en/stable/user.html) · [python-mpd2](https://github.com/Mic92/python-mpd2) · [Open-Meteo](https://open-meteo.com/en/docs)
- [CasaOS](https://github.com/icewhaletech/casaos) · [Docker 컨테이너 목록](https://docs.docker.com/reference/cli/docker/container/ls/) · [systemd 소켓 활성화](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket)
- [yt-dlp JavaScript 런타임 안내](https://github.com/yt-dlp/yt-dlp/wiki/EJS) · [ytmusicapi 보관함](https://ytmusicapi.readthedocs.io/en/stable/reference/library.html)
