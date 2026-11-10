#!/usr/bin/env python3
"""DietPi MacBook web remote and Now Playing frame. Python 3.10+."""

import asyncio
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import secrets
import select
import stat
import subprocess
import sys
import time
from array import array
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread
from typing import Literal
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

import psutil
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from mpd import CommandError, MPDClient, MPDError
from pydantic import BaseModel, Field

LOG = logging.getLogger("uvicorn.error")
MPD_HOST = os.getenv("MPD_HOST", "localhost")
MPD_PORT = int(os.getenv("MPD_PORT", "6600"))
MPD_PASSWORD = os.getenv("MPD_PASSWORD", "")
MEMO_PATH = Path(os.getenv("MEMO_PATH", "/opt/macbook-assistant/memo.json"))
FIFO_PATH = Path(os.getenv("PCM_FIFO", "/run/macbook-audio/pcm"))
DISK_PATH = os.getenv("HEALTH_DISK_PATH", "/")
SMC = Path("/sys/devices/platform/applesmc.768")
BACKLIGHT = Path("/sys/class/backlight")
BACKLIGHT_DEVICE = os.getenv("BACKLIGHT_DEVICE", "").strip()
CITY = os.getenv("CITY_NAME", "부산")
CITY_LAT_LON = os.getenv("CITY_LAT_LON", "35.1796,129.0756")
REMOTE_KEY = os.getenv("REMOTE_KEY", "")
SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"
BAR_COUNT = 32
QUEUE_LIMIT = 8
REMOTE_HOST = "0.0.0.0"
REMOTE_PORT = 8080


class MusicAction(BaseModel):
    action: Literal["play", "pause", "previous", "next", "volume"]
    volume: int | None = Field(default=None, ge=0, le=100, strict=True)


class MemoInput(BaseModel):
    content: str = Field(min_length=1, max_length=500)


class MemoRemoval(BaseModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


def require_key(request: Request):
    supplied = request.headers.get("X-Remote-Key", "")
    if not REMOTE_KEY or not supplied.isascii() or not secrets.compare_digest(supplied, REMOTE_KEY):
        raise HTTPException(401, "리모컨 키를 확인해 주세요.")


@contextmanager
def mpd_connection(rejection="MPD가 요청을 거절했습니다. 대기열이나 믹서를 확인해 주세요."):
    # Each request owns its socket. MPDClient is not thread-safe.
    client = MPDClient()
    client.timeout = 2
    try:
        client.connect(MPD_HOST, MPD_PORT)
        if MPD_PASSWORD:
            client.password(MPD_PASSWORD)
        yield client
    except CommandError as exc:
        LOG.warning("MPD rejected command: %s", exc)
        raise HTTPException(409, rejection) from exc
    except (OSError, MPDError) as exc:
        raise HTTPException(503, "MPD에 연결할 수 없습니다.") from exc
    finally:
        client.disconnect()


def tag(song, key):
    value = song.get(key, "")
    return ", ".join(value) if isinstance(value, list) else value


def song_metadata(song):
    return {
        "title": tag(song, "title") or tag(song, "name") or Path(song.get("file", "")).stem,
        "artist": tag(song, "artist") or tag(song, "albumartist"),
        "album": tag(song, "album"),
    }


def now_playing(client):
    client.command_list_ok_begin()
    client.status()
    client.currentsong()
    status, song = client.command_list_end()
    volume = int(status.get("volume", "-1"))
    return {
        **song_metadata(song),
        "state": status.get("state", "stop"),
        "volume": volume if 0 <= volume <= 100 else None,
    }


@contextmanager
def memo_entries():
    # Share the voice assistant's resolved path and lock, including symlink installs.
    try:
        path = MEMO_PATH.resolve()
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                raw = b"[]"
            entries = json.loads(raw)
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict)
                or not isinstance(entry.get("time"), str)
                or not isinstance(entry.get("content"), str)
                for entry in entries
            ):
                raise ValueError("Invalid memo schema")
            yield path, entries, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, RuntimeError) as exc:
        LOG.warning("Memo operation failed (%s)", type(exc).__name__)
        raise HTTPException(503, "메모 파일의 권한이나 형식을 확인해 주세요.") from exc


def save_memos(path, entries):
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            json.dump(entries, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def read_text(path):
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def read_int(path):
    value = read_text(path)
    return int(value) if re.fullmatch(r"-?\d+", value) else None


def system_stats():
    fans = []
    smc_cpu = []
    for root in (SMC, *sorted((SMC / "hwmon").glob("hwmon*"))):
        for sensor in sorted(root.glob("fan*_input")):
            rpm = read_int(sensor)
            if rpm is not None and rpm >= 0:
                fans.append({"fan": sensor.stem.split("_")[0], "rpm": rpm})
        for label in root.glob("temp*_label"):
            # Apple SMC temperature keys beginning with TC are CPU-related.
            if read_text(label).startswith("TC"):
                raw = read_int(label.with_name(label.name.replace("_label", "_input")))
                if raw is not None and 0 < raw < 130_000:
                    smc_cpu.append(raw / 1000)
    temps = []
    with suppress(OSError, AttributeError):
        for sensor in psutil.sensors_temperatures().get("coretemp", []):
            if 0 < sensor.current < 130:
                temps.append(sensor.current)
    frequency = None
    with suppress(OSError, NotImplementedError):
        freq = psutil.cpu_freq()
        if freq and freq.current > 0:
            frequency = round(freq.current)
    disk = None
    with suppress(OSError):
        disk = psutil.disk_usage(DISK_PATH).percent
    fans = list({item["fan"]: item for item in fans}.values())
    return {
        "cpu_mhz": frequency,
        "cpu_temp": max(temps or smc_cpu, default=None),
        "cpu_temp_source": "coretemp" if temps else ("Apple SMC" if smc_cpu else None),
        "fans": fans,
        "ram_percent": psutil.virtual_memory().percent,
        "disk_percent": disk,
        "disk_path": DISK_PATH,
    }


backlight_lock = Lock()
last_brightness = 0


def backlight_path():
    if BACKLIGHT_DEVICE:
        return BACKLIGHT / BACKLIGHT_DEVICE
    choices = sorted(BACKLIGHT.glob("*"))
    return choices[0] if len(choices) == 1 else None


def backlight_status():
    path = backlight_path()
    level = read_int(path / "brightness") if path is not None else None
    if level is None:
        return {"ready": False, "on": None, "device": None}
    return {"ready": os.access(path / "brightness", os.W_OK), "on": level > 0, "device": path.name}


def toggle_backlight():
    global last_brightness
    with backlight_lock:
        path = backlight_path()
        current = backlight_status()
        if path is None or not current["ready"]:
            raise HTTPException(503, "백라이트 장치 이름과 brightness 쓰기 권한을 확인해 주세요.")
        brightness = path / "brightness"
        level = read_int(brightness)
        maximum = read_int(path / "max_brightness")
        if level is None or maximum is None or maximum < 1:
            raise HTTPException(503, "백라이트 밝기를 읽을 수 없습니다.")
        target = 0 if level > 0 else min(maximum, last_brightness or max(1, round(maximum * 0.6)))
        try:
            brightness.write_text(f"{target}\n", encoding="ascii")
        except OSError as exc:
            raise HTTPException(503, "백라이트 밝기 쓰기에 실패했습니다.") from exc
        if level > 0:
            last_brightness = level
        return backlight_status()


weather_lock = Lock()
weather_cached = None
weather_retry_at = 0.0


def weather():
    global weather_cached, weather_retry_at
    with weather_lock:
        if time.monotonic() < weather_retry_at:
            return weather_cached or {"available": False, "city": CITY}
        lat, lon = map(float, CITY_LAT_LON.split(","))
        params = urlencode({"latitude": lat, "longitude": lon,
                            "current": "temperature_2m,weather_code",
                            "timezone": "auto", "forecast_days": 1})
        try:
            with urlopen("https://api.open-meteo.com/v1/forecast?" + params, timeout=4) as response:
                current = json.loads(response.read(65536))["current"]
            temperature = float(current["temperature_2m"])
            code = int(current["weather_code"])
            observed = current["time"]
            if not math.isfinite(temperature) or not isinstance(observed, str):
                raise ValueError("Invalid weather observation")
            weather_cached = {"available": True, "city": CITY, "temperature": temperature,
                              "code": code, "observed": observed, "stale": False}
            weather_retry_at = time.monotonic() + 900
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            LOG.warning("Weather update failed (%s)", type(exc).__name__)
            weather_retry_at = time.monotonic() + 60
            if weather_cached is not None:
                weather_cached = {**weather_cached, "stale": True}
        return weather_cached or {"available": False, "city": CITY}


class PCMReader:
    """Drain MPD's FIFO and expose a small peak envelope, never raw audio."""

    def __init__(self, path):
        self.path = path
        self.stop = Event()
        self.latest = (0.0, [])

    def run(self):
        while not self.stop.is_set():
            fd = None
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                opened = os.fstat(fd)
                if not stat.S_ISFIFO(opened.st_mode):
                    raise OSError("PCM path is not a FIFO")
                self.drain(fd, opened)
            except OSError:
                self.stop.wait(1)
            finally:
                if fd is not None:
                    os.close(fd)

    def drain(self, fd, opened):
        pending = b""
        next_check = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            if now >= next_check:
                current = self.path.stat()
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    return
                next_check = now + 1
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            try:
                data = os.read(fd, 8192)
            except BlockingIOError:
                continue
            if not data:
                pending = b""
                self.latest = (0.0, [])
                self.stop.wait(0.2)
                continue
            pending += data
            # 2048 samples at 16 kHz: about 8 frames/s and 64 samples per bar.
            if len(pending) < 4096:
                continue
            complete = len(pending) // 4096 * 4096
            samples = array("h", pending[complete - 4096:complete])
            pending = pending[complete:]
            if sys.byteorder != "little":
                samples.byteswap()
            step = len(samples) // BAR_COUNT
            bars = [round(max(map(abs, samples[start:start + step])) / 32768, 3)
                    for start in range(0, len(samples), step)]
            self.latest = (time.monotonic(), bars)

    def envelope(self):
        updated, bars = self.latest
        return bars if time.monotonic() - updated < 0.8 else []


pcm = PCMReader(FIFO_PATH)
suspend_lock = Lock()


@asynccontextmanager
async def lifespan(_app):
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", REMOTE_KEY):
        raise RuntimeError("Set REMOTE_KEY to a random 32–128 character URL-safe key.")
    if BACKLIGHT_DEVICE and (
        BACKLIGHT_DEVICE in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.:-]+", BACKLIGHT_DEVICE)
    ):
        raise RuntimeError("BACKLIGHT_DEVICE must be one device name in /sys/class/backlight.")
    try:
        lat, lon = map(float, CITY_LAT_LON.split(","))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("CITY_LAT_LON must contain latitude,longitude.") from exc
    pcm.stop.clear()
    reader = Thread(target=pcm.run, name="mpd-pcm", daemon=True)
    reader.start()
    try:
        yield
    finally:
        pcm.stop.set()
        await asyncio.to_thread(reader.join, 1.5)


def casaos_origins():
    origins = {"http://localhost", "http://127.0.0.1"}
    for entry in os.getenv("CASAOS_ORIGINS", "").split(","):
        value = entry.strip()
        if not value:
            continue
        try:
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.path not in {"", "/"}
                or any(char in value for char in "*?#\\") or any(char.isspace() for char in value)
            ):
                raise ValueError
            port = url.port
        except ValueError as exc:
            raise RuntimeError("CASAOS_ORIGINS must list exact HTTP(S) origins, e.g. http://192.168.0.10.") from exc
        host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        default_port = 80 if url.scheme == "http" else 443
        suffix = f":{port}" if port is not None and port != default_port else ""
        origins.add(f"{url.scheme}://{host}{suffix}")
    return sorted(origins)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=casaos_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Remote-Key"],
)
KEY_REQUIRED = [Depends(require_key)]


@app.middleware("http")
async def private_response(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def page(view):
    nonce = secrets.token_urlsafe(18)
    html = TEMPLATE.replace("__VIEW__", view).replace("__NONCE__", nonce)
    policy = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; "
        "connect-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; "
        f"script-src 'nonce-{nonce}';"
    )
    return HTMLResponse(html, headers={"Content-Security-Policy": policy})


@app.get("/")
def remote_view():
    return page("remote")


@app.get("/frame")
def frame_view():
    return page("frame")


CASAOS_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
<defs><linearGradient id="tile" x2="1" y2="1"><stop stop-color="#334e87"/><stop offset="1" stop-color="#0f172a"/></linearGradient></defs>
<rect x="2" y="2" width="124" height="124" rx="30" fill="url(#tile)" stroke="#647ba8" stroke-width="2"/>
<rect x="24" y="29" width="80" height="55" rx="12" fill="#0f172a" stroke="#dbeafe" stroke-width="4"/>
<path d="M37 58h10l5-12 8 24 8-29 7 18h16M45 98h38" fill="none" stroke="#93c5fd" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


@app.get("/casaos-icon.svg")
def casaos_icon():
    return Response(CASAOS_ICON, media_type="image/svg+xml",
                    headers={"Content-Security-Policy": "default-src 'none'; sandbox"})


@app.get("/api/playing")
def public_playing():
    with mpd_connection() as client:
        result = now_playing(client)
    result.pop("volume")
    return result


@app.get("/api/weather")
def public_weather():
    return weather()


@app.get("/api/visualizer")
async def visualizer():
    return {"bars": pcm.envelope()}


@app.get("/api/remote", dependencies=KEY_REQUIRED)
def remote_status():
    music = None
    music_error = None
    try:
        with mpd_connection() as client:
            music = now_playing(client)
    except HTTPException as exc:
        music_error = exc.detail
    return {"music": music, "music_error": music_error}


@app.get("/api/system", dependencies=KEY_REQUIRED)
def remote_system():
    return {**system_stats(), "backlight": backlight_status()}


@app.get("/api/mpd/queue", dependencies=KEY_REQUIRED)
def music_queue():
    with mpd_connection("MPD 재생 대기열을 읽을 수 없습니다.") as client:
        status = client.status()
        total = int(status.get("playlistlength", "0"))
        selected = int(status.get("song", "-1"))
        start = min(max(selected - 1, 0), max(total - QUEUE_LIMIT, 0))
        songs = client.playlistinfo(f"{start}:{min(start + QUEUE_LIMIT, total)}") if total else []
    return {
        "total": total,
        "updating_db": bool(status.get("updating_db")),
        "items": [
            {**song_metadata(song), "position": position + 1, "selected": position == selected}
            for position, song in enumerate(songs, start)
        ],
    }


@app.post("/api/mpd/update", dependencies=KEY_REQUIRED, status_code=202)
def update_music_database():
    with mpd_connection("MPD가 음원 스캔을 거절했습니다. MPD의 update 권한과 현재 스캔 상태를 확인해 주세요.") as client:
        job = client.update()
    return {"job_id": job}


@app.post("/api/music", dependencies=KEY_REQUIRED)
def music_action(body: MusicAction):
    with mpd_connection() as client:
        if body.action == "volume":
            if body.volume is None:
                raise HTTPException(422, "볼륨은 0~100 사이 정수여야 합니다.")
            if int(client.status().get("volume", "-1")) < 0:
                raise HTTPException(409, "MPD 출력에 볼륨 믹서를 설정해 주세요.")
            client.setvol(body.volume)
        elif body.action == "pause":
            client.pause(1)
        elif body.action == "play":
            if client.status().get("state") == "pause":
                client.pause(0)
            else:
                client.play()
        elif body.action == "previous":
            client.previous()
        else:
            client.next()
        return now_playing(client)


@app.get("/api/memos", dependencies=KEY_REQUIRED)
def list_memos():
    with memo_entries() as (_, entries, revision):
        return {"revision": revision, "items": [
            {"index": index, "time": entries[index]["time"], "content": entries[index]["content"]}
            for index in range(len(entries) - 1, -1, -1)
        ]}


@app.post("/api/memos", dependencies=KEY_REQUIRED, status_code=201)
def add_memo(body: MemoInput):
    content = body.content.strip()
    if not content:
        raise HTTPException(422, "메모 내용을 입력해 주세요.")
    with memo_entries() as (path, entries, _):
        entries.append({"time": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "content": content})
        save_memos(path, entries)
    return {"ok": True}


@app.post("/api/memos/{index}/delete", dependencies=KEY_REQUIRED)
def delete_memo(index: int, body: MemoRemoval):
    with memo_entries() as (path, entries, revision):
        if body.revision != revision or not 0 <= index < len(entries):
            raise HTTPException(409, "메모 목록이 바뀌었습니다. 목록을 확인하고 다시 삭제해 주세요.")
        del entries[index]
        save_memos(path, entries)
    return {"ok": True}


@app.post("/api/power/backlight", dependencies=KEY_REQUIRED)
def backlight_action():
    return toggle_backlight()


@app.post("/api/power/suspend", dependencies=KEY_REQUIRED)
def suspend_action():
    if not suspend_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 절전 요청을 처리하고 있습니다.")
    try:
        subprocess.run([SUDO, "-n", SYSTEMCTL, "suspend"], check=True, timeout=8,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        LOG.warning("Suspend rejected: %s", exc.stderr.decode(errors="replace").strip())
        raise HTTPException(503, "절전 요청이 거절됐습니다. sudoers와 systemd 로그를 확인해 주세요.") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "절전 요청 결과를 확인할 수 없습니다.") from exc
    finally:
        suspend_lock.release()
    return JSONResponse({"accepted": True}, status_code=202)


TEMPLATE = r"""<!doctype html>
<html lang="ko" data-view="__VIEW__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>맥북 오디오 · 웹 리모컨</title>
<link rel="icon" href="/casaos-icon.svg" type="image/svg+xml">
<script defer nonce="__NONCE__" crossorigin="anonymous"
 integrity="sha384-P0V9YU7TFy919I0oVLXiqiUsdiJiaV+QKGysF4Lx+s901F1X9+zjFV0mc6pRKCKq"
 src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4.1.13/dist/index.global.js"></script>
<style>
:root{color-scheme:dark;--bg:#0f172a;--panel:rgb(255 255 255/.1);--line:rgb(255 255 255/.15);
 --ink:#f1f5f9;--muted:#becadd;--accent:#bfdbfe;--warn:#fecaca;font-family:system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--ink);
 background-image:radial-gradient(ellipse at 12% 0%,#283e67 0,transparent 42rem),
 radial-gradient(ellipse at 90% 12%,#222d55 0,transparent 35rem)}
[hidden]{display:none!important}
html[data-view=remote] .frame,html[data-view=frame] .remote{display:none}
h1,h2,h3,p{margin:0}a{color:var(--accent)}button,input,textarea{font:inherit}
button,input[type=password],textarea{border:1px solid var(--line);border-radius:.75rem;padding:.65rem .85rem}
button{background:rgb(15 23 42/.56);color:var(--ink);cursor:pointer;min-height:44px}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
button:disabled{opacity:.5;cursor:default}.primary{background:var(--accent);color:#172554;border-color:transparent;font-weight:700}
.danger{color:var(--warn)}input[type=password],textarea{background:rgb(15 23 42/.72);color:var(--ink);width:100%;min-width:0}
input[type=range]{width:100%;min-height:35px;accent-color:var(--accent)}
.shell{width:min(100% - 1.75rem,60rem);margin:auto;padding:1.5rem 0 2.5rem}
.row{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}.split{justify-content:space-between}
.topbar{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem;padding:.4rem;margin-bottom:1.25rem}
.nav-link{display:inline-flex;align-items:center;min-height:44px;border-radius:.7rem;padding:.55rem .75rem;
 color:var(--ink);text-decoration:none;font-size:.88rem;font-weight:650}
.nav-link:hover,.nav-link:focus-visible{background:rgb(255 255 255/.12)}
.nav-link+ .nav-link{color:var(--accent)}
.brand{display:flex;align-items:center;gap:.75rem;margin:0 0 .9rem}
.brand img{width:3.15rem;height:3.15rem;flex:none}
.kicker{font-size:.72rem;font-weight:750;letter-spacing:.2em;color:var(--accent)}
.shell h1{font-size:clamp(1.75rem,5vw,2.7rem);line-height:1.15;margin:.25rem 0 0}
.muted,small{color:var(--muted)}
.glass{background:var(--panel);border:1px solid var(--line);border-radius:1rem;
 -webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);box-shadow:0 10px 32px rgb(2 6 23/.17)}
.panel{padding:1.05rem;min-width:0}
.panel h2{font-size:1rem;margin-bottom:.75rem}.grid-remote{display:grid;gap:.8rem;margin-top:.8rem}
@media(min-width:48rem){.grid-remote{grid-template-columns:1fr 1fr}.wide{grid-column:1/-1}}
.form-stack{display:grid;gap:.65rem}.notice{min-height:1.5rem;color:var(--warn);font-size:.88rem;margin:.5rem 0}
.track{overflow-wrap:anywhere;line-height:1.13}.remote .track{font-size:clamp(1.45rem,5vw,2.5rem);margin:.4rem 0}
.metadata{color:var(--muted);overflow-wrap:anywhere}.controls{margin:.9rem 0 .55rem}.controls .primary{min-width:8rem}
.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.55rem;margin:0}
.stats div{padding:.55rem .65rem;background:rgb(15 23 42/.55);border-radius:.65rem;overflow-wrap:anywhere}
.stats dt{font-size:.78rem;color:var(--muted)}.stats dd{margin:.2rem 0 0;font-size:1.05rem;font-variant-numeric:tabular-nums}
.memo-list{list-style:none;padding:0;margin:.6rem 0 0;max-height:24rem;overflow:auto}
.memo-list li{border-top:1px solid var(--line);padding:.65rem 0;display:flex;align-items:center;justify-content:space-between;gap:.75rem}
.memo-list p{overflow-wrap:anywhere;white-space:pre-wrap}.memo-list button{flex:none}
.queue{margin-top:.85rem;padding-top:.85rem;border-top:1px solid var(--line)}
.queue-list{list-style:none;margin:.25rem 0 .8rem;padding:0;max-height:14rem;overflow:auto}
.queue-list li{display:grid;grid-template-columns:2rem minmax(0,1fr);gap:.25rem .55rem;
 padding:.48rem .5rem;border-radius:.55rem;overflow-wrap:anywhere}
.queue-list li[aria-current=true]{background:rgb(147 197 253/.13)}
.queue-list li small{grid-column:2}.queue-number{color:var(--muted);font-variant-numeric:tabular-nums}
.upload-help{padding:.8rem;border:1px solid var(--line);border-radius:.8rem;background:rgb(15 23 42/.44)}
.upload-help p{margin-bottom:.6rem;color:var(--muted);font-size:.88rem;overflow-wrap:anywhere}
.upload-help code{color:var(--ink)}
dialog{position:fixed;inset:0;margin:auto;width:min(28rem,calc(100% - 2rem));border:1px solid var(--line);
 background:#1e293b;color:var(--ink);border-radius:1rem;padding:1.15rem}
dialog::backdrop{background:#000b}dialog p{margin:.65rem 0 1rem;color:var(--muted)}
.frame{min-height:100vh}
.frame-shell{width:min(100% - 2.5rem,79rem);min-height:100vh;margin:auto;padding:clamp(1rem,4vw,3.5rem) 0 1.4rem;
 display:grid;grid-template-rows:auto 1fr auto;gap:clamp(1rem,3vh,2.5rem)}
.frame-grid{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);align-items:center;gap:clamp(1rem,5vw,5rem)}
.clock{font-size:clamp(4rem,12vw,11rem);letter-spacing:-.085em;font-weight:740;line-height:.94;
 font-variant-numeric:tabular-nums;white-space:nowrap;margin:.5rem 0 .65rem}
.frame-date{font-size:clamp(1rem,2.3vw,1.65rem);color:var(--muted)}
.weather{margin-top:1.35rem;border-top:1px solid var(--line);padding-top:.8rem}.weather p{font-size:clamp(1.1rem,2.5vw,1.7rem)}
.frame .track{font-size:clamp(1.8rem,5vw,5rem);letter-spacing:-.04em;margin:.45rem 0 .75rem}
.frame .metadata{font-size:clamp(1rem,2vw,1.45rem)}
.frame-music{padding:clamp(1rem,3vw,2.5rem)}
.wave{display:flex;align-items:center;gap:clamp(2px,.45vw,6px);height:clamp(2.8rem,13vh,7rem);margin-top:.65rem}
.wave span{width:100%;height:100%;border-radius:4px;background:var(--accent);transform:scaleY(.035);
 transition:transform 95ms linear;opacity:.84}
.frame-foot{border-top:1px solid var(--line);padding-top:.75rem;font-size:.85rem;color:var(--muted)}
.frame-foot a{color:inherit}.frame-foot button{font-size:.85rem}
@media(max-width:43rem) and (orientation:portrait){.frame-grid{grid-template-columns:1fr;align-content:center}
 .clock{font-size:clamp(4rem,19vw,7rem)}}
@media(prefers-reduced-motion:reduce){.wave span{transition:none}}
</style>
</head>
<body class="bg-slate-900">
<main class="remote shell">
 <header>
  <nav class="topbar glass backdrop-blur-md bg-white/10 border border-white/15 rounded-2xl" aria-label="빠른 이동">
   <a class="nav-link" id="casaos-link">CasaOS 대시보드</a>
   <a class="nav-link" href="/frame">액자 모드</a>
  </nav>
  <div class="brand">
   <img src="/casaos-icon.svg" alt="" width="50" height="50">
   <div><p class="kicker uppercase tracking-widest">MacBook · Audio</p><h1>웹 리모컨</h1></div>
  </div>
 </header>
 <form class="panel glass backdrop-blur-md bg-white/10 border border-white/15 rounded-2xl form-stack" id="login">
  <label for="key">리모컨 키</label>
  <input id="key" type="password" required autocomplete="off" spellcheck="false">
  <div><button class="primary" type="submit">연결</button></div>
 </form>
 <p id="notice" class="notice" role="status"></p>
 <div id="dashboard" class="grid-remote" hidden>
  <section class="panel glass backdrop-blur-md bg-white/10 border border-white/15 rounded-2xl wide" aria-labelledby="remote-song">
   <div class="row split"><p class="kicker uppercase tracking-widest">Now Playing</p><small id="remote-state">연결 중</small></div>
   <h3 id="remote-song" class="track">—</h3><p id="remote-meta" class="metadata"></p>
   <div class="row controls" aria-label="재생 제어">
    <button type="button" data-action="previous">이전 곡</button>
    <button type="button" data-action="play" class="primary" id="play">재생</button>
    <button type="button" data-action="next">다음 곡</button>
   </div>
   <div class="row split"><label for="volume">볼륨</label><output id="volume-value" for="volume">—</output></div>
   <input id="volume" type="range" min="0" max="100" value="0" disabled>
   <section class="queue" aria-labelledby="queue-title">
    <div class="row split"><h2 id="queue-title">재생 대기열</h2><small id="queue-state">대기열 확인 중</small></div>
    <ol class="queue-list" id="queue-list"></ol>
    <div class="upload-help">
     <p>새 음원 추가는 CasaOS Files 앱(<code>/DATA/Media/Music</code>)에 드래그 앤 드롭하세요.</p>
     <div class="row">
      <button type="button" id="mpd-update">MPD 음원 DB 새로고침</button>
      <small id="mpd-update-result" role="status"></small>
     </div>
    </div>
   </section>
  </section>
  <section class="panel glass backdrop-blur-md bg-white/10 border border-white/15 rounded-2xl">
   <h2>음성 메모</h2>
   <form class="form-stack" id="memo-form">
    <textarea id="memo" aria-label="새 메모" rows="2" maxlength="500" required placeholder="예: 내일 광케이블 주문"></textarea>
    <div class="row"><button type="submit" class="primary">메모 추가</button><button id="memo-refresh" type="button">새로고침</button></div>
   </form>
   <ul class="memo-list" id="memos"></ul>
  </section>
  <section class="panel glass backdrop-blur-md bg-white/10 border border-white/15 rounded-2xl">
   <h2>맥북 상태</h2>
   <dl class="stats">
    <div><dt>CPU 현재 클럭</dt><dd id="cpu-mhz">—</dd></div>
    <div><dt>CPU 온도</dt><dd id="cpu-temp">—</dd></div>
    <div><dt>쿨링팬</dt><dd id="fans">—</dd></div>
    <div><dt>RAM</dt><dd id="ram">—</dd></div>
    <div><dt id="disk-label">SSD 파일시스템</dt><dd id="disk">—</dd></div>
   </dl>
  </section>
  <section class="panel glass backdrop-blur-md bg-white/10 border border-white/15 rounded-2xl wide">
   <h2>전원 및 화면</h2>
   <div class="row">
    <button type="button" class="danger" id="sleep">절전 모드 진입 (WOL 대기)</button>
    <button type="button" id="backlight" disabled>화면 백라이트 토글</button>
    <small id="backlight-state">장치 확인 중</small>
   </div>
  </section>
  <div><button id="logout" type="button">리모컨 잠그기</button></div>
 </div>
 <dialog id="confirm" aria-labelledby="confirm-title" aria-describedby="confirm-text">
  <h2 id="confirm-title"></h2><p id="confirm-text"></p>
  <div class="row">
   <button type="button" id="confirm-cancel" autofocus>취소</button>
   <button type="button" id="confirm-yes" class="primary">확인</button>
  </div>
 </dialog>
</main>
<main class="frame">
 <div class="frame-shell">
  <header><p class="kicker uppercase tracking-widest">MacBook · Listening Room</p></header>
  <div class="frame-grid">
   <section>
    <div class="clock" id="clock">--:--</div><p class="frame-date" id="date">—</p>
    <div class="weather"><p id="weather">날씨 확인 중</p><small id="weather-note">Open-Meteo</small></div>
   </section>
   <section class="frame-music glass backdrop-blur-md bg-white/10 border border-white/15 rounded-2xl">
    <div class="row split"><p class="kicker uppercase tracking-widest">Now Playing</p><small id="frame-state">연결 중</small></div>
    <h1 class="track" id="frame-song">—</h1><p class="metadata" id="frame-meta"></p>
    <div class="wave" id="wave" aria-hidden="true"></div><small id="wave-note">PCM 입력 대기</small>
   </section>
  </div>
  <footer class="frame-foot row split"><a href="/">웹 리모컨</a><button type="button" id="fullscreen">전체 화면</button></footer>
 </div>
</main>
<script nonce="__NONCE__">
"use strict";
const $ = id => document.getElementById(id);
const isFrame = document.documentElement.dataset.view === "frame";
$("casaos-link").href = `http://${window.location.hostname}:80`;
const stateText = {play:"재생 중",pause:"일시 정지",stop:"정지"};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function api(path, {method="GET", body, key=""} = {}) {
  const headers = {Accept:"application/json"};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["X-Remote-Key"] = key;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 9500);
  try {
    const response = await fetch(path, {method,headers,body:JSON.stringify(body),
      cache:"no-store",signal:controller.signal,redirect:"error"});
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === "string" ? data.detail : "입력값을 확인해 주세요.");
      error.status = response.status;
      throw error;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function repeat(task, ms) {
  const run = async () => {
    try { if (!document.hidden) await task(); }
    finally { setTimeout(run, ms); }
  };
  run();
}

function track(prefix, music, error) {
  $(prefix+"-state").textContent = error || stateText[music?.state] || "—";
  $(prefix+"-song").textContent = music?.title || "재생할 음악을 기다리는 중";
  $(prefix+"-meta").textContent = [music?.artist,music?.album].filter(Boolean).join(" · ");
}

if (isFrame) {
  document.title = "맥북 오디오 · Now Playing";
  let playing = false;
  const timeFormat = new Intl.DateTimeFormat("ko-KR",{hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
  const dateFormat = new Intl.DateTimeFormat("ko-KR",{month:"long",day:"numeric",weekday:"long"});
  const bars = Array.from({length:32},() => $("wave").appendChild(document.createElement("span")));
  function clock() {
    const now = new Date();
    $("clock").textContent = timeFormat.format(now).replace(/\s/g,"");
    $("date").textContent = dateFormat.format(now);
  }
  function weatherLabel(code) {
    if (code === 0) return "맑음";
    if (code <= 2) return "구름 조금";
    if (code === 3) return "흐림";
    if (code <= 48) return "안개";
    if (code <= 57) return "이슬비";
    if (code <= 67) return "비";
    if (code <= 77) return "눈";
    if (code <= 82) return "소나기";
    if (code <= 86) return "눈 소나기";
    return "뇌우";
  }
  async function updateWeather() {
    try {
      const w = await api("/api/weather");
      $("weather").textContent = w.available
        ? w.city+" · "+Math.round(w.temperature)+"°C · "+weatherLabel(w.code)
        : w.city+" · 날씨 정보 없음";
      const time = w.observed?.replace("T"," ");
      $("weather-note").textContent = [w.stale ? "이전 관측" : "",time,"Open-Meteo"].filter(Boolean).join(" · ");
    } catch {
      $("weather-note").textContent = "날씨 연결 실패 · Open-Meteo";
    }
  }
  function draw(values) {
    bars.forEach((bar,index) => {
      bar.style.transform = "scaleY("+Math.max(.035,Math.sqrt(values[index] || 0))+")";
    });
    const label = values.length ? "MPD · 실시간 파형" : (playing ? "PCM 입력 대기" : "재생 대기");
    if ($("wave-note").textContent !== label) $("wave-note").textContent = label;
  }
  repeat(clock,1000);
  repeat(updateWeather,60000);
  repeat(async () => {
    try {
      const music = await api("/api/playing");
      playing = music.state === "play";
      track("frame",music);
    } catch (error) {
      playing = false;
      track("frame",null,error.status ? error.message : "서버 연결 대기");
    }
    if (!playing) draw([]);
  },2000);
  repeat(async () => {
    if (!playing) return;
    try {
      const data = await api("/api/visualizer");
      if (playing) draw(data.bars);
    } catch { draw([]); }
  },125);
  if (!document.fullscreenEnabled) $("fullscreen").hidden = true;
  $("fullscreen").onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch { $("fullscreen").textContent = "브라우저 전체 화면을 사용하세요"; }
  };
  document.addEventListener("fullscreenchange",() => {
    $("fullscreen").textContent = document.fullscreenElement ? "전체 화면 해제" : "전체 화면";
  });
} else {
  let key = "";
  let epoch = 0;
  let musicBusy = false;
  let backlightBusy = false;
  let musicVersion = 0;
  let backlightVersion = 0;
  let memoVersion = 0;
  let queueVersion = 0;
  let queueSignature = "";
  let editingVolume = false;
  let connected = false;
  const musicButtons = [...document.querySelectorAll("[data-action]")];
  const metric = (value,unit) => value == null ? "—" : value+" "+unit;
  const message = text => { $("notice").textContent = text; };
  const explain = error => error.status ? error.message : "서버에 연결할 수 없습니다.";
  const call = (path,method,body) => api(path,{method,body,key});

  function lock() {
    epoch++;
    key = "";
    connected = false;
    musicBusy = false;
    backlightBusy = false;
    editingVolume = false;
    try { sessionStorage.removeItem("macbookRemoteKey"); } catch {}
    $("memos").replaceChildren();
    $("memo").value = "";
    $("queue-list").replaceChildren();
    $("queue-state").textContent = "대기열 확인 중";
    $("mpd-update-result").textContent = "";
    $("mpd-update").disabled = false;
    queueSignature = "";
    $("dashboard").hidden = true;
    $("sleep").disabled = false;
    $("login").hidden = false;
    $("key").value = "";
  }
  function showError(error) {
    if (error.status === 401) lock();
    message(explain(error));
  }
  function remoteMusic(music,error) {
    track("remote",music,error);
    musicButtons.forEach(button => { button.disabled = !music; });
    const playing = music?.state === "play";
    $("play").dataset.action = playing ? "pause" : "play";
    $("play").textContent = playing ? "일시 정지" : "재생";
    $("volume").disabled = music?.volume == null;
    if (!editingVolume) {
      $("volume").value = music?.volume ?? 0;
      $("volume-value").textContent = metric(music?.volume,"%");
    }
  }
  function backlight(data) {
    $("backlight").disabled = backlightBusy || !data.ready;
    $("backlight-state").textContent = data.ready
      ? "화면 "+(data.on ? "켜짐" : "꺼짐")
      : data.device ? "쓰기 권한 확인 필요" : "백라이트 장치 설정 필요";
  }
  async function refreshMusic() {
    if (!connected || musicBusy) return;
    const version = musicVersion, session = epoch;
    try {
      const data = await call("/api/remote");
      if (session === epoch && version === musicVersion) remoteMusic(data.music,data.music_error);
    } catch (error) {
      if (session === epoch) showError(error);
    }
  }
  async function refreshQueue() {
    if (!connected) return;
    const session = epoch, version = ++queueVersion;
    try {
      const queue = await call("/api/mpd/queue");
      if (session !== epoch || version !== queueVersion) return;
      const summary = queue.total
        ? (queue.items.length < queue.total ? queue.total+"곡 중 "+queue.items.length+"곡 표시" : queue.total+"곡")
        : "대기열이 비어 있습니다.";
      $("queue-state").textContent = queue.updating_db ? summary+" · 음원 DB 스캔 중" : summary;
      const signature = JSON.stringify(queue.items);
      if (signature === queueSignature) return;
      queueSignature = signature;
      const fragment = document.createDocumentFragment();
      for (const song of queue.items) {
        const row = document.createElement("li");
        const position = document.createElement("span");
        const title = document.createElement("span");
        position.className = "queue-number";
        position.textContent = String(song.position).padStart(2,"0");
        title.textContent = song.title || "제목 없음";
        if (song.selected) row.setAttribute("aria-current","true");
        row.append(position,title);
        if (song.artist) {
          const artist = document.createElement("small");
          artist.textContent = song.artist;
          row.append(artist);
        }
        fragment.append(row);
      }
      $("queue-list").replaceChildren(fragment);
    } catch (error) {
      if (session !== epoch || version !== queueVersion) return;
      if (error.status === 401) { showError(error); return; }
      $("queue-state").textContent = explain(error);
      $("queue-list").replaceChildren();
      queueSignature = "";
    }
  }
  async function refreshSystem() {
    if (!connected) return;
    const session = epoch, version = backlightVersion;
    try {
      const data = await call("/api/system");
      if (session !== epoch) return;
      $("cpu-mhz").textContent = metric(data.cpu_mhz,"MHz");
      $("cpu-temp").textContent = metric(data.cpu_temp,"°C");
      $("cpu-temp").title = data.cpu_temp_source || "";
      $("fans").textContent = data.fans.map(fan => metric(fan.rpm,"RPM")).join(" / ") || "—";
      $("ram").textContent = metric(data.ram_percent,"%");
      $("disk").textContent = metric(data.disk_percent,"%");
      $("disk-label").textContent = "SSD 파일시스템 "+data.disk_path;
      if (version === backlightVersion) backlight(data.backlight);
    } catch (error) {
      if (session === epoch) showError(error);
    }
  }
  async function refreshMemos() {
    const version = ++memoVersion, session = epoch;
    try {
      const data = await call("/api/memos");
      if (version !== memoVersion || session !== epoch) return;
      const fragment = document.createDocumentFragment();
      for (const item of data.items) {
        const row = document.createElement("li");
        const body = document.createElement("div");
        const content = document.createElement("p");
        const stamp = document.createElement("small");
        const when = new Date(item.time);
        const remove = document.createElement("button");
        content.textContent = item.content;
        stamp.textContent = Number.isNaN(when.valueOf()) ? item.time : when.toLocaleString("ko-KR");
        remove.type = "button";
        remove.textContent = "삭제";
        remove.className = "danger";
        remove.onclick = () => confirmAction("메모 삭제","이 메모를 삭제할까요?",async () => {
          try {
            await call("/api/memos/"+item.index+"/delete","POST",{revision:data.revision});
            if (session === epoch) await refreshMemos();
          } catch (error) {
            if (session !== epoch) return;
            showError(error);
            if (error.status === 409) await refreshMemos();
          }
        });
        body.append(content,stamp);
        row.append(body,remove);
        fragment.append(row);
      }
      if (!data.items.length) {
        const empty = document.createElement("li");
        empty.className = "muted";
        empty.textContent = "저장된 메모가 없습니다.";
        fragment.append(empty);
      }
      $("memos").replaceChildren(fragment);
    } catch (error) {
      if (session === epoch) showError(error);
    }
  }
  async function login(value) {
    if (!value) return;
    const session = ++epoch;
    try {
      const data = await api("/api/remote",{key:value});
      if (session !== epoch) return;
      key = value;
      connected = true;
      try { sessionStorage.setItem("macbookRemoteKey",key); } catch {}
      $("key").value = "";
      $("login").hidden = true;
      $("dashboard").hidden = false;
      message("");
      remoteMusic(data.music,data.music_error);
      await Promise.all([refreshSystem(),refreshMemos(),refreshQueue()]);
    } catch (error) {
      if (session === epoch) showError(error);
    }
  }
  async function changeMusic(action,volume) {
    if (!connected || musicBusy) return;
    const session = epoch;
    ++musicVersion;
    musicBusy = true;
    musicButtons.forEach(button => { button.disabled = true; });
    $("volume").disabled = true;
    try {
      const music = await call("/api/music","POST",{action,volume});
      if (session === epoch) {
        message("");
        remoteMusic(music);
        if (action === "previous" || action === "next") refreshQueue();
      }
    } catch (error) {
      if (session === epoch) {
        showError(error);
        musicBusy = false;
        await refreshMusic();
      }
    } finally {
      if (session === epoch) musicBusy = false;
    }
  }
  const modal = $("confirm");
  let pending = null;
  function confirmAction(title,text,fn) {
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    pending = fn;
    modal.showModal();
  }
  modal.addEventListener("cancel",() => { pending = null; });
  $("confirm-cancel").onclick = () => {
    pending = null;
    modal.close();
  };
  $("confirm-yes").onclick = () => {
    const action = pending;
    pending = null;
    modal.close();
    if (action) action();
  };
  async function power(type,button) {
    const session = epoch;
    button.disabled = true;
    if (type === "backlight") {
      backlightBusy = true;
      backlightVersion++;
    }
    try {
      const result = await call("/api/power/"+type,"POST");
      if (session !== epoch) return;
      if (type === "suspend") message("절전 요청을 보냈습니다. 깨어난 뒤 다시 연결됩니다.");
      else {
        backlightBusy = false;
        backlightVersion++;
        backlight(result);
        message("");
      }
    } catch (error) {
      if (session !== epoch) return;
      if (type === "suspend" && !error.status) message("응답이 끊겼습니다. 맥북이 절전에 들어갔는지 확인해 주세요.");
      else showError(error);
      if (type === "backlight") {
        backlightBusy = false;
        backlightVersion++;
        await refreshSystem();
      }
    } finally {
      if (type === "suspend" && session === epoch) {
        await sleep(1500);
        if (session === epoch) button.disabled = false;
      }
    }
  }
  $("login").onsubmit = event => { event.preventDefault(); login($("key").value.trim()); };
  $("logout").onclick = () => { lock(); message(""); };
  $("sleep").onclick = () => confirmAction("절전 모드 진입","재생과 웹 연결이 중단됩니다. 맥북을 깨운 뒤 다시 연결하세요.",() => power("suspend",$("sleep")));
  $("backlight").onclick = () => confirmAction("화면 백라이트 토글","맥북 화면의 백라이트 상태를 바꿀까요?",() => power("backlight",$("backlight")));
  $("mpd-update").onclick = async () => {
    const session = epoch;
    const button = $("mpd-update"), result = $("mpd-update-result");
    button.disabled = true;
    result.textContent = "스캔 요청 중…";
    try {
      const scan = await call("/api/mpd/update","POST");
      if (session !== epoch) return;
      result.textContent = scan.job_id
        ? "스캔 요청 접수 · 작업 #"+scan.job_id
        : "스캔 요청 접수";
      await refreshQueue();
    } catch (error) {
      if (session !== epoch) return;
      if (error.status === 401) showError(error);
      else result.textContent = explain(error);
    } finally {
      if (session === epoch) button.disabled = false;
    }
  };
  $("memo-refresh").onclick = refreshMemos;
  $("memo-form").onsubmit = async event => {
    event.preventDefault();
    const session = epoch, submit = event.submitter;
    const input = $("memo"), content = input.value.trim();
    if (!content) { message("메모 내용을 입력해 주세요."); return; }
    if (submit) submit.disabled = true;
    try {
      await call("/api/memos","POST",{content});
      if (session === epoch) {
        if (input.value.trim() === content) input.value = "";
        message("");
        await refreshMemos();
      }
    } catch (error) {
      if (session === epoch) showError(error);
    } finally { if (submit) submit.disabled = false; }
  };
  musicButtons.forEach(button => { button.onclick = () => changeMusic(button.dataset.action); });
  $("volume").oninput = () => {
    editingVolume = true;
    $("volume-value").textContent = metric($("volume").value,"%");
  };
  $("volume").onchange = () => {
    editingVolume = false;
    changeMusic("volume",Number($("volume").value));
  };
  $("volume").onpointercancel = () => { editingVolume = false; refreshMusic(); };
  repeat(refreshMusic,2500);
  repeat(refreshQueue,10000);
  repeat(refreshSystem,10000);
  repeat(async () => { if (connected) await refreshMemos(); },15000);
  try { login(sessionStorage.getItem("macbookRemoteKey")); } catch {}
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    uvicorn.run(app, host=REMOTE_HOST, port=REMOTE_PORT, workers=1,
                access_log=False, timeout_keep_alive=5)
