#!/usr/bin/env bash
# MacBookPro7,1 — DietPi / Debian Bookworm·Trixie (amd64)
# 실행: sudo bash setup_macbook_server.sh [--enable-wifi]
# 앱도 배치하려면 이 스크립트 옆에 macbook-assistant.zip, web_remote.py를 둡니다.
# Debian 하드웨어 참고: https://wiki.debian.org/InstallingDebianOn/Apple/MacBookPro/7-1
# Broadcom wl 참고: https://wiki.debian.org/wl
# CasaOS 공식 설치: https://github.com/IceWhaleTech/CasaOS#quick-setup-casaos
set -Eeuo pipefail
umask 022
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C DEBIAN_FRONTEND=noninteractive

APP=/opt/macbook-assistant
VENV=$APP/.venv
BACKUP=/var/backups/macbook-server-setup
WIFI_POLICY=/etc/modprobe.d/macbook-server-wired-only.conf
WIFI=0
ISSUES=0
STAGE=사전점검
LAN=
LAN_IP=
OPTICAL_PCM=
APP_PCM=default
CASA_READY=0
CASA_PORT=80
REMOTE_READY=0
MUSIC_READY=0
CASA_MUSIC=/DATA/Media/Music
MPD_CONF=/etc/mpd.conf
DEBIAN_MUSIC=/var/lib/mpd/music
DIETPI_MUSIC=/mnt/dietpi_userdata/Music
log() { printf '[설치] %s\n' "$*"; }
warn() { printf '[경고] %s\n' "$*" >&2; }
issue() { warn "$@"; ISSUES=$((ISSUES + 1)); }
die() { printf '[실패] %s\n' "$*" >&2; exit 1; }
trap 'printf "[실패] %s 단계, %s행, 종료 코드 %s\n" "$STAGE" "$LINENO" "$?" >&2' ERR

usage() {
    cat <<'HELP'
사용법: sudo bash setup_macbook_server.sh [--enable-wifi]
  기본값          유선 LAN 사용, 내장 Wi-Fi 차단
  --enable-wifi   BCM4322용 Debian Broadcom wl(DKMS) 설치 및 활성화
  --help          사용법 표시
대상: MacBookPro7,1, GRUB과 systemd를 사용하는 DietPi/Debian 12·13 amd64.
CasaOS가 없으면 공식 설치 프로그램으로 Docker와 CasaOS를 설치합니다.
MPD는 CasaOS의 /DATA/Media/Music과 음원 폴더를 공유합니다.
변경 전 설정의 첫 사본은 /var/backups/macbook-server-setup/에 보관합니다.
선택 앱: 스크립트 옆에 macbook-assistant.zip과 web_remote.py를 두면 함께 배치합니다.
HELP
}

# 같은 내용이면 건드리지 않고, 변경 시 원본과 권한을 보존하며 원자적으로 교체합니다.
write_config() {
    local dest=$1 temp
    [[ ! -L "$dest" ]] || die "심볼릭 링크인 설정 파일은 직접 변경할 수 없습니다: $dest"
    temp=$(mktemp "${dest}.setup.XXXXXXXX")
    cat > "$temp"
    if [[ -f "$dest" ]] && cmp -s "$temp" "$dest"; then
        rm -f -- "$temp"
        return
    fi
    if [[ -f "$dest" ]]; then
        local saved="$BACKUP$dest"
        if [[ ! -e "$saved" ]]; then
            mkdir -p -- "$(dirname -- "$saved")"
            cp -a -- "$dest" "$saved"
        fi
        chmod --reference="$dest" "$temp"
        chown --reference="$dest" "$temp"
    else
        chmod 0644 "$temp"
    fi
    mv -fT -- "$temp" "$dest"
}

detect_lan() {
    local path candidate flags preferred='' first='' pci=''
    local routes
    routes=$( { ip -o -4 route show default; ip -o -6 route show default; } |
        awk '{for (i=1;i<NF;i++) if ($i=="dev") print $(i+1)}')
    for path in /sys/class/net/*; do
        [[ -e "$path/device" && ! -e "$path/wireless" && ! -e "$path/phy80211" ]] || continue
        [[ $(cat "$path/type") == 1 ]] || continue
        ! grep -qx 'DEVTYPE=wlan' "$path/uevent" || continue
        flags=$(cat "$path/flags")
        (( flags & 1 )) || continue
        [[ $(cat "$path/carrier" 2>/dev/null) == 1 ]] || continue
        candidate=${path##*/}
        [[ "$candidate" =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || continue
        [[ -n "$first" ]] || first=$candidate
        if [[ -z "$pci" && -e "$path/device/subsystem" ]]; then
            [[ $(basename "$(readlink -f "$path/device/subsystem")") != pci ]] || pci=$candidate
        fi
        if grep -Fxq -- "$candidate" <<< "$routes"; then
            preferred=$candidate
            break
        fi
    done
    LAN=${preferred:-${pci:-$first}}
    [[ -n "$LAN" ]] || die "링크가 올라온 유선 LAN이 없습니다. 케이블을 연결한 뒤 다시 실행하세요."
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        local peer=${SSH_CONNECTION%% *} ssh_iface
        ssh_iface=$(ip -o route get "$peer" | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')
        [[ "$ssh_iface" == "$LAN" ]] ||
            die "현재 SSH 연결이 선택된 유선 LAN($LAN)을 사용하지 않습니다. 유선 SSH 또는 로컬 콘솔에서 실행하세요."
    fi
    local driver=unknown
    if [[ -L "/sys/class/net/$LAN/device/driver" ]]; then
        driver=$(basename "$(readlink -f "/sys/class/net/$LAN/device/driver")")
    fi
    log "활성 유선 LAN: $LAN (드라이버 $driver)"
    LAN_IP=$(ip -4 -o addr show dev "$LAN" scope global | awk 'NR==1 {sub(/\/.*/, "", $4); print $4}')
}

has_unit() {
    local state
    state=$(systemctl show --property=LoadState --value "$1" 2>/dev/null) || true
    [[ -n "$state" && "$state" != not-found ]]
}

http_ready() {
    local url=$1 attempt
    for attempt in {1..8}; do
        if curl -q -fsS --noproxy '*' --max-time 3 -o /dev/null "$url"; then
            return 0
        fi
        sleep 1
    done 2>/dev/null
    return 1
}

install_casaos() {
    local installer listeners address
    if has_unit casaos.service; then
        log "기존 CasaOS 서비스가 있어 공식 설치를 건너뜁니다."
    else
        listeners=$(ss -H -ltn 'sport = :80')
        if [[ -n "$listeners" ]]; then
            issue "TCP 80을 다른 서비스가 사용 중이어서 CasaOS 설치를 건너뜁니다. ss -ltnp 'sport = :80'로 확인하세요."
            return
        fi
        if [[ "$CODENAME" == trixie ]]; then
            warn "CasaOS 공식 문서의 권장 배포판은 Debian 12입니다. Debian 13에서는 설치 로그와 서비스 상태를 확인합니다."
        fi
        log "CasaOS 공식 설치 프로그램 실행 (필요하면 Docker도 설치)"
        installer=$(mktemp)
        # 현재 프로세스가 이미 root입니다. 다운로드가 끝난 파일만 실행합니다.
        if ! curl -q -fsSL --proto '=https' --proto-redir '=https' --retry 3 \
            --connect-timeout 15 --max-time 120 -o "$installer" https://get.casaos.io ||
            [[ ! -s "$installer" ]] || ! bash -n "$installer"; then
            rm -f -- "$installer"
            issue "CasaOS 공식 설치 파일을 내려받거나 검사하지 못했습니다."
            return
        fi
        if ! bash "$installer" 9>&-; then
            rm -f -- "$installer"
            issue "CasaOS 공식 설치 프로그램이 실패했습니다. 위의 로그를 확인하세요."
            return
        fi
        rm -f -- "$installer"
    fi

    if ! systemctl start casaos-gateway.service casaos.service ||
        ! systemctl is-active --quiet casaos-gateway.service ||
        ! systemctl is-active --quiet casaos.service; then
        issue "CasaOS 서비스가 시작되지 않았습니다. journalctl -u casaos.service -u casaos-gateway.service -b를 확인하세요."
        return
    fi
    CASA_READY=1
    if [[ -r /run/casaos/gateway.url ]]; then
        address=$(</run/casaos/gateway.url)
        if [[ "$address" =~ :([0-9]{1,5})$ ]]; then
            local port=$((10#${BASH_REMATCH[1]}))
            if (( port >= 1 && port <= 65535 )); then
                CASA_PORT=$port
            fi
        fi
    fi
    local -a docker_check=(-q -fsS --noproxy '*' --max-time 5 --unix-socket /run/docker.sock -o /dev/null)
    if ! curl "${docker_check[@]}" http://localhost/_ping; then
        issue "로컬 Docker 엔진의 응답을 확인하지 못했습니다."
    elif ! curl "${docker_check[@]}" http://localhost/v1.43/version; then
        warn "Docker API 1.43을 확인하지 못했습니다. CasaOS 앱스토어가 열리지 않으면 CasaOS와 Docker의 호환성을 확인하세요."
    fi
    if [[ -n "$LAN_IP" ]] && ! http_ready "http://$LAN_IP:$CASA_PORT/"; then
        issue "유선 LAN에서 CasaOS HTTP 응답을 확인하지 못했습니다 (TCP $CASA_PORT)."
    fi
}

patch_grub() {
    local dest=/etc/default/grub original rendered
    original=$(mktemp)
    cp -p -- "$dest" "$original"
    rendered=$(mktemp)
    if ! /usr/bin/python3 - "$dest" > "$rendered" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text()
name = "GRUB_CMDLINE_LINUX_DEFAULT"
assignment = re.compile(rf"^[ \t]*(?:export[ \t]+)?{name}=", re.M)
pattern = re.compile(
    rf"^([ \t]*(?:export[ \t]+)?{name}=)"
    r"""(?:"([^"\n]*)"|'([^'\n]*)'|([^\s#]*))([ \t]*(?:#[^\n]*)?)$""",
    re.M,
)
matches = list(pattern.finditer(source))
if len(matches) > 1 or len(matches) != len(assignment.findall(source)):
    sys.exit(f"{path}: {name} 설정이 여러 개이거나 지원하지 않는 형식입니다.")
wanted = ("init_on_alloc=0", "intel_iommu=off")
if not matches:
    print(source.rstrip("\n") + f'\n{name}="' + " ".join(wanted) + '"')
    sys.exit()
match = matches[0]
value = next(part for part in match.group(2, 3, 4) if part is not None)
# Keep dynamic, escaped and multiline shell expressions out of the rewrite path.
if "\\" in value or (match.group(3) is None and any(c in value for c in "$`")):
    sys.exit(f"{path}: 동적으로 구성된 {name}은 수동 설정이 필요합니다.")
if match.group(4) is not None and re.search(r'[;|&<>(){}!"]', value):
    sys.exit(f"{path}: {name}에 복합 셸 구문이 있습니다.")
keys = {item.split("=")[0] for item in wanted}
tokens = re.findall(r"""(?:[^\s"']+|"[^"]*"|'[^']*')+""", value)
arguments = []
for token in tokens:
    key = token.partition("=")[0]
    if key in keys:
        canonical = next(item for item in wanted if item.startswith(key + "="))
        if canonical not in arguments:
            arguments.append(canonical)
    else:
        arguments.append(token)
arguments.extend(item for item in wanted if item not in arguments)
quote = "'" if match.group(3) is not None else '"'
replacement = match[1] + quote + " ".join(arguments) + quote + match[5]
sys.stdout.write(source[:match.start()] + replacement + source[match.end():])
PY
    then
        rm -f -- "$original" "$rendered"
        die "GRUB 설정을 안전하게 해석할 수 없습니다."
    fi
    if ! dash -n "$rendered"; then
        rm -f -- "$original" "$rendered"
        die "생성된 GRUB 설정의 셸 문법이 올바르지 않습니다."
    fi
    write_config "$dest" < "$rendered"
    rm -f -- "$rendered"
    if ! update-grub; then
        write_config "$dest" < "$original"
        rm -f -- "$original"
        die "update-grub 실패로 실행 직전 설정을 복구했습니다."
    fi
    if ! awk '
        /^[[:space:]]*linux(efi)?[[:space:]]/ {
            alloc = iommu = ""
            for (i=2; i<=NF; i++) {
                if ($i ~ /^init_on_alloc=/) alloc = $i
                if ($i ~ /^intel_iommu=/) iommu = $i
            }
            if (alloc == "init_on_alloc=0" && iommu == "intel_iommu=off") found = 1
        }
        END {exit !found}
    ' /boot/grub/grub.cfg; then
        write_config "$dest" < "$original"
        rm -f -- "$original"
        update-grub || warn "이전 설정으로 grub.cfg를 다시 생성하지 못했습니다."
        die "생성된 grub.cfg에 두 옵션이 함께 적용된 부팅 항목이 없습니다. /etc/default/grub.d 설정을 확인하세요."
    fi
    rm -f -- "$original"
    local running
    running=" $(cat /proc/cmdline) "
    if [[ "$running" != *' init_on_alloc=0 '* || "$running" != *' intel_iommu=off '* ]]; then
        log "커널 옵션을 적용하려면 설치가 끝난 뒤 재부팅하세요."
    fi
}

enable_apt_components() {
    # 활성 Debian 바이너리 저장소만 편집합니다. third-party, deb-src, disabled는 보존합니다.
    local file rendered matches=0
    for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [[ -f "$file" ]] || continue
        rendered=$(mktemp)
        if /usr/bin/python3 - "$file" "$CODENAME" "$@" > "$rendered" <<'PY'
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

path = Path(sys.argv[1])
codename = sys.argv[2]
suites = {codename, codename + "-updates", codename + "-security"}
aliases = ("oldstable", "stable")
base_alias = aliases[0] if codename == "bookworm" else aliases[1]
suites.update({base_alias, base_alias + "-updates", base_alias + "-security"})
wanted = sys.argv[3:]
found = False

def debian(uri):
    parsed = urlsplit(uri)
    return parsed.scheme in {"http", "https"} and parsed.path.rstrip("/").endswith(("/debian", "/debian-security"))

def add_components(tokens):
    return tokens + [item for item in wanted if item not in tokens]

def legacy(line):
    global found
    match = re.fullmatch(r"(\s*deb[ \t]+(?:\[[^\]]+\][ \t]+)?)(\S+)([ \t]+)(\S+)([ \t]+)([^#\n]*)(.*)", line)
    if not match or match[4] not in suites or not debian(match[2]):
        return line
    old = match[6].split()
    if "main" not in old:
        return line
    found = True
    new = add_components(old)
    if old == new:
        return line
    suffix = match[6][len(match[6].rstrip()):] or (" " if match[7] else "")
    return "".join(match.group(1, 2, 3, 4, 5)) + " ".join(new) + suffix + match[7]

def stanza(block):
    global found
    fields = {}
    last = None
    for line in block.splitlines():
        if line.startswith("#"):
            continue
        if line[:1].isspace() and last is not None:
            fields[last] += line.split()
            continue
        key, separator, value = line.partition(":")
        if separator:
            last = key.lower()
            fields[last] = value.split()
    if (fields.get("enabled", ["yes"])[0].lower() == "no"
        or "deb" not in fields.get("types", [])
        or not fields.get("suites") or not set(fields["suites"]).issubset(suites)
        or not fields.get("uris") or not all(map(debian, fields["uris"]))):
        return block
    old = fields.get("components", [])
    if "main" not in old:
        return block
    found = True
    new = add_components(old)
    if new == old:
        return block
    # Append to the last line of Components, preserving existing indentation/comments.
    lines = block.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lower().startswith("components:"):
            target = index
            for following in range(index + 1, len(lines)):
                if lines[following].startswith("#"):
                    continue
                if not lines[following][:1].isspace():
                    break
                target = following
            body = lines[target].rstrip("\n")
            lines[target] = body.rstrip() + " " + " ".join(new[len(old):]) + body[len(body.rstrip()):] + lines[target][len(body):]
            return "".join(lines)
    return block

source = path.read_text()
if path.suffix == ".sources":
    blocks = re.split(r"(\n[ \t]*\n(?:[ \t]*\n)*)", source)
    output = "".join(stanza(block) if index % 2 == 0 else block for index, block in enumerate(blocks))
else:
    output = "\n".join(legacy(line) for line in source.split("\n"))
sys.stdout.write(output)
sys.exit(0 if found else 3)
PY
        then
            write_config "$file" < "$rendered"
            matches=$((matches + 1))
        else
            local status=$?
            [[ "$status" == 3 ]] || die "APT 소스를 읽지 못했습니다: $file"
        fi
        rm -f -- "$rendered"
    done
    (( matches > 0 )) || die "활성 Debian 저장소를 찾지 못했습니다. contrib non-free non-free-firmware를 활성화한 뒤 다시 실행하세요."
}

has_bcm4322() {
    local dev
    for dev in /sys/bus/pci/devices/*; do
        [[ -r "$dev/vendor" && -r "$dev/device" ]] || continue
        if [[ $(<"$dev/vendor") == 0x14e4 && $(<"$dev/device") == 0x432b ]]; then
            return 0
        fi
    done
    return 1
}

configure_wifi() {
    local current kernel module driver='' found=0
    if (( ! WIFI )); then
        modprobe rfkill || true
        if [[ -c /dev/rfkill ]] && ! rfkill block wlan; then
            issue "현재 Wi-Fi 무선 차단에 실패했습니다. 재부팅 뒤 rfkill list를 확인하세요."
        fi
        write_config "$WIFI_POLICY" <<'EOF'
# Managed by setup_macbook_server.sh. Use --enable-wifi to remove.
blacklist b43
blacklist b43legacy
blacklist brcmsmac
blacklist wl
EOF
        return
    fi
    if ! has_bcm4322; then
        issue "BCM4322(14e4:432b)가 감지되지 않아 Wi-Fi 설치를 건너뜁니다."
        return
    fi
    if [[ -L "/sys/class/net/$LAN/device/driver" ]]; then
        driver=$(basename "$(readlink -f "/sys/class/net/$LAN/device/driver")")
    fi
    case "$driver" in
        b44|b43|b43legacy|ssb|brcmsmac|bcma)
            issue "$driver 모듈을 유선 LAN이 사용 중이므로 Wi-Fi 전환을 건너뜁니다."
            return ;;
    esac
    local policy component
    local -a missing=()
    policy=$(apt-cache policy)
    for component in contrib non-free non-free-firmware; do
        if ! grep -Eq "(^|,)n=$CODENAME,(.*,)?c=$component(,|$)" <<< "$policy"; then
            missing+=("$component")
        fi
    done
    if (( ${#missing[@]} )); then
        enable_apt_components "${missing[@]}"
        if ! apt-get update --error-on=any; then
            issue "Wi-Fi용 Debian 저장소 목록을 갱신하지 못했습니다."
            return
        fi
    fi
    local -a packages=(linux-headers-amd64 broadcom-sta-dkms wireless-tools)
    current=$(uname -r)
    if [[ ! -e "/lib/modules/$current/build/Makefile" ]]; then
        if apt-cache show "linux-headers-$current" >/dev/null 2>&1; then
            packages+=("linux-headers-$current")
        elif [[ $(dpkg-query -W -f='${db:Status-Status}' linux-image-amd64 2>/dev/null || true) == installed ]]; then
            packages+=(linux-image-amd64)
        fi
    fi
    if ! apt-get install -y --no-install-recommends "${packages[@]}"; then
        issue "Broadcom DKMS 패키지 설치에 실패했습니다. 위의 APT/DKMS 로그를 확인하세요."
        return
    fi
    if ! modinfo -k "$current" wl >/dev/null 2>&1; then
        for kernel in /lib/modules/*; do
            [[ -f "/boot/vmlinuz-${kernel##*/}" ]] || continue
            if modinfo -k "${kernel##*/}" wl >/dev/null 2>&1; then
                found=1
                break
            fi
        done
        if (( found )); then
            rm -f -- "$WIFI_POLICY"
            log "wl은 커널 ${kernel##*/}에 빌드되었습니다. 해당 커널로 재부팅한 뒤 이 스크립트를 --enable-wifi로 다시 실행하세요."
        else
            issue "부팅 가능한 커널에서 wl을 찾지 못했습니다. dkms status 및 실행 커널 헤더를 확인하세요."
        fi
        return
    fi
    for module in b44 b43 b43legacy brcmsmac ssb bcma; do
        grep -q "^$module " /proc/modules || continue
        if ! modprobe -r "$module"; then
            issue "$module 모듈이 사용 중입니다. 재부팅 후 --enable-wifi로 다시 시도하세요."
            return
        fi
    done
    if ! modprobe wl; then
        issue "wl 로드에 실패했습니다. dkms status, 커널 로그, Secure Boot 상태를 확인하세요."
        return
    fi
    rm -f -- "$WIFI_POLICY"
    if ! rfkill unblock wlan; then
        issue "wl을 로드했지만 Wi-Fi의 소프트 차단 해제에 실패했습니다."
    fi
}

configure_lid() {
    local file=/etc/systemd/logind.conf input rendered
    input=$file
    [[ -f "$input" ]] || input=/dev/null
    rendered=$(mktemp)
    awk '
        /^[[:space:]]*\[/ {
            if (general && !written) {print "HandleLidSwitch=ignore"; written=1}
            general = $0 ~ /^[[:space:]]*\[Login\][[:space:]]*$/
        }
        general && /^[[:space:]]*HandleLidSwitch[[:space:]]*=/ {
            if (!written) {print "HandleLidSwitch=ignore"; written=1}
            next
        }
        {print}
        END {
            if (!written) {
                if (!general) print "\n[Login]"
                print "HandleLidSwitch=ignore"
            }
        }
    ' "$input" > "$rendered"
    write_config "$file" < "$rendered"
    rm -f -- "$rendered"
    # Enforce the same setting if a distro drop-in overrides the main file.
    install -d -m 0755 /etc/systemd/logind.conf.d
    write_config /etc/systemd/logind.conf.d/90-macbook-server-lid.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
    systemctl restart systemd-logind.service
}

configure_wol() {
    local caps tool device
    tool=$(command -v ethtool)
    caps=$("$tool" "$LAN" | awk '/Supports Wake-on:/ {print $NF}') || caps=
    if [[ "$caps" != *g* ]]; then
        issue "$LAN 드라이버가 매직 패킷 WOL(g)을 지원한다고 보고하지 않습니다."
        return
    fi
    device=$(systemd-escape --path --suffix=device "/sys/subsystem/net/devices/$LAN")
    write_config /etc/systemd/system/macbook-server-wol.service <<EOF
[Unit]
Description=Enable magic-packet Wake-on-LAN on $LAN
After=$device network.target
BindsTo=$device

[Service]
Type=oneshot
ExecStart=$tool -s $LAN wol g

[Install]
WantedBy=$device multi-user.target
EOF
    # reenable removes the old interface's .wants link if LAN was renamed.
    systemctl daemon-reload
    systemctl reenable macbook-server-wol.service
    if ! systemctl start macbook-server-wol.service ||
       ! "$tool" "$LAN" | awk '/^[[:space:]]*Wake-on:/ && $NF=="g" {ok=1} END {exit !ok}'; then
        issue "Wake-on-LAN 적용 확인 실패: $LAN"
    fi
}

configure_fans() {
    local sensor=/sys/devices/platform/applesmc.768 min max rpm file=/etc/mbpfan.conf input rendered
    if [[ ! -r "$sensor/fan1_min" || ! -r "$sensor/fan1_max" ]]; then
        issue "Apple SMC 팬 센서를 읽지 못했습니다. modprobe applesmc와 커널 로그를 확인하세요."
        return
    fi
    input=$file
    [[ -f "$input" ]] || input=/dev/null
    min=$(<"$sensor/fan1_min")
    max=$(<"$sensor/fan1_max")
    if [[ ! "$min" =~ ^[0-9]+$ || ! "$max" =~ ^[0-9]+$ ]]; then
        issue "SMC가 팬 RPM 한계를 숫자로 제공하지 않았습니다."
        return
    fi
    rpm=2000
    (( min <= rpm )) || rpm=$min
    if (( rpm > max )); then
        issue "팬 RPM 범위가 유효하지 않습니다 ($min–$max)."
        return
    fi
    rendered=$(mktemp)
    awk -v rpm="$rpm" '
        function settings() {
            print "min_fan1_speed = " rpm "\nlow_temp = 63\nhigh_temp = 66\nmax_temp = 80"
            written=1
        }
        /^[[:space:]]*\[/ {
            if (general && !written) settings()
            general = $0 ~ /^[[:space:]]*\[general\][[:space:]]*$/
        }
        general && /^[[:space:]]*(min_fan1_speed|low_temp|high_temp|max_temp)[[:space:]]*=/ {next}
        {print}
        END {
            if (!written) {
                if (!general) print "\n[general]"
                settings()
            }
        }
    ' "$input" > "$rendered"
    write_config "$file" < "$rendered"
    rm -f -- "$rendered"
    systemctl enable mbpfan.service
    if ! systemctl restart mbpfan.service || ! systemctl is-active --quiet mbpfan.service; then
        issue "mbpfan 구동 확인 실패: journalctl -u mbpfan.service -b"
    else
        log "팬 최저 ${rpm} RPM, 80°C부터 최고 속도"
    fi
}

configure_audio() {
    local path card name index controls switches line numid saved=0
    local listed card_id dev
    local simple="^Simple mixer control '([^']+)',([0-9]+)$"
    local digital='^(IEC958|S/PDIF|SPDIF)( Playback( Switch)?)?$'
    listed=$(aplay -l 2>/dev/null || true)
    for path in /proc/asound/card[0-9]*; do
        [[ -r "$path/id" ]] || continue
        card=${path##*card}
        card_id=$(<"$path/id")
        dev=$(awk -v card="$card" '
            $1=="card" && $2==card":" && /Digital|IEC958|S\/PDIF/ && !/HDMI/ {
                for (i=3; i<NF; i++) if ($i=="device") {
                    number=$(i+1); sub(/:.*/, "", number); print number; exit
                }
            }
        ' <<< "$listed")
        [[ -e "/sys/class/sound/card$card/device/subsystem" ]] || continue
        [[ $(basename "$(readlink -f "/sys/class/sound/card$card/device/subsystem")") == pci ]] || continue
        [[ "$card_id" =~ ^[A-Za-z0-9_]+$ && "$dev" =~ ^[0-9]+$ ]] || continue
        controls=$(amixer -c "$card" scontrols 2>/dev/null) || continue
        local enabled=0
        while IFS= read -r line; do
            [[ "$line" =~ $simple ]] || continue
            name=${BASH_REMATCH[1]}
            index=${BASH_REMATCH[2]}
            [[ "$name" =~ $digital ]] || continue
            switches=$(amixer -c "$card" sget "$name,$index" 2>/dev/null) || continue
            if [[ "$switches" == *pswitch* ]] &&
                amixer -c "$card" sset "$name,$index" unmute >/dev/null; then
                enabled=1
            fi
        done <<< "$controls"
        if (( ! enabled )); then
            while IFS= read -r numid; do
                [[ -n "$numid" ]] || continue
                if amixer -c "$card" cset "numid=$numid" on >/dev/null; then
                    enabled=1
                fi
            done < <(amixer -c "$card" controls | sed -nE "s/^numid=([0-9]+),iface=MIXER,name='(IEC958|S\/PDIF|SPDIF) Playback Switch'(,index=[0-9]+)?$/\1/p")
        fi
        (( enabled )) || continue
        if alsactl store "$card"; then
            saved=1
            log "ALSA 카드 $card 광출력 스위치 켜짐, 상태 저장"
        else
            issue "ALSA 카드 $card 상태 저장 실패"
        fi
        [[ -n "$OPTICAL_PCM" ]] || OPTICAL_PCM="hw:CARD=$card_id,DEV=$dev"
    done
    (( saved )) || issue "저장 가능한 IEC958/S/PDIF 출력 스위치를 찾지 못했습니다. aplay -l 및 amixer scontrols를 확인하세요."
}

configure_mpd_audio() {
    [[ -n "$OPTICAL_PCM" ]] || return 0
    local alsa=/etc/asound.conf mpd=/etc/mpd.conf rendered original checksum
    rendered=$(mktemp)
    if ! /usr/bin/python3 - "$alsa" "$OPTICAL_PCM" > "$rendered" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text() if path.exists() else ""
begin = "# BEGIN setup_macbook_server S/PDIF"
end = "# END setup_macbook_server S/PDIF"
block = f'''{begin}
pcm.macbook_server_dmix {{
    type dmix
    ipc_key 201071
    ipc_key_add_uid false
    ipc_perm 0666
    slave {{
        pcm "{sys.argv[2]}"
        format S16_LE
        rate 48000
        channels 2
    }}
}}
pcm.macbook_server_spdif {{
    type plug
    slave.pcm "macbook_server_dmix"
}}
{end}'''
if source.count(begin) == source.count(end) == 1 and source.index(begin) < source.index(end):
    output = source[:source.index(begin)] + block + source[source.index(end) + len(end):]
elif begin not in source and end not in source and not re.search(r"pcm\.macbook_server_(dmix|spdif)\b", source):
    output = (source.rstrip("\n") + "\n\n" if source else "") + block + "\n"
else:
    sys.exit("기존 ALSA 설정에서 설치 스크립트의 관리 블록과 충돌합니다.")
sys.stdout.write(output)
PY
    then
        rm -f -- "$rendered"
        APP_PCM="plughw:${OPTICAL_PCM#hw:}"
        issue "$alsa의 공유 출력 설정에 실패했습니다."
        return
    fi
    write_config "$alsa" < "$rendered"
    APP_PCM=macbook_server_spdif
    if grep -Fxq '# Managed by setup_macbook_server.sh: MPD S/PDIF' "$mpd"; then
        rm -f -- "$rendered"
        systemctl start mpd.service || issue "MPD 시작 실패: journalctl -u mpd.service -b"
        return
    fi
    checksum=$(dpkg-query -W -f='${Conffiles}' mpd | awk '$1=="/etc/mpd.conf" {print $2}')
    if [[ ! -f "$mpd" || -z "$checksum" || $(md5sum "$mpd" | cut -d ' ' -f 1) != "$checksum" ]]; then
        rm -f -- "$rendered"
        log "기존 MPD 설정을 보존합니다. 광출력을 쓰려면 $mpd의 ALSA device를 $APP_PCM 으로 지정하세요."
        return
    fi
    # Only replace the stock Debian output, leaving music and database paths intact.
    /usr/bin/python3 - "$mpd" > "$rendered" <<'PY'
import re
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
output = '''# Managed by setup_macbook_server.sh: MPD S/PDIF
audio_output {
    type "alsa"
    name "MacBook Optical S/PDIF"
    device "macbook_server_spdif"
    format "48000:16:2"
    mixer_type "software"
}
'''
source = re.sub(r"^[ \t]*audio_output[ \t]*\{[^\n]*\n.*?^[ \t]*\}[^\n]*\n?", "", source, flags=re.M | re.S)
sys.stdout.write(source.rstrip("\n") + "\n\n" + output)
PY
    original=$(mktemp)
    cp -p -- "$mpd" "$original"
    write_config "$mpd" < "$rendered"
    if ! systemctl restart mpd.service; then
        write_config "$mpd" < "$original"
        systemctl restart mpd.service || true
        issue "MPD가 새 광출력 설정으로 시작하지 못해 이전 설정을 복구했습니다."
    fi
    rm -f -- "$rendered" "$original"
}

check_music_merge() {
    /usr/bin/python3 - "$@" <<'PY'
import filecmp
import stat
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
require_copy = len(sys.argv) > 3
source_device = source.stat().st_dev

def compare(left, right):
    src = left.lstat()
    if src.st_dev != source_device or not (stat.S_ISDIR(src.st_mode) or stat.S_ISREG(src.st_mode)):
        raise ValueError(f"별도 마운트, 링크 또는 특수 파일: {str(left)!r}")
    try:
        dest = right.lstat()
    except FileNotFoundError:
        if require_copy:
            raise ValueError(f"복사본이 없습니다: {str(right)!r}")
        dest = None
    if dest and stat.S_IFMT(src.st_mode) != stat.S_IFMT(dest.st_mode):
        raise ValueError(f"경로 유형 충돌: {str(right)!r}")
    if stat.S_ISDIR(src.st_mode):
        for child in left.iterdir():
            compare(child, right / child.name)
    elif dest and not filecmp.cmp(left, right, shallow=False):
        raise ValueError(f"같은 이름의 서로 다른 파일: {str(right)!r}")

try:
    compare(source, target)
except (OSError, ValueError) as error:
    sys.exit(str(error))
PY
}

mpd_can_read_directory() {
    runuser -u mpd -- sh -c 'test -r "$1" && test -x "$1"' sh "$1"
}

allow_mpd_music_access() {
    local directory=$1
    if ! find "$directory" -xdev -type d -exec setfacl -m u:mpd:rx,d:u:mpd:rx {} + ||
        ! find "$directory" -xdev -type f -exec setfacl -m u:mpd:r {} + ||
        ! mpd_can_read_directory "$directory"; then
        issue "MPD의 음악 폴더 읽기 권한을 확인하지 못했습니다: $directory"
        return 1
    fi
}

link_music_folders() {
    local legacy=$1 backup real_legacy real_casa
    if [[ -L "$CASA_MUSIC" && "$legacy" != "$CASA_MUSIC" && -d "$legacy" && ! -L "$legacy" ]] &&
        [[ "$CASA_MUSIC" -ef "$legacy" ]]; then
        log "이미 연결된 CasaOS 음악 경로를 사용합니다."
        allow_mpd_music_access "$legacy"
        return
    fi
    if [[ -L "$CASA_MUSIC" ]] || ! mkdir -p -- "$CASA_MUSIC"; then
        issue "$CASA_MUSIC 경로를 실제 디렉터리로 만들 수 없습니다."
        return 1
    fi
    real_casa=$(realpath -e -- "$CASA_MUSIC") || return 1
    real_legacy=$(realpath -m -- "$legacy") || return 1
    if [[ "$real_legacy" == "$real_casa" ]]; then
        allow_mpd_music_access "$real_casa"
        return
    fi
    if [[ -L "$legacy" || "$real_legacy/" == "$real_casa/"* ||
        "$real_casa/" == "$real_legacy/"* ]]; then
        issue "$legacy 기존 링크 또는 상하위 경로가 $CASA_MUSIC와 충돌합니다."
        return 1
    fi
    if [[ -e "$legacy" ]]; then
        if [[ ! -d "$legacy" ]] || mountpoint -q -- "$legacy"; then
            issue "$legacy 경로가 일반 디렉터리가 아니거나 별도 마운트 지점입니다."
            return 1
        fi
        if ! check_music_merge "$legacy" "$CASA_MUSIC"; then
            issue "음악 폴더를 합치기 전에 위의 파일 충돌을 확인하세요."
            return 1
        fi
        if [[ -n $(find "$legacy" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
            log "기존 MPD 음원을 $CASA_MUSIC에 복사합니다. 원본은 별도로 보관합니다."
            if ! rsync -rtx --ignore-existing --omit-dir-times -- "$legacy/" "$CASA_MUSIC/" ||
                ! check_music_merge "$legacy" "$CASA_MUSIC" require-copy; then
                issue "복사를 완료하지 못했습니다. 기존 MPD 경로를 유지했습니다."
                return 1
            fi
        fi
    else
        mkdir -p -- "$(dirname -- "$legacy")" || return 1
    fi
    if ! allow_mpd_music_access "$real_casa"; then
        return 1
    fi
    if [[ -d "$legacy" ]]; then
        backup=$(mktemp -d -- "${legacy}.macbook-backup.XXXXXXXX") || return 1
        if ! mv -T -- "$legacy" "$backup/music"; then
            rmdir -- "$backup"
            issue "기존 음악 폴더를 보관 경로로 옮기지 못했습니다."
            return 1
        fi
    fi
    if ! ln -sT -- "$CASA_MUSIC" "$legacy"; then
        if [[ -n "${backup:-}" ]]; then
            if [[ ! -e "$legacy" && ! -L "$legacy" ]] && mv -T -- "$backup/music" "$legacy"; then
                rmdir -- "$backup"
            else
                warn "기존 음원 보관 경로: $backup/music"
            fi
        fi
        issue "MPD 음악 경로의 심볼릭 링크 생성에 실패했습니다."
        return 1
    fi
    if [[ -n "${backup:-}" ]]; then
        if rmdir -- "$backup/music" 2>/dev/null; then
            rmdir -- "$backup"
        else
            log "기존 음원 보관 경로: $backup/music"
        fi
    fi
}

configure_casaos_music() {
    local fallback=$DEBIAN_MUSIC legacy rendered original changed=0
    if [[ -d /boot/dietpi || -d "$(dirname -- "$DIETPI_MUSIC")" ]]; then
        fallback=$DIETPI_MUSIC
    fi
    rendered=$(mktemp)
    if ! legacy=$(/usr/bin/python3 - "$MPD_CONF" "$fallback" "$rendered" \
        "$DEBIAN_MUSIC" "$DIETPI_MUSIC" "$CASA_MUSIC" <<'PY'
import re
import shlex
import sys
from pathlib import Path

config, fallback, output, *allowed = sys.argv[1:]
lines = Path(config).read_text().splitlines(keepends=True)
settings = {}
has_include = False
for index, line in enumerate(lines):
    directive = re.match(r"^\s*(music_directory|auto_update|include|include_optional)(?=\s)", line)
    if not directive:
        continue
    key = directive[1]
    if key.startswith("include"):
        has_include = True
        continue
    args = shlex.split(line, comments=True)
    if len(args) != 2 or key in settings:
        sys.exit(f"MPD 설정에 중복 또는 해석할 수 없는 {key} 항목이 있습니다.")
    settings[key] = (index, args[1])
if has_include:
    sys.exit("MPD include 설정이 있습니다. 실제 music_directory와 auto_update를 확인해 주세요.")
music = settings["music_directory"][1] if "music_directory" in settings else fallback
music = music.rstrip("/")
if music not in allowed:
    sys.exit(f"사용자 지정 MPD 음악 경로를 보존합니다: {music!r}")
additions = []
if "music_directory" not in settings:
    additions.append(f'music_directory "{music}"')
if "auto_update" not in settings:
    additions.append('auto_update "yes"')
elif settings["auto_update"][1] != "yes":
    index = settings["auto_update"][0]
    lines[index] = 'auto_update "yes"\n'
result = "".join(lines)
if additions:
    result = result.rstrip("\n") + "\n\n" + "\n".join(additions) + "\n"
Path(output).write_text(result)
print(music)
PY
    ); then
        rm -f -- "$rendered"
        issue "MPD 음악 경로를 자동 통합하지 못했습니다."
        return
    fi
    if [[ -L "$MPD_CONF" ]]; then
        rm -f -- "$rendered"
        issue "$MPD_CONF가 심볼릭 링크여서 MPD 설정 자동 변경을 건너뜁니다."
        return
    fi
    if [[ "$legacy" != "$CASA_MUSIC" && ! -L "$legacy" && ! "$legacy" -ef "$CASA_MUSIC" ]]; then
        changed=1
    fi
    if ! link_music_folders "$legacy"; then
        rm -f -- "$rendered"
        return
    fi
    if ! mpd_can_read_directory "$legacy"; then
        rm -f -- "$rendered"
        issue "MPD의 음악 폴더 읽기 권한을 확인하지 못했습니다: $legacy"
        return
    fi
    original=$(mktemp)
    cp -p -- "$MPD_CONF" "$original"
    if ! cmp -s "$rendered" "$MPD_CONF"; then
        write_config "$MPD_CONF" < "$rendered"
        changed=1
    fi
    rm -f -- "$rendered"
    if (( changed )); then
        if ! systemctl restart mpd.service; then
            write_config "$MPD_CONF" < "$original"
            systemctl start mpd.service || true
            rm -f -- "$original"
            issue "MPD 재시작에 실패해 설정을 복구했습니다. 음원과 링크는 보존했습니다."
            return
        fi
    elif ! systemctl start mpd.service; then
        rm -f -- "$original"
        issue "MPD 서비스 시작에 실패했습니다."
        return
    fi
    rm -f -- "$original"
    MUSIC_READY=1
    log "CasaOS Files 및 MPD 공유 음악 경로: $CASA_MUSIC"
    if ! timeout 10 mpc --host localhost update >/dev/null; then
        warn "MPD 초기 스캔 요청을 확인하지 못했습니다. MPD 접속 설정을 확인하세요."
    fi
}

setup_python() {
    install -d -o root -g root -m 0755 "$APP"
    # Trixie: distro ONNX Runtime and NumPy are built for Debian amd64.
    local -a venv_options=() extra=(onnxruntime 'numpy<2.4')
    if [[ "$CODENAME" == trixie ]]; then
        venv_options+=(--system-site-packages)
        extra=()
    fi
    local expected actual
    expected=$(/usr/bin/python3 -c 'import sys; print(sys.version_info[:2])')
    if [[ -f "$VENV/pyvenv.cfg" ]]; then
        actual=$("$VENV/bin/python" -c 'import sys; print(sys.version_info[:2])' || true)
        [[ "$expected" == "$actual" ]] || die "기존 $VENV의 Python 버전이 다릅니다. 해당 환경을 백업 경로로 옮긴 뒤 재실행하세요."
        if [[ "$CODENAME" == trixie ]] && ! grep -qi '^include-system-site-packages = true$' "$VENV/pyvenv.cfg"; then
            warn "기존 가상환경을 유지합니다. Debian 런타임 대신 PyPI ONNX Runtime을 사용합니다."
            extra=(onnxruntime 'numpy<2.4')
        fi
    else
        /usr/bin/python3 -m venv "${venv_options[@]}" "$VENV"
    fi
    local py=$VENV/bin/python
    "$py" -m pip --disable-pip-version-check install --only-binary=:all: \
        google-genai edge-tts python-mpd2 fastapi uvicorn sounddevice psutil \
        'httpx[socks]' python-dotenv requests tqdm scipy scikit-learn "${extra[@]}"
    # The assistant only uses ONNX. openWakeWord 0.6.0 otherwise requires the
    # obsolete tflite-runtime package even when its TFLite backend is unused.
    "$py" -m pip --disable-pip-version-check install --only-binary=:all: --no-deps 'openwakeword==0.6.0'
    if ! (ulimit -c 0; env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 "$py" - <<'PY'
from google import genai
import dotenv, edge_tts, fastapi, httpx, mpd, psutil, sounddevice, uvicorn
import numpy, onnxruntime, openwakeword, requests, scipy, sklearn, tqdm
assert "CPUExecutionProvider" in onnxruntime.get_available_providers()
PY
    ); then
        die "Python 런타임 확인 실패. Illegal instruction이면 Core 2 Duo와 ONNX/NumPy 바이너리의 호환성을 확인하세요."
    fi
}

check_wake_word() {
    if ! (ulimit -c 0; timeout 300 env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
        "$VENV/bin/python" - <<'PY'
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

import numpy as np
import openwakeword as oww
from openwakeword.model import Model

entries = (*oww.FEATURE_MODELS.values(), *oww.VAD_MODELS.values(), oww.MODELS["hey_jarvis"])
for entry in entries:
    path = Path(entry["model_path"]).with_suffix(".onnx")
    if path.is_file() and path.stat().st_size:
        continue
    path.parent.mkdir(parents=True, exist_ok=True)
    url = entry["download_url"].replace(".tflite", ".onnx")
    with urlopen(url, timeout=20) as response:
        data = response.read(30_000_001)
    if not 0 < len(data) <= 30_000_000:
        raise RuntimeError(f"모델 파일 크기가 예상 범위를 벗어났습니다: {path.name}")
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(data)
    os.chmod(temp.name, 0o644)
    os.replace(temp.name, path)

model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx", vad_threshold=0.5, ncpu=1)
frame = np.zeros(1280, dtype=np.int16)
for _ in range(12):
    scores = model.predict(frame)
if not scores or not all(np.isfinite(score) for score in scores.values()):
    raise RuntimeError("호출어 모델이 유효한 점수를 반환하지 않았습니다.")
PY
    ); then
        issue "호출어 모델 다운로드 또는 ONNX 추론 검사 실패. 다운로드 연결과 CPU 호환성을 확인하세요."
    else
        log "openWakeWord ONNX 호출어 모델 검사 통과"
    fi
}

deploy_apps() {
    local source=$1 account=macbook-assistant
    if ! id "$account" >/dev/null 2>&1; then
        useradd --system --user-group --home-dir "$APP" --shell /usr/sbin/nologin "$account"
    fi
    local group
    group=$(id -gn "$account")
    if [[ -f "$source/macbook-assistant.zip" && ( ! -e "$APP/assistant.py" || ! -e "$APP/voice_tools.py" ) ]]; then
        /usr/bin/python3 - "$source/macbook-assistant.zip" "$APP" <<'PY'
import os
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

destination = Path(sys.argv[2])
with ZipFile(sys.argv[1]) as archive:
    files = ("assistant.py", "voice_tools.py")
    entries = {name: archive.getinfo(f"macbook-assistant/{name}") for name in files}
    if any(entry.file_size > 2_000_000 for entry in entries.values()):
        sys.exit("비서 ZIP의 Python 파일 크기가 예상 범위를 벗어났습니다.")
    for name, entry in entries.items():
        target = destination / name
        if target.exists() or target.is_symlink():
            continue
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as output:
            output.write(archive.read(entry))
        os.chmod(output.name, 0o644)
        os.replace(output.name, target)
PY
    fi
    local web_updated=0 web_unit=macbook-web-remote.service
    if has_unit macbook-web.service; then
        web_unit=macbook-web.service
    elif [[ -f "$source/web_remote.py" ]] && ! cmp -s "$source/web_remote.py" "$APP/web_remote.py"; then
        write_config "$APP/web_remote.py" < "$source/web_remote.py"
        web_updated=1
    fi

    write_config "$APP/.env.example" <<EOF
# 설치 시 .env가 따로 생성됩니다. .env의 GEMINI_API_KEY를 입력하세요.
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite
WAKE_WORD_MODEL=hey_jarvis
AUDIO_DEVICE_INDEX=
ALSA_PCM=$APP_PCM
TTS_VOICE=ko-KR-SunHiNeural
MPD_HOST=localhost
MPD_PORT=6600
MEMO_PATH=/var/lib/macbook-assistant/memo.json
REMOTE_HOST=0.0.0.0
REMOTE_PORT=8080
CASAOS_ORIGINS=${LAN_IP:+http://$LAN_IP}
REMOTE_KEY=
EOF
    if [[ ! -e "$APP/.env" && ! -L "$APP/.env" ]]; then
        local temp
        temp=$(mktemp "$APP/.env.setup.XXXXXXXX")
        /usr/bin/python3 - "$APP/.env.example" > "$temp" <<'PY'
import secrets
import sys
from pathlib import Path

sample = Path(sys.argv[1]).read_text()
sys.stdout.write(sample.replace("\nREMOTE_KEY=\n", "\nREMOTE_KEY=" + secrets.token_urlsafe(32) + "\n"))
PY
        chown "root:$group" "$temp"
        chmod 0640 "$temp"
        mv -fT -- "$temp" "$APP/.env"
    fi

    local unit entry service
    for entry in assistant.py web_remote.py; do
        [[ -f "$APP/$entry" ]] || continue
        service=macbook-assistant
        if [[ "$entry" == web_remote.py ]]; then
            [[ "$web_unit" != macbook-web.service ]] || continue
            service=macbook-web-remote
        fi
        unit="/etc/systemd/system/$service.service"
        if [[ ! -e "$unit" && ! -L "$unit" ]]; then
            write_config "$unit" <<EOF
[Unit]
Description=$service
Wants=network-online.target
After=network-online.target sound.target mpd.service

[Service]
User=$account
Group=$group
SupplementaryGroups=audio
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
Environment=OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ExecStart=$VENV/bin/python $APP/$entry
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=78
StateDirectory=macbook-assistant
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
        fi
    done
    systemctl daemon-reload

    if [[ -f "$APP/assistant.py" && -f "$APP/voice_tools.py" ]]; then
        log "비서: sudoedit $APP/.env 에서 GEMINI_API_KEY 입력 후 sudo systemctl enable --now macbook-assistant.service"
    else
        log "비서 배치: macbook-assistant.zip을 이 스크립트 옆에 두고 다시 실행하세요."
    fi
    if has_unit "$web_unit"; then
        start_web_remote "$web_unit" "$web_updated"
        if (( REMOTE_READY )) && [[ "$web_unit" == macbook-web-remote.service ]]; then
            log "리모컨 접속 키는 $APP/.env의 REMOTE_KEY입니다."
        fi
    else
        log "리모컨 배치: web_remote.py를 이 스크립트 옆에 두고 다시 실행하세요."
    fi
    if [[ -n "$OPTICAL_PCM" ]]; then
        log "감지한 광출력: $OPTICAL_PCM (ALSA_PCM=$APP_PCM)"
    fi
}

start_web_remote() {
    local unit=$1 updated=$2 listeners
    if systemctl is-active --quiet "$unit"; then
        if (( updated )) && ! systemctl restart "$unit"; then
            issue "웹 리모컨 재시작 실패: journalctl -u $unit -b"
            return
        fi
    else
        listeners=$(ss -H -ltn 'sport = :8080')
        if [[ -n "$listeners" ]]; then
            issue "TCP 8080을 이미 사용 중이어서 $unit 시작을 건너뜁니다."
            return
        fi
        if ! systemctl enable --now "$unit"; then
            issue "웹 리모컨 시작 실패: journalctl -u $unit -b"
            return
        fi
    fi
    if [[ -n "$LAN_IP" ]] && http_ready "http://$LAN_IP:8080/casaos-icon.svg"; then
        REMOTE_READY=1
    else
        issue "LAN에서 최신 웹 리모컨의 HTTP 응답을 확인하지 못했습니다. $unit 로그와 배치된 web_remote.py 버전을 확인하세요."
    fi
}

show_summary() {
    local host=${LAN_IP:-'<맥북IP>'}
    printf '\n%s\n' '=== 맥북 서버 설치 요약 ==='
    printf 'CasaOS 웹 대시보드: http://%s:%s\n' "$host" "$CASA_PORT"
    printf '맥북 오디오 & 절전 리모컨: http://%s:8080\n' "$host"
    if (( ! CASA_READY )); then
        printf '%s\n' 'CasaOS 서비스 상태를 확인하세요.'
    elif (( CASA_PORT != 80 )); then
        printf '%s\n' '기존 CasaOS 게이트웨이의 포트를 유지했습니다.'
    fi
    if (( ! MUSIC_READY )); then
        printf '%s\n' 'MPD 음악 폴더 공유 상태를 확인하세요.'
    fi
    if (( ! REMOTE_READY )); then
        printf '%s\n' '리모컨은 최신 web_remote.py를 배치한 뒤 접속할 수 있습니다.'
    fi
    if [[ -z "$LAN_IP" ]]; then
        printf '%s\n' '유선 LAN IPv4를 확인하려면 ip -4 addr를 실행하세요.'
    fi
    printf '%s\n' 'GRUB 커널 패치 적용을 위해 최초 1회 sudo reboot를 실행해 주세요.'
}

main() {
    while (( $# )); do
        case "$1" in
            --enable-wifi) WIFI=1 ;;
            -h|--help) usage; return ;;
            *) usage >&2; die "알 수 없는 인자: $1" ;;
        esac
        shift
    done
    [[ "$EUID" == 0 ]] || die "sudo bash $0 로 실행하세요."
    [[ $(uname -m) == x86_64 && $(dpkg --print-architecture) == amd64 ]] ||
        die "amd64 Linux가 필요합니다."
    [[ $(cat /sys/class/dmi/id/product_name 2>/dev/null) == MacBookPro7,1 ]] ||
        die "MacBookPro7,1에서만 실행할 수 있습니다."
    [[ -d /run/systemd/system ]] || die "실행 중인 systemd가 필요합니다."
    # shellcheck disable=SC1091
    . /etc/os-release
    CODENAME=${VERSION_CODENAME:-}
    case "$CODENAME" in bookworm|trixie) ;; *) die "Debian Bookworm/Trixie 기반이 필요합니다." ;; esac
    command -v update-grub >/dev/null && [[ -f /etc/default/grub ]] ||
        die "활성 GRUB 설치를 찾지 못했습니다."
    command -v ip >/dev/null || die "유선 LAN 확인에 iproute2가 필요합니다."
    local source
    source=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
    exec 9>/run/lock/macbook-server-setup.lock
    flock -n 9 || die "다른 설치 작업이 진행 중입니다."
    install -d -m 0700 "$BACKUP"
    detect_lan

    STAGE=GRUB
    patch_grub
    STAGE=시스템패키지
    modprobe applesmc || warn "applesmc 로드 실패"
    modprobe coretemp || warn "coretemp 로드 실패"
    apt-get update --error-on=any
    local -a packages=(ethtool rfkill mbpfan alsa-utils python3 python3-venv ca-certificates curl
        libportaudio2 mpd mpc mpv lm-sensors acl rsync)
    [[ "$CODENAME" != trixie ]] || packages+=(python3-onnxruntime python3-numpy python3-scipy python3-sklearn)
    apt-get install -y --no-install-recommends "${packages[@]}"

    STAGE=Wi-Fi
    configure_wifi
    STAGE=상판
    configure_lid
    STAGE=Wake-on-LAN
    configure_wol
    STAGE=팬
    configure_fans
    STAGE=광출력
    configure_audio
    configure_mpd_audio
    STAGE=CasaOS
    install_casaos
    if (( CASA_READY )); then
        STAGE=공유음악폴더
        configure_casaos_music
    fi
    STAGE=Python
    setup_python
    check_wake_word
    STAGE=앱
    deploy_apps "$source"

    show_summary
    if (( ISSUES )); then
        die "설치 작업을 마쳤습니다. 위의 미해결 항목 $ISSUES개를 확인하세요."
    fi
    log "설치 완료. 필요 시 재부팅한 후 WOL과 광출력을 실제로 테스트하세요."
}

main "$@"
