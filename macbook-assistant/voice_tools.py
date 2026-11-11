"""Gemini-callable local tools and MPD volume recovery."""

import asyncio
from bisect import bisect_right
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import datetime
import fcntl
import json
import logging
import os
from pathlib import Path, PurePosixPath
import socket
import subprocess
from tempfile import NamedTemporaryFile
from time import monotonic, sleep

from httpx import HTTPError
from mpd import CommandError, FailureResponseCode, MPDClient, MPDError


LOG = logging.getLogger(__name__)
MPD_ERRORS = (MPDError, OSError, ValueError)
COMMAND_ERRORS = (OSError, subprocess.SubprocessError)
MPD_UPDATE_WAIT_SECONDS = 10
DOCKER_STATUS_SOCKET = "/run/macbook-assistant-docker.sock"
DOCKER_PS_FORMAT = "{{.Names}} ({{.Status}})"
WEATHER = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
    56: "약한 어는 이슬비", 57: "강한 어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "약한 어는 비", 67: "강한 어는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기",
    85: "약한 눈 소나기", 86: "강한 눈 소나기",
    95: "뇌우", 96: "우박을 동반한 뇌우", 99: "강한 우박을 동반한 뇌우",
}


@contextmanager
def mpd_connection(config):
    client = MPDClient()
    client.timeout = 2.0
    try:
        client.connect(config.mpd_host, config.mpd_port)
        if config.mpd_password:
            client.password(config.mpd_password)
        yield client
    finally:
        client.disconnect()


def song_status(client):
    status = client.status()
    song = client.currentsong()
    state = {"play": "재생 중", "pause": "일시 정지", "stop": "정지"}.get(
        status.get("state"), "상태 확인 불가"
    )
    if not song:
        return f"음악 {state}. 선택된 곡이 없습니다."
    title = song.get("title") or PurePosixPath(song.get("file", "")).name or "제목 미상"
    artist = song.get("artist") or "아티스트 미상"
    if isinstance(title, list):
        title = " / ".join(title)
    if isinstance(artist, list):
        artist = " / ".join(artist)
    return f"음악 {state}. {artist}의 {title}."


def refresh_mpd_library(client):
    """Wait for a fresh database update, including one queued behind a busy scan."""
    deadline = monotonic() + MPD_UPDATE_WAIT_SECONDS
    while True:
        try:
            client.update()
            accepted = True
        except CommandError as error:
            if error.errno != FailureResponseCode.UPDATE_ALREADY:
                raise
            accepted = False
        status = client.status()
        while status.get("updating_db") and monotonic() < deadline:
            sleep(0.2)
            status = client.status()
        if accepted and not status.get("updating_db"):
            return status
        if monotonic() >= deadline:
            return None
        sleep(0.2)


class VoiceTools:
    def __init__(self, config, http):
        self.config = config
        self.http = http

    async def control_music(self, action: str, query: str = "") -> str:
        """Control MPD music or report its current track and state.

        Play and search_and_play refresh the music library before playback.

        Args:
            action: One of play, pause, stop, next, prev, status, search_and_play.
            query: Track, artist, or album to search for when action is search_and_play.
        """
        return await asyncio.to_thread(self._control_music, action.strip().lower(), query.strip())

    def _control_music(self, action, query):
        if action not in {"play", "pause", "stop", "next", "prev", "status", "search_and_play"}:
            return "지원하지 않는 음악 명령입니다."
        if action == "search_and_play" and not 1 <= len(query) <= 200:
            return "검색할 곡이나 아티스트 이름을 알려 주세요."
        try:
            with mpd_connection(self.config) as client:
                if action in {"play", "search_and_play"}:
                    status = refresh_mpd_library(client)
                    if status is None:
                        return "MPD가 음악 폴더를 아직 스캔 중입니다. 잠시 뒤 다시 요청해 주세요."
                if action == "play" and int(status.get("playlistlength", -1)) == 0:
                    if int(client.stats()["songs"]) == 0:
                        return "MPD 음악 폴더에서 재생할 음원을 찾지 못했습니다."
                    client.add("")
                if action == "search_and_play":
                    matches = client.search("any", query, "window", "0:1")
                    if not matches:
                        matches = client.search("file", query, "window", "0:1")
                    if not matches:
                        return f"MPD 음악 라이브러리에 {query} 검색 결과가 없습니다."
                    song_id = client.addid(matches[0]["file"])
                    client.playid(song_id)
                elif action == "pause":
                    client.pause(1)
                elif action != "status":
                    method = "previous" if action == "prev" else action
                    getattr(client, method)()
                return song_status(client)
        except MPD_ERRORS as error:
            LOG.warning("MPD music request failed (%s)", type(error).__name__)
            return "음악 요청을 완료했는지 확인할 수 없습니다. MPD 상태를 확인해 주세요."

    async def set_system_volume(self, level: int) -> str:
        """Set the ALSA Master volume or MPD music volume as an absolute percent.

        Args:
            level: Integer from 0 through 100, inclusive.
        """
        if type(level) is not int or not 0 <= level <= 100:
            return "볼륨은 0부터 100 사이의 정수로 알려 주세요."
        return await asyncio.to_thread(self._set_system_volume, level)

    def _set_system_volume(self, level):
        backend = self.config.volume_backend
        if backend in {"auto", "alsa"}:
            command = ["amixer"]
            if self.config.alsa_control_device:
                command.extend(("-D", self.config.alsa_control_device))
            applied = []
            for control in ("Master", "Headphone"):
                try:
                    subprocess.run([*command, "sset", control, f"{level}%", "unmute"],
                                   capture_output=True, timeout=3, check=True)
                    applied.append(control)
                except COMMAND_ERRORS as error:
                    LOG.warning("ALSA %s volume failed (%s)", control, type(error).__name__)
            if len(applied) == 2:
                return f"시스템 볼륨을 {level}퍼센트로 설정했습니다."
            if applied:
                return f"ALSA에서 {applied[0]} 볼륨을 {level}퍼센트로 설정했습니다."
            if backend == "alsa":
                return "ALSA Master와 Headphone 볼륨을 변경하지 못했습니다."
        try:
            with mpd_connection(self.config) as client:
                client.setvol(level)
            return f"음악 볼륨을 {level}퍼센트로 설정했습니다."
        except MPD_ERRORS as error:
            LOG.warning("MPD volume failed (%s)", type(error).__name__)
            return "음악 볼륨을 변경하지 못했습니다."

    async def Manage_memo(self, action: str, content: str = "") -> str:
        """Add, read, or delete local memos. Delete only on an explicit request.

        Args:
            action: One of add, read (five most recent), or delete (matching entries).
            content: Memo text for add, or the user-provided nonempty keyword for delete.
        """
        action, content = action.strip().lower(), content.strip()
        if action not in {"add", "read", "delete"}:
            return "지원하지 않는 메모 명령입니다."
        if action != "read" and not 1 <= len(content) <= 500:
            return "저장할 내용이나 삭제할 키워드를 500자 이내로 알려 주세요."
        try:
            return await asyncio.to_thread(self._manage_memo, action, content)
        except (OSError, ValueError) as error:
            LOG.warning("Memo operation failed (%s)", type(error).__name__)
            return "메모 파일에 접근하지 못했거나 파일 형식을 읽을 수 없습니다."

    def _manage_memo(self, action, content):
        path = self.config.memo_path.resolve()
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                entries = []
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict)
                or not isinstance(entry.get("time"), str)
                or not isinstance(entry.get("content"), str)
                for entry in entries
            ):
                raise ValueError("Invalid memo format")
            if action == "read":
                return "\n".join(
                    f"{entry['time']} {entry['content']}" for entry in reversed(entries[-5:])
                ) or "저장된 메모가 없습니다."
            if action == "add":
                entries.append({"time": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "content": content})
                write_memos(path, entries)
                return "메모를 저장했습니다."
            needle = content.casefold()
            kept = [entry for entry in entries if needle not in entry["content"].casefold()]
            count = len(entries) - len(kept)
            if count:
                write_memos(path, kept)
            return f"키워드와 일치하는 메모 {count}개를 삭제했습니다."

    async def get_weather(self, city: str = "Busan") -> str:
        """Report current temperature, sky condition, and near-term precipitation probability.

        Args:
            city: City name in Korean or English. Use Busan when the user omits the city.
        """
        city = city.strip()
        if len(city) > 100:
            return "도시 이름을 짧게 알려 주세요."
        try:
            if city.casefold() in {"", "busan", "부산", "부산광역시", self.config.city_name.casefold()}:
                lat, lon = self.config.city_lat_lon
                name = self.config.city_name
            else:
                response = await self.http.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": city, "count": 1, "language": "ko", "format": "json"},
                )
                response.raise_for_status()
                matches = response.json().get("results", [])
                if not matches:
                    return f"{city}의 위치를 찾지 못했습니다."
                location = matches[0]
                lat, lon = location["latitude"], location["longitude"]
                name = ", ".join(filter(None, (location["name"], location.get("country"))))
            response = await self.http.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
                        "current": "temperature_2m,weather_code", "hourly": "precipitation_probability"},
            )
            response.raise_for_status()
            forecast = response.json()
            current, hourly = forecast["current"], forecast["hourly"]
            condition = WEATHER.get(current.get("weather_code"), "상태 정보 없음")
            temperature = current.get("temperature_2m")
            degrees = f"섭씨 {temperature:g}도" if temperature is not None else "기온 정보 없음"
            times = hourly["time"]
            index = bisect_right(times, current["time"])
            if index < len(times) and hourly["precipitation_probability"][index] is not None:
                probability = hourly["precipitation_probability"][index]
                hour = int(times[index].split("T")[1].split(":")[0])
                rain = f"현지 시각 {hour}시까지의 강수 확률 {probability}퍼센트"
            else:
                rain = "강수 확률 정보 없음"
            return f"{name} 현재 {degrees}, {condition}. {rain}."
        except (HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            LOG.warning("Weather request failed (%s)", type(error).__name__)
            return "날씨 정보를 지금 가져오지 못했습니다."

    async def get_server_health(self) -> str:
        """Report CPU temperature, RAM, SSD filesystem usage and CasaOS Docker container states."""
        return await asyncio.to_thread(self._get_server_health)

    def _get_server_health(self):
        temperatures = cpu_temperatures()
        cpu = f"CPU 최고 온도 섭씨 {max(temperatures):.1f}도" if temperatures else "CPU 온도 정보 없음"
        try:
            lines = Path("/proc/meminfo").read_text().splitlines()
            ram = {key: int(value.split()[0]) for key, value in
                   (line.split(":", 1) for line in lines)
                   if key in {"MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached"}}
            total = ram["MemTotal"]
            available = ram.get("MemAvailable", ram.get("MemFree", 0) + ram.get("Buffers", 0)
                                + ram.get("Cached", 0))
            used = min(total, max(0, total - available))
            memory = (f"RAM 사용률 {used / total:.0%}, "
                      f"{used / 1024**2:.1f}기가바이트 사용, 총 {total / 1024**2:.1f}기가바이트")
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            memory = "RAM 정보 없음"
        try:
            result = subprocess.run(
                ["df", "-h", "--output=size,used,avail,pcent", "--", self.config.disk_path],
                capture_output=True, check=True, timeout=3, text=True,
            )
            size, used, available, percent = result.stdout.splitlines()[-1].split()
            disk = f"SSD 파일시스템 {used} 사용, 총 {size}, 사용률 {percent}, 여유 {available}"
        except (*COMMAND_ERRORS, ValueError, IndexError):
            disk = "SSD 파일시스템 정보 없음"
        return f"{cpu}. {memory}. {disk}. {docker_status()}"


def read_docker_status_socket():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(6)
        connection.connect(DOCKER_STATUS_SOCKET)
        with connection.makefile("rb") as stream:
            raw = stream.read(65_537)
    rows = raw.decode("utf-8", errors="replace").splitlines()
    if len(raw) > 65_536 or not rows or rows[-1] != "DOCKER_PS_OK":
        raise OSError("Docker status service did not finish successfully")
    return rows[:-1]


def docker_status():
    try:
        try:
            rows = read_docker_status_socket()
        except (FileNotFoundError, ConnectionRefusedError):
            result = subprocess.run(
                ["docker", "ps", "--format", DOCKER_PS_FORMAT],
                capture_output=True, check=True, timeout=4, text=True, encoding="utf-8",
            )
            rows = result.stdout.splitlines()
    except FileNotFoundError:
        return "Docker CLI를 찾을 수 없어 CasaOS 컨테이너 상태를 확인하지 못했습니다."
    except (*COMMAND_ERRORS, UnicodeError) as error:
        LOG.warning("Docker container status unavailable (%s)", type(error).__name__)
        return "CasaOS Docker 컨테이너 상태를 확인하지 못했습니다."
    if not rows:
        return "CasaOS Docker 호스트에 실행 중인 컨테이너가 없습니다."
    return f"CasaOS Docker 호스트의 컨테이너 {len(rows)}개.\n" + "\n".join(rows)


def write_memos(path, entries):
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            json.dump(entries, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def cpu_temperatures():
    readings = []
    for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        try:
            name = (zone / "type").read_text().strip().lower()
            if any(label in name for label in ("cpu", "pkg", "coretemp", "processor")):
                readings.append(float((zone / "temp").read_text()) / 1000)
        except (OSError, ValueError):
            continue
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            name = (hwmon / "name").read_text().strip()
        except OSError:
            continue
        if name in {"coretemp", "k10temp", "zenpower"}:
            for sensor in hwmon.glob("temp*_input"):
                with suppress(OSError, ValueError):
                    readings.append(float(sensor.read_text()) / 1000)
    if not readings:
        try:
            result = subprocess.run(["sensors", "-j"], capture_output=True,
                                    check=True, timeout=3, text=True)
            for chip, values in json.loads(result.stdout).items():
                if chip.startswith(("coretemp", "k10temp", "zenpower", "cpu_thermal")):
                    for feature in values.values():
                        if isinstance(feature, dict):
                            readings.extend(value for key, value in feature.items()
                                            if key.startswith("temp") and key.endswith("_input")
                                            and isinstance(value, (int, float)))
        except (*COMMAND_ERRORS, ValueError):
            pass
    return [reading for reading in readings if -30 < reading < 150]


class MusicDucking:
    """Restore volume even when playback raises or is cancelled."""

    def __init__(self, config):
        self.config = config
        self.lock = asyncio.Lock()
        self.pending = None

    def _lower(self):
        try:
            with mpd_connection(self.config) as client:
                status = client.status()
                original = int(status.get("volume", -1))
                if status.get("state") != "play" or not 0 < original <= 100:
                    return None
                lowered = round(original * 0.70)
                if lowered == original:
                    return None
                try:
                    client.setvol(lowered)
                except MPD_ERRORS as error:
                    # A lost acknowledgement can still leave the lower volume applied.
                    LOG.warning("MPD ducking update uncertain (%s)", type(error).__name__)
                return original, lowered
        except MPD_ERRORS as error:
            LOG.warning("MPD ducking unavailable (%s)", type(error).__name__)
            return None

    def _restore(self, original, lowered):
        try:
            with mpd_connection(self.config) as client:
                current = int(client.status().get("volume", -1))
                if current < 0:
                    return False
                if current == lowered:
                    client.setvol(original)
                return True
        except MPD_ERRORS as error:
            LOG.warning("MPD restore deferred (%s)", type(error).__name__)
            return False

    async def _restore_pending(self):
        if self.pending is not None and await asyncio.to_thread(self._restore, *self.pending):
            self.pending = None

    @asynccontextmanager
    async def during_playback(self):
        async with self.lock:
            await self._restore_pending()
            if self.pending is not None:
                yield
                return
            lowering = asyncio.create_task(asyncio.to_thread(self._lower))
            try:
                await asyncio.shield(lowering)
                yield
            finally:
                self.pending = await lowering
                await self._restore_pending()

    async def recover_loop(self):
        while True:
            await asyncio.sleep(5)
            await self.restore()

    async def restore(self):
        async with self.lock:
            await self._restore_pending()
