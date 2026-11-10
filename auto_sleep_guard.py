#!/usr/bin/env python3
"""Suspend a DietPi server after a continuous run of idle observations."""
import argparse
from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import time


LOG = logging.getLogger("auto-sleep-guard")
CMDLINE = Path("/proc/cmdline")
ALSA = Path("/proc/asound")
NET = Path("/sys/class/net")
POWER = Path("/sys/power")
DOCKER_BRIDGE = "docker0"
DOCKER_SOCKET = Path("/run/docker.sock")
CASAOS_PORTS = {80, 8080, 4533, 8384, 22000}
KERNEL_GUARD_MESSAGE = (
    "/proc/cmdline에 init_on_alloc=0 옵션이 없어 절전 진입을 보류합니다. /etc/default/grub을 패치하세요."
)
RESUME_SIGNAL = {signal.SIGUSR1}
WOL_SUPPORTED = re.compile(r"^\s*Supports Wake-on:\s*([a-z]+)\s*$", re.M)
WOL_CURRENT = re.compile(r"^\s*Wake-on:\s*([a-z]+)\s*$", re.M)
PCM_STATE = re.compile(r"^state:\s*([A-Z]+)\s*$", re.M)
IDLE_PCM_STATES = {"OPEN", "SETUP", "PREPARED", "XRUN", "PAUSED", "SUSPENDED", "DISCONNECTED"}


class GuardError(Exception):
    """A prerequisite could not be verified."""


@dataclass(frozen=True)
class Traffic:
    ifindex: int
    rx: int
    tx: int


@dataclass(frozen=True)
class NetworkSample:
    at: float
    interfaces: dict[str, Traffic]


def kernel_safe():
    value = None
    for token in shlex.split(CMDLINE.read_text()):
        if token == "--":
            break
        name, _, setting = token.partition("=")
        if name == "init_on_alloc":
            value = setting
    return value == "0"


def check_kernel_guard():
    try:
        if kernel_safe():
            return True
    except (OSError, ValueError) as exc:
        LOG.warning("/proc/cmdline 확인 실패: %s", exc)
    LOG.critical(KERNEL_GUARD_MESSAGE)
    return False


def require_deep_sleep():
    if "mem" not in (POWER / "state").read_text().split():
        raise GuardError("커널에서 mem 절전을 지원하지 않습니다")
    if "[deep]" not in (POWER / "mem_sleep").read_text().split():
        raise GuardError("/sys/power/mem_sleep에서 deep이 선택되어야 합니다")


def run(*command, timeout=5, reject_stderr=False):
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or f"종료 코드 {exc.returncode}"
        raise GuardError(f"{command[0]}: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuardError(f"{command[0]}: {exc}") from exc
    if reject_stderr and result.stderr.strip():
        raise GuardError(f"{command[0]}: {result.stderr.strip()}")
    return result.stdout


def playback_active():
    found = False
    for card in ALSA.iterdir():
        if not re.fullmatch(r"card[0-9]+", card.name):
            continue
        for pcm in card.iterdir():
            if not re.fullmatch(r"pcm[0-9]+p", pcm.name):
                continue
            for sub in pcm.iterdir():
                if not re.fullmatch(r"sub[0-9]+", sub.name):
                    continue
                status = sub / "status"
                content = status.read_text().strip()
                found = True
                if content == "closed":
                    continue
                state = PCM_STATE.search(content)
                if state and state[1] in {"RUNNING", "DRAINING"}:
                    return True
                if not state or state[1] not in IDLE_PCM_STATES:
                    raise GuardError(f"ALSA 상태를 해석할 수 없습니다: {status}")
    if not found:
        raise GuardError("ALSA 재생 장치의 status 파일을 찾지 못했습니다")
    return False


def read_traffic(interface):
    link = NET / interface
    ifindex = int((link / "ifindex").read_text())
    stats = link / "statistics"
    rx = int((stats / "rx_bytes").read_text())
    tx = int((stats / "tx_bytes").read_text())
    if ifindex < 1 or min(rx, tx) < 0:
        raise GuardError(f"{interface}: 올바르지 않은 네트워크 카운터 또는 인터페이스 번호")
    return Traffic(ifindex, rx, tx)


def sample_network(interface):
    interfaces = {interface: read_traffic(interface)}
    try:
        (NET / DOCKER_BRIDGE).stat()
    except FileNotFoundError:
        pass
    else:
        interfaces[DOCKER_BRIDGE] = read_traffic(DOCKER_BRIDGE)
    return NetworkSample(time.monotonic(), interfaces)


def published_tcp_port(protected_ports):
    # Docker DNAT 연결은 호스트에 소켓이 없을 수 있다. 첫 dport가 변환 전 공개 포트다.
    for family in ("ipv4", "ipv6"):
        flows = run("conntrack", "-L", "-f", family, "-p", "tcp",
                    "--state", "ESTABLISHED", "--dst-nat")
        for line in flows.splitlines():
            if not line.strip():
                continue
            original_port = re.search(r"\bdport=([0-9]+)\b", line)
            if not original_port:
                raise GuardError("conntrack: 원본 TCP 포트를 확인할 수 없습니다")
            port = int(original_port[1])
            if port in protected_ports:
                return port
    return None


def active_hours_block(options, local_time):
    if options.hours_mode == "off":
        return False
    start, end = options.active_hours
    minute = local_time.hour * 60 + local_time.minute
    if start < end:
        inside = start <= minute < end
    else:
        inside = minute >= start or minute < end
    return inside if options.hours_mode == "block" else not inside


def check_activity(options, previous):
    # 샘플마다 기준값을 갱신한다. 어떤 검사든 실패하면 호출자가 유휴 시간을 버린다.
    sample = sample_network(options.interface)
    if playback_active():
        return sample, "ALSA 재생 중"
    # 일부 ss 버전은 netlink 오류가 있어도 종료 코드 0을 반환한다.
    sockets = run("ss", "-Htn", "state", "established", options.socket_filter, reject_stderr=True)
    if sockets.strip():
        return sample, "SSH/SMB/CasaOS 등 보호 대상 TCP 연결 유지 중"
    if DOCKER_BRIDGE in sample.interfaces or DOCKER_SOCKET.is_socket():
        port = published_tcp_port(options.protected_ports)
        if port is not None:
            return sample, f"Docker 공개 TCP 포트 {port} 연결 유지 중"
    if previous is None:
        return sample, "네트워크 기준값 수집"
    elapsed = sample.at - previous.at
    if sample.interfaces.keys() != previous.interfaces.keys() or not 0 < elapsed < 2 * options.poll_seconds:
        return sample, "네트워크 기준값 재수집"
    for interface, current in sample.interfaces.items():
        old = previous.interfaces[interface]
        rx_delta = current.rx - old.rx
        tx_delta = current.tx - old.tx
        if current.ifindex != old.ifindex or min(rx_delta, tx_delta) < 0:
            return sample, f"{interface} 네트워크 기준값 재수집"
        rate = (rx_delta + tx_delta) / elapsed
        if rate >= options.traffic_bytes_per_second:
            return sample, f"{interface} 트래픽 {rate:,.0f} B/s"
    if active_hours_block(options, datetime.now().astimezone()):
        return sample, "설정한 시간대 정책으로 절전 보류"
    return sample, None


def wol_enabled(interface):
    link = NET / interface
    if (link / "type").read_text().strip() != "1" or not (link / "device").exists():
        raise GuardError(f"{interface}: 물리 유선 이더넷 인터페이스를 지정하세요")
    if (link / "wireless").exists() or (link / "phy80211").exists():
        raise GuardError(f"{interface}: 무선 인터페이스입니다")
    if (link / "carrier").read_text().strip() != "1":
        raise GuardError(f"{interface}: 유선 링크가 연결되어야 합니다")
    output = run("ethtool", interface)
    support = WOL_SUPPORTED.search(output)
    current = WOL_CURRENT.search(output)
    if not support or "g" not in support[1] or not current:
        raise GuardError(f"{interface}: 매직 패킷 WOL 지원 또는 상태를 확인할 수 없습니다")
    try:
        wakeup = (link / "device" / "power" / "wakeup").read_text().strip()
    except FileNotFoundError:
        wakeup = None
    if wakeup not in (None, "enabled", "disabled"):
        raise GuardError(f"{interface}: 커널 장치의 wakeup 상태를 확인할 수 없습니다")
    return "g" in current[1] and wakeup != "disabled"


class ResumeWatcher:
    def __init__(self):
        signal.pthread_sigmask(signal.SIG_BLOCK, RESUME_SIGNAL)
        self.offset = self.clock_offsets()[1]

    @staticmethod
    def clock_offsets():
        # 두 monotonic 읽기로 감싸 측정 도중 스케줄링 지연이 기상으로 오인되지 않게 한다.
        before = time.monotonic()
        boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        return boot - time.monotonic(), boot - before

    def resumed(self, timeout=0):
        # post 훅은 대기를 깨운다. 실제 절전은 커널 시계 차이로 확인한다.
        signal.sigtimedwait(RESUME_SIGNAL, timeout)
        lower, upper = self.clock_offsets()
        slept = lower - self.offset
        if slept < 0.05:
            self.offset = max(lower, min(self.offset, upper))
            return False
        self.offset = upper
        LOG.info("절전 복귀 %s · 약 %.1f초 절전 · 유휴 타이머 초기화",
                 datetime.now().astimezone().isoformat(timespec="seconds"), slept)
        return True


def try_suspend(options, previous, watcher):
    if options.dry_run and not check_kernel_guard():
        return
    require_deep_sleep()
    enabled = wol_enabled(options.interface)
    if options.dry_run:
        LOG.info("[dry-run] 유휴 조건 충족 · WOL %s · 실제 절전 생략",
                 "활성" if enabled else "활성화 필요")
        return
    if not enabled:
        run("ethtool", "-s", options.interface, "wol", "g")
        if not wol_enabled(options.interface):
            raise GuardError("매직 패킷 WOL 활성화 검증 실패")
        LOG.info("%s 매직 패킷 WOL 활성화", options.interface)
    run("sync", timeout=120)
    require_deep_sleep()
    if not wol_enabled(options.interface):
        raise GuardError("절전 직전 매직 패킷 WOL 검증 실패")

    # 최종 구간에도 트래픽을 확인한다. 최소 1초를 재서 아주 짧은 순간의 비율을 피한다.
    remaining = max(0.0, 1.0 - (time.monotonic() - previous.at))
    if watcher.resumed(remaining):
        return
    _, reason = check_activity(options, previous)
    if watcher.resumed():
        return
    if reason:
        raise GuardError(f"최종 점검: {reason}")
    if not check_kernel_guard():
        return
    run("systemctl", "suspend", timeout=30)
    LOG.info("절전 요청 접수 · 복귀는 별도로 기록")


def monitor(options):
    watcher = ResumeWatcher()
    previous = None
    idle_since = None
    last_reason = None
    while True:
        started = time.monotonic()
        if watcher.resumed():
            previous = idle_since = last_reason = None
        try:
            previous, reason = check_activity(options, previous)
        except (GuardError, OSError, ValueError) as exc:
            reason = f"감시 오류: {exc}"
            if reason != last_reason:
                LOG.warning("%s · 절전 보류", reason)
            previous = None

        if watcher.resumed():
            previous = idle_since = last_reason = None
            continue
        if reason is not None:
            idle_since = None
            if reason != last_reason and previous is not None:
                LOG.info("절전 보류 · %s", reason)
        else:
            if idle_since is None:
                idle_since = previous.at
                LOG.info("유휴 측정 시작 · 연속 %d분 후 절전 가능", options.idle_minutes)
            if previous.at - idle_since >= options.idle_minutes * 60:
                try:
                    try_suspend(options, previous, watcher)
                except (GuardError, OSError, ValueError) as exc:
                    LOG.warning("절전 보류 · %s", exc)
                previous = idle_since = None
        last_reason = reason

        remaining = max(0.0, options.poll_seconds - (time.monotonic() - started))
        if watcher.resumed(remaining):
            previous = idle_since = last_reason = None


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수가 필요합니다")
    return number


def parse_hours(value):
    match = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])", value)
    if not match:
        raise argparse.ArgumentTypeError("HH:MM-HH:MM 형식이 필요합니다")
    sh, sm, eh, em = map(int, match.groups())
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        raise argparse.ArgumentTypeError("시작과 종료 시각은 달라야 합니다")
    return start, end


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default=os.environ.get("SLEEP_GUARD_INTERFACE"),
                        help="물리 유선 이더넷 인터페이스")
    parser.add_argument("--idle-minutes", type=positive_int,
                        default=os.environ.get("SLEEP_GUARD_IDLE_MINUTES", "30"))
    parser.add_argument("--poll-seconds", type=positive_int, default=60)
    parser.add_argument("--traffic-bytes-per-second", type=positive_int,
                        default=os.environ.get("SLEEP_GUARD_TRAFFIC_BYTES_PER_SECOND", "50000"))
    parser.add_argument("--hours-mode", choices=("off", "block", "allow-only"),
                        default=os.environ.get("SLEEP_GUARD_HOURS_MODE", "off"))
    parser.add_argument("--active-hours", type=parse_hours,
                        default=os.environ.get("SLEEP_GUARD_ACTIVE_HOURS", "19:00-23:00"))
    parser.add_argument("--tcp-ports", default=os.environ.get("SLEEP_GUARD_TCP_PORTS", "22,445"),
                        help="CasaOS 관련 고정 포트 외에 보호할 서버 쪽 TCP 포트, 쉼표로 구분")
    parser.add_argument("--dry-run", action="store_true", help="감시와 사전 조회만 수행")
    options = parser.parse_args()
    if (not options.interface or options.interface == DOCKER_BRIDGE
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}", options.interface)):
        parser.error("--interface에 유선 인터페이스 이름을 지정하세요 (예: enp2s0)")
    if options.poll_seconds > 60:
        parser.error("--poll-seconds는 1~60이어야 합니다")
    if options.hours_mode not in {"off", "block", "allow-only"}:
        parser.error("SLEEP_GUARD_HOURS_MODE는 off, block, allow-only 중 하나여야 합니다")
    raw_ports = options.tcp_ports.split(",")
    if any(not re.fullmatch(r"[0-9]{1,5}", port.strip()) for port in raw_ports):
        parser.error("--tcp-ports에 숫자 포트를 쉼표로 구분해 입력하세요")
    ports = {int(port) for port in raw_ports}
    if any(not 1 <= port <= 65535 for port in ports):
        parser.error("TCP 포트는 1~65535 범위여야 합니다")
    options.protected_ports = ports | CASAOS_PORTS
    options.socket_filter = "( " + " or ".join(
        f"sport = :{port}" for port in sorted(options.protected_ports)
    ) + " )"
    return options


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    options = parse_args()
    if not check_kernel_guard():
        LOG.warning("GRUB 사용 시 /etc/default/grub의 GRUB_CMDLINE_LINUX_DEFAULT에 "
                    "기존 옵션을 유지하며 init_on_alloc=0 추가 → sudo update-grub → 재부팅")
        if not options.dry_run:
            return 2
    if not options.dry_run and os.geteuid() != 0:
        LOG.error("절전 및 WOL 설정은 root 권한으로 실행하세요")
        return 2
    commands = ("ss", "ethtool") if options.dry_run else ("ss", "ethtool", "sync", "systemctl")
    for command in commands:
        if not shutil.which(command):
            LOG.error("필요한 명령을 찾을 수 없습니다: %s", command)
            return 2
    LOG.info("감시 시작 · %s · %d초 간격 · %d분 유휴 · %d B/s · 시간대 %s%s",
             options.interface, options.poll_seconds, options.idle_minutes,
             options.traffic_bytes_per_second, options.hours_mode,
             " · dry-run" if options.dry_run else "")
    try:
        monitor(options)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
