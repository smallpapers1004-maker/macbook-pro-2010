#!/usr/bin/python3 -I
"""Root-only, fixed-command helper for the MacBook MPD output toggle."""

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

CONFIG = Path("/etc/mpd.conf")
LOCK = Path("/run/macbook-mpd-output.lock")
SYSTEMCTL = "/usr/bin/systemctl"
OUTPUT_NAME = b"MacBook switchable output"
DEVICES = {"speaker": b"0", "optical": b"1"}
OUTPUT_BLOCK = re.compile(
    rb"^[ \t]*audio_output[ \t]+\{[ \t]*(?:#[^\r\n]*)?\r?\n"
    rb"(?P<body>.*?)^[ \t]*\}[ \t]*(?:#[^\r\n]*)?(?=\r?$)",
    re.M | re.S,
)
DEVICE = re.compile(rb"(?:(?:plug)?hw:)[^\s\"]+")
DEV_NUMBER = re.compile(rb"(?:[:,])DEV=([01])(?=,|$)")


class SwitchError(Exception):
    pass


def fields(block, key):
    return list(re.finditer(
        rb'^[ \t]*' + key + rb'[ \t]+"([^"\r\n]*)"[ \t]*(?:#[^\r\n]*)?(?=\r?$)',
        block, re.M,
    ))


def device_setting(contents):
    named = []
    candidates = []
    for block in OUTPUT_BLOCK.finditer(contents):
        body = block["body"]
        names = fields(body, b"name")
        types = fields(body, b"type")
        devices = fields(body, b"device")
        marked = any(match[1] == OUTPUT_NAME for match in names)
        match = None
        if len(types) == 1 and types[0][1] == b"alsa" and len(devices) == 1:
            pcm = devices[0][1]
            numbers = list(DEV_NUMBER.finditer(pcm))
            if DEVICE.fullmatch(pcm) and len(numbers) == 1 and len(re.findall(rb"(?:[:,])DEV=", pcm)) == 1:
                number = numbers[0]
                offset = block.start("body") + devices[0].start(1)
                match = (number[1], offset + number.start(1))
                candidates.append(match)
        if marked:
            named.append(match)
    chosen = named if named else candidates
    if len(chosen) != 1 or chosen[0] is None:
        raise SwitchError("configuration")
    return chosen[0]


def read_config():
    fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise SwitchError("permissions")
        return source.read(), metadata


def replace_config(contents, metadata, expected):
    temporary = None
    try:
        with NamedTemporaryFile(dir=CONFIG.parent, prefix=".mpd-output-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        current, current_metadata = read_config()
        if current != expected or (current_metadata.st_uid, current_metadata.st_gid, current_metadata.st_mode) != (
            metadata.st_uid, metadata.st_gid, metadata.st_mode
        ):
            raise SwitchError("config_changed")
        os.replace(temporary, CONFIG)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restart_mpd():
    subprocess.run([SYSTEMCTL, "restart", "mpd"], check=True, timeout=20,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


def handle(action):
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SwitchError("busy") from None
        original, metadata = read_config()
        digit, offset = device_setting(original)
        current = next(output for output, value in DEVICES.items() if value == digit)
        if action in {"status", current}:
            return {"output": current, "changed": False}

        updated = original[:offset] + DEVICES[action] + original[offset + 1:]
        replace_config(updated, metadata, original)
        try:
            restart_mpd()
        except (OSError, subprocess.SubprocessError):
            try:
                replace_config(original, metadata, updated)
                restart_mpd()
            except (OSError, subprocess.SubprocessError, SwitchError):
                raise SwitchError("recovery_failed") from None
            raise SwitchError("rolled_back") from None
        return {"output": action, "changed": True}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"status", *DEVICES}:
        return 2
    try:
        print(json.dumps(handle(sys.argv[1])))
    except SwitchError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    except OSError:
        print(json.dumps({"error": "permissions"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
