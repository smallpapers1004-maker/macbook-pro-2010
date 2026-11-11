"""YouTube search, authenticated library lookup, and one managed mpv player."""

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import random
import re
import shutil
import sys
from tempfile import NamedTemporaryFile

from requests import Session
from ytmusicapi import YTMusic


LOG = logging.getLogger(__name__)
AUTH_PATH = Path("/opt/macbook-assistant/browser.json")
IPC_PATH = Path("/tmp/mpv-yt.sock")
QUEUE_PATH = Path("/tmp/ytmusic_queue.m3u")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
PLAYER_ERRORS = (OSError, RuntimeError, ValueError)


class LibrarySession(Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", (4, 8))
        return super().request(*args, **kwargs)


def library_result(action, playlist_name):
    with LibrarySession() as session:
        ytmusic = YTMusic(str(AUTH_PATH), language="ko", requests_session=session)
        if action == "play_liked":
            return "좋아요 표시한 음악", ytmusic.get_liked_songs(limit=100)["tracks"]
        playlists = ytmusic.get_library_playlists(limit=25 if action == "list_playlists" else 50)
        if action == "list_playlists":
            names = [item["title"] for item in playlists if item.get("title")]
            return "내 재생목록. " + ", ".join(names) if names else "저장된 재생목록이 없습니다."
        needle = playlist_name.casefold()
        matches = [item for item in playlists
                   if item.get("playlistId") and needle in (item.get("title") or "").casefold()]
        exact = [item for item in matches if item["title"].casefold() == needle]
        matches = exact or matches
        if not matches:
            return "해당 이름의 유튜브 뮤직 재생목록을 찾지 못했습니다."
        if len(matches) > 1:
            names = ", ".join(item["title"] for item in matches[:5])
            return f"일치하는 재생목록이 여러 개입니다. {names}. 목록을 하나 지정해 주세요."
        item = matches[0]
        return item["title"], ytmusic.get_playlist(item["playlistId"], limit=100)["tracks"]


class YouTubeMusic:
    def __init__(self, config):
        self.config = config
        self.process = None
        self.ducked = False
        self.queue_owned = False
        self.lock = asyncio.Lock()

    def playing_process(self):
        return self.process is not None and self.process.returncode is None

    async def ipc(self, *command):
        async with asyncio.timeout(1.2):
            reader, writer = await asyncio.open_unix_connection(str(IPC_PATH))
            try:
                data = json.dumps({"command": list(command), "request_id": 1})
                writer.write(data.encode("utf-8") + b"\n")
                await writer.drain()
                while line := await reader.readline():
                    reply = json.loads(line)
                    if reply.get("request_id") == 1:
                        if reply.get("error") != "success":
                            raise RuntimeError(f"mpv IPC {command[0]} failed")
                        return reply.get("data")
                raise ConnectionError("mpv closed the IPC connection")
            finally:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()

    async def _stop(self):
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            with suppress(*PLAYER_ERRORS):
                await self.ipc("quit")
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        self.process = None
        IPC_PATH.unlink(missing_ok=True)

    async def _start(self, source, *, playlist=False):
        env = os.environ.copy()
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", os.defpath)
        extractor = shutil.which("yt-dlp", path=env["PATH"])
        if not extractor:
            raise FileNotFoundError("yt-dlp")
        await self._stop()
        if IPC_PATH.exists() or IPC_PATH.is_symlink():
            raise RuntimeError("mpv IPC path is already in use")
        volume = 30 if self.ducked else 100
        command = [
            "mpv", "--no-config", "--no-video", "--ytdl=yes", "--ytdl-format=bestaudio/best",
            f"--script-opts=ytdl_hook-ytdl_path={extractor}",
            f"--ytdl-raw-options=ignore-config=,js-runtimes={self.config.ytdlp_js_runtime},socket-timeout=8,retries=1",
            "--ao=alsa", f"--audio-device=alsa/{self.config.alsa_pcm}",
            f"--input-ipc-server={IPC_PATH}", f"--volume={volume}",
            "--input-terminal=no", "--msg-level=all=warn",
        ]
        command.extend([f"--playlist={source}"] if playlist else ["--", source])
        self.process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, env=env,
        )
        try:
            async with asyncio.timeout(3):
                while self.playing_process():
                    try:
                        await self.ipc("get_property", "pause")
                    except (FileNotFoundError, ConnectionRefusedError):
                        await asyncio.sleep(0.05)
                    else:
                        return
                raise RuntimeError("mpv exited before IPC was ready")
        except BaseException:
            await self._stop()
            raise

    async def set_ducked(self, enabled):
        async with self.lock:
            if self.ducked == enabled:
                return
            self.ducked = enabled
            for _ in range(2):
                if not self.playing_process():
                    return
                try:
                    await self.ipc("set_property", "volume", 30 if enabled else 100)
                    return
                except PLAYER_ERRORS as error:
                    LOG.warning("YouTube volume update failed (%s)", type(error).__name__)

    async def close(self):
        async with self.lock:
            await self._stop()
            if self.queue_owned:
                QUEUE_PATH.unlink(missing_ok=True)
                self.queue_owned = False

    async def control_youtube_music(self, action: str, query: str = "") -> str:
        """Play a YouTube search or control the YouTube player, including library queues.

        Args:
            action: One of play, pause, resume, next, prev, stop.
            query: YouTube search text for play only.
        """
        action, query = action.strip().lower(), " ".join(query.split())
        controls = {
            "pause": (("set_property", "pause", True), "유튜브 음악을 일시 정지했습니다."),
            "resume": (("set_property", "pause", False), "유튜브 음악을 다시 재생합니다."),
            "next": (("playlist-next", "weak"), "유튜브 다음 곡으로 이동을 요청했습니다."),
            "prev": (("playlist-prev", "weak"), "유튜브 이전 곡으로 이동을 요청했습니다."),
        }
        if action not in {"play", "stop", *controls}:
            return "지원하지 않는 유튜브 음악 명령입니다."
        if action == "play" and not 1 <= len(query) <= 200:
            return "유튜브에서 찾을 곡이나 가수 이름을 200자 이내로 알려 주세요."
        try:
            async with self.lock:
                if action == "play":
                    await self._start(f"ytdl://ytsearch10:{query}")
                    return f"유튜브에서 {query} 검색 결과 재생을 요청했습니다."
                if not self.playing_process():
                    return "유튜브 플레이어에 재생 중인 대기열이 없습니다."
                if action == "stop":
                    await self._stop()
                    return "유튜브 음악을 종료했습니다."
                command, reply = controls[action]
                await self.ipc(*command)
                return reply
        except PLAYER_ERRORS as error:
            LOG.warning("YouTube player request failed (%s)", type(error).__name__)
            return "유튜브 재생 요청을 완료하지 못했습니다. 플레이어 상태를 확인해 주세요."

    async def play_my_ytmusic_library(
        self, action: str = "play_liked", playlist_name: str = "", shuffle: bool = True,
    ) -> str:
        """List or play the user's authenticated YouTube Music library.

        Args:
            action: One of list_playlists, play_liked, play_playlist.
            playlist_name: Full or partial playlist name for play_playlist.
            shuffle: Shuffle tracks before playing. Defaults to true.
        """
        action, playlist_name = action.strip().lower(), playlist_name.strip()
        if action not in {"list_playlists", "play_liked", "play_playlist"}:
            return "지원하지 않는 유튜브 뮤직 보관함 명령입니다."
        if action == "play_playlist" and not 1 <= len(playlist_name) <= 200:
            return "재생할 내 플레이리스트 이름을 알려 주세요."
        if not AUTH_PATH.is_file():
            return "유튜브 뮤직 보관함을 사용하려면 맥북에 browser.json 인증 파일을 설정해 주세요."
        try:
            result = await asyncio.to_thread(library_result, action, playlist_name)
        except Exception as error:
            # Library exceptions can contain account or request details.
            LOG.warning("YouTube Music library request failed (%s)", type(error).__name__)
            return "유튜브 뮤직 보관함을 읽지 못했습니다. 인증 파일과 연결 상태를 확인해 주세요."
        if isinstance(result, str):
            return result
        title, tracks = result
        ids = []
        for track in tracks:
            video_id = track.get("videoId")
            if track.get("isAvailable") is not False and isinstance(video_id, str) and VIDEO_ID.fullmatch(video_id):
                ids.append(video_id)
        if not ids:
            return f"{title}에서 재생 가능한 트랙을 찾지 못했습니다."
        if shuffle:
            random.shuffle(ids)
        try:
            async with self.lock:
                self._write_queue(ids)
                await self._start(str(QUEUE_PATH), playlist=True)
        except PLAYER_ERRORS as error:
            LOG.warning("YouTube Music queue failed (%s)", type(error).__name__)
            return "보관함을 읽었지만 유튜브 플레이어를 시작하지 못했습니다."
        return f"{title}의 {len(ids)}곡을 유튜브 재생 대기열로 보냈습니다."

    def _write_queue(self, ids):
        temp_path = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=QUEUE_PATH.parent,
                                    prefix=".ytmusic-", delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write("#EXTM3U\n")
                for video_id in ids:
                    temp.write(f"https://www.youtube.com/watch?v={video_id}\n")
            os.replace(temp_path, QUEUE_PATH)
            self.queue_owned = True
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
