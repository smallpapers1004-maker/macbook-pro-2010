#!/usr/bin/env python3
"""DietPi의 MacBookPro7,1용 CPU 거버너 데몬 (Python 3.9+, 표준 라이브러리).

읽기 전용 사전 점검
    sudo python3 smc_boost_manager.py --check
읽기 전용 연속 모니터링 (종료 Ctrl+C)
    sudo python3 smc_boost_manager.py --dry-run --verbose
실제 적용 (MacBookPro7,1 + Intel Penryn에서만 허용)
    sudo python3 smc_boost_manager.py --apply

읽기 전용 모드에서 모듈이 없다면 실제 장비에서 다음 명령으로 준비한다.
    sudo modprobe coretemp
    sudo modprobe applesmc
    sudo modprobe msr
CPUFreq에 powersave가 표시되지 않고 모듈로 제공되는 커널이라면
    sudo modprobe cpufreq_powersave

부팅 시 실행하려면 먼저 파일을 설치한다.
    sudo install -m 755 smc_boost_manager.py /usr/local/sbin/smc_boost_manager.py
아래 내용을 /etc/systemd/system/smc-boost-manager.service에 저장한다.

    [Unit]
    Description=MacBookPro7,1 CPU governor and thermal guard
    After=mbpfan.service

    [Service]
    ExecStart=/usr/bin/python3 /usr/local/sbin/smc_boost_manager.py --apply
    Restart=on-failure
    RestartSec=15s

    [Install]
    WantedBy=multi-user.target

    sudo systemctl daemon-reload
    sudo systemctl enable --now smc-boost-manager.service
    journalctl -u smc-boost-manager.service -f
    sudo systemctl disable --now smc-boost-manager.service

최근 5초 전체 CPU 사용률이 65% 이상이고 가장 뜨거운 코어가 72°C
미만이면 부스트한다. CasaOS 앱 관리, Docker, containerd 및 MPD의
cgroup v2 CPU 활동은 로그에 참고용으로 표시한다. 서비스 정보 없이도
전체 CPU 사용률을 기준으로 부스트할 수 있다.

78°C 이상에서는 측정한 주기에 powersave로 복귀한다. 83°C 이상에서는
powersave를 유지하고 CRITICAL 로그를 남긴다. 열 보호가 시작되면 72°C
미만까지 부스트를 차단한다. CPU 사용률이 20% 미만으로 60초 지속되거나
정상 종료하면 powersave를 쓴다. 부스트 적용에 실패하면 재시작 전까지
저전력을 유지한다.

팬 제어는 mbpfan 또는 SMC가 담당한다. mbpfan이 83°C부터 최대 RPM을
요청하도록 하려면 /etc/mbpfan.conf의 [general]에서 max_temp = 83을
설정하고 high_temp가 그보다 낮은지 확인한 뒤 적용한다.
    sudo systemctl restart mbpfan.service

scaling_cur_freq는 드라이버가 보고하는 값이다. bios_limit가 제공되면
함께 출력한다. MSR 0x199는 시작 시 한 번 읽는 P-state 진단값이다.
이 데몬은 펌웨어의 상한과 하드웨어 보호 신호를 그대로 따른다.
"""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import fcntl
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time


LOG = logging.getLogger("smc_boost_manager")
CPU_SYS = Path("/sys/devices/system/cpu")
HWMON = Path("/sys/class/hwmon")
PLATFORM = Path("/sys/devices/platform")
PROC = Path("/proc")
SYSTEM_SLICE = Path("/sys/fs/cgroup/system.slice")
WORKLOAD_SERVICES = {
    "CasaOS": "casaos-app-management.service",
    "Docker": "docker.service",
    "containerd": "containerd.service",
    "MPD": "mpd.service",
}
WORKLOAD_MIN_USEC = 250_000
POLL = 5.0
LOAD_WINDOW = 10.0
MAX_SAMPLE_GAP = 7.5
HOT = 78_000
CRITICAL_TEMP = 83_000
COOL = 72_000
BURST_PERCENT = 65
POWERSAVE = "powersave"
BOOST_GOVERNORS = ("schedutil", "performance")
PERF_CTL = 0x199


def read(path: Path) -> str:
    return path.read_text(encoding="ascii").strip()


def optional(path: Path) -> str:
    try:
        return read(path)
    except (OSError, UnicodeError):
        return ""


def cpu_info() -> list[dict[str, str]]:
    blocks = optional(PROC / "cpuinfo").split("\n\n")
    return [
        dict(tuple(part.strip() for part in line.split(":", 1))
             for line in block.splitlines() if ":" in line)
        for block in blocks if block.strip()
    ]


def is_penryn(cpu: dict[str, str]) -> bool:
    return (cpu.get("vendor_id"), cpu.get("cpu family"), cpu.get("model")) == (
        "GenuineIntel", "6", "23",
    )


def load_module(name: str) -> None:
    modprobe = shutil.which("modprobe") or "/sbin/modprobe"
    try:
        result = subprocess.run(
            [modprobe, name], capture_output=True, text=True, timeout=5,
        )
        if result.returncode:
            LOG.warning("modprobe %s: %s", name, result.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOG.warning("modprobe %s: %s", name, exc)


def preflight(cpus: list[dict[str, str]], apply: bool) -> None:
    loaded = {line.split()[0] for line in optional(PROC / "modules").splitlines()
              if line.split()}
    for name in ("coretemp", "applesmc", "msr"):
        marker = Path("/sys/module") / name
        if apply and name not in loaded and not marker.exists():
            load_module(name)
        present = name in loaded or marker.exists()
        LOG.info("module %s: %s", name, "present" if present else "not listed")

    for cpu in cpus:
        flags = set(cpu.get("flags", "").split())
        number = cpu.get("processor", "")
        if not (number.isdigit() and is_penryn(cpu) and {"msr", "est"} <= flags):
            continue
        try:
            path = Path("/dev/cpu") / number / "msr"
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                value = os.pread(fd, 8, PERF_CTL)
            finally:
                os.close(fd)
            if len(value) != 8:
                raise OSError("short MSR read")
            LOG.info("cpu%s IA32_PERF_CTL read-only: %#018x",
                     number, int.from_bytes(value, "little"))
        except OSError as exc:
            LOG.info("cpu%s IA32_PERF_CTL unavailable: %s", number, exc)


def core_temperature(min_cores: int) -> int | None:
    hwmons = [p.resolve() for p in HWMON.glob("hwmon*")]
    hwmons.extend(p.resolve() for p in PLATFORM.glob("coretemp.*/hwmon/hwmon*"))
    cores = {}
    try:
        for chip in sorted(set(hwmons)):
            if optional(chip / "name") != "coretemp":
                continue
            for sensor in sorted(chip.glob("temp*_input")):
                base = sensor.name.removesuffix("_input")
                label = read(chip / (base + "_label"))
                if not re.fullmatch(r"Core\s+\d+", label):
                    continue
                fault = chip / (base + "_fault")
                value = int(read(sensor))
                if (fault.exists() and int(read(fault))) or not -20_000 <= value <= 150_000:
                    return None
                cores[chip, label] = value
    except (OSError, ValueError):
        return None
    return max(cores.values()) if len(cores) >= min_cores else None


def fan_rpms() -> tuple[int, ...]:
    paths = {p.resolve() for p in PLATFORM.glob("applesmc.*/fan*_input")}
    for chip in HWMON.glob("hwmon*"):
        if optional(chip / "name") == "applesmc":
            paths.update(p.resolve() for p in chip.glob("fan*_input"))
    try:
        return tuple(int(read(path)) for path in sorted(paths))
    except (OSError, ValueError):
        return ()


def healthy_fans(rpms: tuple[int, ...]) -> bool:
    return bool(rpms) and all(rpm > 0 for rpm in rpms)


@dataclass(frozen=True)
class Policy:
    path: Path
    available: frozenset[str]

    @property
    def governor(self) -> Path:
        return self.path / "scaling_governor"

    @property
    def boost_governor(self) -> str:
        return next((name for name in BOOST_GOVERNORS if name in self.available), "")

    def status(self, wanted: str = "") -> str:
        fields = [
            ("cpus", "affected_cpus", ""),
            ("driver", "scaling_driver", ""),
            ("gov", "scaling_governor", ""),
            ("cur", "scaling_cur_freq", " kHz"),
            ("max", "scaling_max_freq", " kHz"),
            ("bios_limit", "bios_limit", " kHz"),
        ]
        data = []
        for label, name, unit in fields:
            value = optional(self.path / name)
            data.append(f"{label}={value + unit if value else '?'}")
        if wanted:
            data.append(f"wanted={wanted}")
        data.append(f"available={','.join(sorted(self.available)) or '?'}")
        return f"{self.path.name}[{', '.join(data)}]"


def policies() -> list[Policy]:
    paths = set((CPU_SYS / "cpufreq").glob("policy[0-9]*"))
    paths.update((p / "cpufreq").resolve() for p in CPU_SYS.glob("cpu[0-9]*")
                 if (p / "cpufreq").is_dir())
    return [
        Policy(path, frozenset(optional(path / "scaling_available_governors").split()))
        for path in sorted(paths)
    ]


def online_cpus_covered(items: list[Policy]) -> bool:
    online = optional(CPU_SYS / "online")
    if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", online):
        return False
    expected = set()
    for group in online.split(","):
        start, _, end = group.partition("-")
        expected.update(range(int(start), int(end or start) + 1))
    covered = set()
    for policy in items:
        covered.update(int(cpu) for cpu in optional(policy.path / "affected_cpus").split()
                       if cpu.isdigit())
    return bool(expected) and expected <= covered


def cpu_ticks() -> tuple[int, int]:
    with (PROC / "stat").open(encoding="ascii") as stream:
        name, *fields = stream.readline().split()
    ticks = [int(value) for value in fields[:8]]
    if name != "cpu" or len(ticks) < 8 or min(ticks) < 0:
        raise ValueError("invalid aggregate CPU accounting")
    total = sum(ticks)
    # iowait is not executing CPU work; guest time is already in user/nice.
    return total - ticks[3] - ticks[4], total


def service_cpu_times() -> dict[str, int]:
    """Optional cgroup v2 CPU counters for workload hints."""
    times = {}
    for label, unit in WORKLOAD_SERVICES.items():
        for line in optional(SYSTEM_SLICE / unit / "cpu.stat").splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == "usage_usec" and fields[1].isdecimal():
                times[label] = int(fields[1])
                break
    return times


@dataclass(frozen=True)
class Load:
    average: float | None = None
    interval: float | None = None
    idle_seconds: float = 0.0
    interrupted: bool = False


class LoadMeter:
    def __init__(self) -> None:
        self.previous: tuple[float, int, int] | None = None
        self.intervals: deque[tuple[float, float, int, int]] = deque()
        self.idle_seconds = 0.0

    def reset(self) -> Load:
        self.previous = None
        self.intervals.clear()
        self.idle_seconds = 0.0
        return Load(interrupted=True)

    def update(self, now: float, busy: int, total: int) -> Load:
        previous = self.previous
        self.previous = (now, busy, total)
        if previous is None:
            return Load()
        start, previous_busy, previous_total = previous
        elapsed = now - start
        busy_delta = busy - previous_busy
        total_delta = total - previous_total
        if not 0 < elapsed <= MAX_SAMPLE_GAP or total_delta <= 0 or not 0 <= busy_delta <= total_delta:
            result = self.reset()
            self.previous = (now, busy, total)
            return result

        interval = 100 * busy_delta / total_delta
        self.idle_seconds = self.idle_seconds + elapsed if interval < 20 else 0.0
        cutoff = now - LOAD_WINDOW
        self.intervals.append((start, now, busy_delta, total_delta))
        while self.intervals[0][1] <= cutoff:
            self.intervals.popleft()
        average = None
        if self.intervals[0][0] <= cutoff:
            busy_sum = total_sum = 0.0
            for begin, end, used, counted in self.intervals:
                weight = (end - max(begin, cutoff)) / (end - begin)
                busy_sum += used * weight
                total_sum += counted * weight
            average = 100 * busy_sum / total_sum
        return Load(average, interval, self.idle_seconds)


class State(Enum):
    LOW = "low"
    BOOST = "boost"
    PROTECT = "protect"
    CRITICAL = "critical"


def decide(previous: State, temp: int | None, fan_ok: bool, load: Load,
           ready: bool) -> tuple[State, str]:
    if temp is not None and temp >= CRITICAL_TEMP:
        return State.CRITICAL, "core >= 83°C; forcing powersave; check mbpfan cooling"
    if temp is not None and temp >= HOT:
        return State.PROTECT, "core >= 78°C; returning to powersave"
    if temp is None:
        return State.PROTECT, "core temperature unavailable"
    if not fan_ok:
        return State.PROTECT, "fan speed unavailable or zero"
    if load.interrupted:
        return State.PROTECT, "CPU load sample interrupted"
    if not ready:
        return State.PROTECT, "CPUFreq policy or required governor unavailable"
    if previous in (State.PROTECT, State.CRITICAL) and temp >= COOL:
        return State.PROTECT, "waiting for core < 72°C"
    if previous is State.BOOST and load.idle_seconds < 60:
        return State.BOOST, "boost active"
    if temp < COOL and load.interval is not None and load.interval >= BURST_PERCENT:
        return State.BOOST, "core < 72°C and 5s CPU >= 65%"
    return State.LOW, "baseline or CPU < 20% for 60s"


def set_governors(items: list[Policy], state: State, apply: bool) -> bool:
    success = bool(items)
    for policy in items:
        wanted = policy.boost_governor if state is State.BOOST else POWERSAVE
        if not wanted or wanted not in policy.available:
            LOG.debug("%s: required governor %s unavailable", policy.path, wanted or "boost")
            success = False
            if state is State.BOOST:
                break
            continue
        if not apply:
            continue
        try:
            if optional(policy.governor) != wanted:
                policy.governor.write_text(wanted + "\n", encoding="ascii")
                if read(policy.governor) != wanted:
                    raise OSError("governor readback mismatch")
        except (OSError, UnicodeError) as exc:
            LOG.error("%s -> %s: %s", policy.governor, wanted, exc)
            success = False
            if state is State.BOOST:
                break
    return success


def run(stopping: Callable[[], bool], apply: bool, check: bool, min_cores: int) -> int:
    meter = LoadMeter()
    previous_service_times: dict[str, int] = {}
    state = State.LOW
    boost_write_failed = False
    previous_status = None
    next_poll = time.monotonic()
    result = 0
    try:
        while not stopping():
            temp = core_temperature(min_cores)
            rpms = fan_rpms()
            try:
                load = meter.update(time.clock_gettime(time.CLOCK_BOOTTIME), *cpu_ticks())
            except (OSError, ValueError) as exc:
                LOG.warning("CPU accounting unavailable: %s", exc)
                load = meter.reset()

            items = policies()
            powersave_ready = bool(items) and online_cpus_covered(items) and all(
                POWERSAVE in policy.available and optional(policy.governor)
                for policy in items
            )
            boost_ready = powersave_ready and all(
                policy.boost_governor for policy in items
            )
            state, reason = decide(state, temp, healthy_fans(rpms), load, boost_ready)
            if boost_write_failed and state in (State.LOW, State.BOOST):
                state, reason = State.PROTECT, "boost locked out after write failure"
            if stopping():
                break
            success = set_governors(items, state, apply)
            if not success and state is State.BOOST:
                boost_write_failed = True
                state, reason = State.PROTECT, "boost failed; returning to powersave"
                success = set_governors(items, state, apply)
            service_times = service_cpu_times()
            workloads = ()
            if load.interval is not None:
                workloads = tuple(
                    label for label, usec in service_times.items()
                    if label in previous_service_times
                    and usec - previous_service_times[label] >= WORKLOAD_MIN_USEC
                )
            previous_service_times = service_times

            status = (state, reason, powersave_ready, boost_ready, success)
            level = logging.DEBUG
            if status != previous_status:
                if state is State.CRITICAL:
                    level = logging.CRITICAL
                else:
                    level = logging.WARNING if state is State.PROTECT or not success else logging.INFO
            previous_status = status
            LOG.log(
                level,
                "%s%s (%s) core=%s fan=%s cpu_10s=%s cpu_sample=%s idle=%.0fs "
                "workload=%s powersave_ready=%s boost_ready=%s %s",
                "" if apply else "[dry-run] ", state.value, reason,
                "?" if temp is None else f"{temp / 1000:.1f}°C",
                rpms or "?", "?" if load.average is None else f"{load.average:.1f}%",
                "?" if load.interval is None else f"{load.interval:.1f}%",
                load.idle_seconds, ",".join(workloads) or "-",
                powersave_ready, boost_ready and not boost_write_failed,
                " ".join(policy.status(policy.boost_governor if state is State.BOOST else POWERSAVE)
                         for policy in items) or "no CPUFreq policies",
            )
            if check:
                return int(temp is None or not healthy_fans(rpms)
                           or not boost_ready or load.interrupted)
            next_poll = max(next_poll + POLL, time.monotonic())
            if not stopping():
                time.sleep(max(0.0, next_poll - time.monotonic()))
    finally:
        if apply:
            result = int(not set_governors(policies(), State.LOW, apply=True))
            if result:
                LOG.error("Could not verify powersave on every policy at exit")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="one read-only preflight and snapshot")
    modes.add_argument("--dry-run", action="store_true", help="read-only loop (default)")
    modes.add_argument("--apply", action="store_true", help="apply governors on MacBookPro7,1")
    parser.add_argument("--verbose", action="store_true", help="log every sample")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    model = optional(Path("/sys/class/dmi/id/product_name"))
    cpus = cpu_info()
    target = model == "MacBookPro7,1" and bool(cpus) and all(is_penryn(cpu) for cpu in cpus)
    LOG.info("model=%s cpu=%s target=%s", model or "?",
             cpus[0].get("model name", "?") if cpus else "?", target)
    if args.apply and (not target or os.geteuid() != 0):
        LOG.error("--apply requires root and a MacBookPro7,1 with Penryn")
        return 2

    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    def stopping() -> bool:
        return stop

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)
    if not args.apply:
        preflight(cpus, apply=False)
        status = run(stopping, apply=False, check=args.check, min_cores=2 if target else 1)
        return 2 if args.check and not target else status

    try:
        lock_fd = os.open("/run/smc_boost_manager.lock",
                          os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        LOG.error("Cannot open daemon lock: %s", exc)
        return 1
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            LOG.error("Cannot acquire daemon lock: %s", exc)
            return 1
        preflight(cpus, apply=True)
        if any(POWERSAVE not in policy.available for policy in policies()):
            load_module("cpufreq_powersave")
        return run(stopping, apply=True, check=False, min_cores=2)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
