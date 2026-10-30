#!/usr/bin/env bash
# Read-only check of a Raspberry Pi 5 for ripshelf. Prints OK, WARN or FAIL per item.
# Changes nothing. See docs/raspberry-pi.md.
#
#   scripts/pi/check-pi.sh            # everything below
#   scripts/pi/check-pi.sh --bench    # also time a short software x265 encode
#   scripts/pi/check-pi.sh --no-makemkv   # skip asking makemkvcon for its drives (it spins them up)
#   scripts/pi/check-pi.sh --worker   # also check this machine can work for a controller:
#       RIPSHELF_CONTROLLER_URL, RIPSHELF_WORKER_TOKEN and RIPSHELF_WORKER_ID from the environment
#       or ripshelf.env in the checkout (the same names as the worker's compose file)
# ok/warn/info always succeed, so "test && ok ... || fail ..." is if/else here.
# shellcheck disable=SC2015
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BENCH=0 MAKEMKV=1 WORKER=0
for a in "$@"; do
    case "$a" in
        --bench) BENCH=1 ;;
        --no-makemkv) MAKEMKV=0 ;;
        --worker) WORKER=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 1 ;;
    esac
done

FAILS=0 WARNS=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; WARNS=$((WARNS + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILS=$((FAILS + 1)); }
info() { printf '        %s\n' "$*"; }
section() { printf '\n\033[1m%s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
in_group() { id -nG | tr ' ' '\n' | grep -qx "$1"; }

section "Machine"
arch=$(uname -m)
[ "$arch" = aarch64 ] && ok "arch $arch" || fail "arch $arch (expected aarch64: 64-bit Raspberry Pi OS)"
model=$(tr -d '\0' 2>/dev/null </proc/device-tree/model || echo unknown)
case "$model" in *"Raspberry Pi 5"*) ok "$model" ;; *) warn "model: $model (not a Pi 5)" ;; esac
# shellcheck disable=SC1091
. /etc/os-release
case "${VERSION_CODENAME:-}" in
    bookworm|trixie) ok "$PRETTY_NAME" ;;
    *) warn "$PRETTY_NAME (docs assume bookworm or trixie)" ;;
esac
info "kernel $(uname -r), page size $(getconf PAGESIZE) (16384 is the Pi 5 default; makemkvcon is fine with it)"
root_src=$(findmnt -no SOURCE / 2>/dev/null)
case "$root_src" in
    /dev/nvme*) ok "root on NVMe ($root_src)" ;;
    /dev/mmcblk*) warn "root on the SD card ($root_src): fine for tests, slow for rips" ;;
    *) info "root on $root_src" ;;
esac
free_gb=$(df -BG --output=avail "$ROOT" | tail -1 | tr -dc 0-9)
[ "${free_gb:-0}" -ge 100 ] && ok "${free_gb} GB free under $ROOT" \
    || warn "${free_gb} GB free under $ROOT: a UHD rip needs about 100 GB of scratch"
tmp=${TMPDIR:-/tmp}
tmp_free=$(df -BG --output=avail "$tmp" | tail -1 | tr -dc 0-9)
[ "${tmp_free:-0}" -ge 150 ] && ok "${tmp_free} GB free in $tmp ($(findmnt -no FSTYPE -T "$tmp"))" \
    || warn "${tmp_free} GB free in $tmp ($(findmnt -no FSTYPE -T "$tmp")): the tests' simulated UHD rips want 100+ GB; setup-pi.sh --tests uses var/tmp"
if have vcgencmd; then
    t=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    case "$t" in
        0x0) ok "no under-voltage or throttling since boot" ;;
        "") ;;
        *) warn "vcgencmd get_throttled=$t (bit 0/16: under-voltage, bit 2/18: throttled). Check the PSU." ;;
    esac
    info "$(vcgencmd measure_temp 2>/dev/null)"
fi

section "USB power"
cfg=/boot/firmware/config.txt; [ -f "$cfg" ] || cfg=/boot/config.txt
# The firmware turns it on itself when the PSU says it can give 5 A (the official 27 W one).
v=$(vcgencmd get_config usb_max_current_enable 2>/dev/null | cut -d= -f2)
if [ "$v" = 1 ]; then
    grep -q '^usb_max_current_enable=1' "$cfg" 2>/dev/null \
        && ok "USB ports may draw 1.6 A (usb_max_current_enable=1 in $cfg)" \
        || ok "USB ports may draw 1.6 A (the firmware found a 5 A PSU)"
elif [ -n "$v" ]; then
    warn "USB ports are limited to 600 mA: not a 5 A PSU. A bus-powered BD drive needs its own power, a powered hub, or usb_max_current_enable=1 in $cfg"
else
    info "can't read usb_max_current_enable (no vcgencmd)"
fi

section "Tools"
for t in git ffmpeg ffprobe 7z blkid java; do
    if have "$t"; then ok "$t: $(command -v "$t")"
    elif [ "$t" = blkid ] && [ -x /usr/sbin/blkid ]; then
        ok "blkid: /usr/sbin/blkid (not on your PATH; ripshelf looks in /usr/sbin too)"
    else
        case "$t" in
            java) warn "java missing: some Blu-rays need BD-J (default-jre-headless)" ;;
            git) warn "git missing" ;;
            *) fail "$t missing" ;;
        esac
    fi
done
have java && info "$(java -version 2>&1 | head -1)"
have sg_inq || info "sg3-utils not installed (optional: sg_inq for poking at drives)"

section "Python and Node"
if have python3; then
    pv=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' && ok "python3 $pv" \
        || info "system python3 is $pv (< 3.12): the .venv must come from uv (setup-pi.sh does this)"
fi
VPY="$ROOT/.venv/bin/python"
if [ -x "$VPY" ]; then
    if "$VPY" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then ok ".venv $("$VPY" -V)"
    else fail ".venv is $("$VPY" -V): needs 3.12+"; fi
    "$VPY" -c 'import ripshelf' 2>/dev/null && ok "ripshelf importable from .venv" || fail "ripshelf not installed in .venv"
    "$VPY" -c 'import pytest, respx' 2>/dev/null && ok "test extras installed" || warn "test extras missing (pip install -r requirements-dev.lock)"
else
    warn "no .venv in $ROOT (setup-pi.sh --tests or --native makes it)"
fi
have uv && info "uv $(uv --version | cut -d' ' -f2)"
if have node; then
    nm=$(node -p 'process.versions.node.split(".")[0]')
    [ "$nm" -ge 20 ] && ok "node $(node -v)" || fail "node $(node -v): the UI build needs 20+ (CI uses 22)"
else
    warn "node missing (needed to build the UI natively and for npm run check)"
fi
[ -d "$ROOT/web/node_modules" ] && ok "web/node_modules present" || info "web/node_modules missing (npm ci in web/)"
[ -f "$ROOT/web/build/index.html" ] || [ -f "$ROOT/src/ripshelf/static/index.html" ] \
    && ok "web UI built" || info "web UI not built (npm run build in web/) - only needed for a native run"

section "ffmpeg encoders"
if have ffmpeg; then
    info "$(ffmpeg -hide_banner -version | head -1)"
    enc=$(ffmpeg -hide_banner -encoders 2>/dev/null)
    grep -q ' libx265 ' <<<"$enc" && ok "libx265 (what ripshelf uses on a Pi)" || fail "ffmpeg has no libx265"
    grep -q ' libx264 ' <<<"$enc" && ok "libx264" || info "no libx264"
    grep -q 'hevc_v4l2m2m' <<<"$enc" && info "hevc_v4l2m2m is listed, but a Pi 5 has no H.264/H.265 encoder block (ripshelf doesn't use it)"
    if [ -e /dev/dri/renderD128 ]; then
        if ffmpeg -v error -init_hw_device vaapi=va:/dev/dri/renderD128 -filter_hw_device va -f lavfi \
            -i color=black:s=256x144:d=0.2 -vf format=nv12,hwupload -c:v hevc_vaapi -f null - >/dev/null 2>&1; then
            warn "hevc_vaapi works on /dev/dri/renderD128 - unexpected on a Pi 5"
        else
            ok "hevc_vaapi doesn't work on /dev/dri/renderD128 (the v3d GPU), so ripshelf picks x265"
        fi
    fi
    if [ "$BENCH" = 1 ]; then
        info "timing 10 s of 1080p through libx265 -preset medium (what ripshelf runs)..."
        start=$(date +%s.%N)
        ffmpeg -v error -f lavfi -i testsrc2=s=1920x1080:r=24:d=10 -c:v libx265 -preset medium -crf 20 \
            -x265-params log-level=error -f null - 2>/dev/null
        end=$(date +%s.%N)
        fps=$(awk -v s="$start" -v e="$end" 'BEGIN { printf "%.1f", 240 / (e - s) }')
        hrs=$(awk -v f="$fps" 'BEGIN { printf "%.1f", 172800 / f / 3600 }')
        info "x265 1080p: ${fps} fps, so a 2 h film takes about ${hrs} h (synthetic source; real films are slower)"
    fi
fi

section "Optical drives"
if [ -e /sys/module/sg ]; then ok "sg module loaded"; else fail "sg module not loaded (sudo modprobe sg)"; fi
[ -f /etc/modules-load.d/sg.conf ] && ok "sg loads at boot" || warn "sg isn't set to load at boot (/etc/modules-load.d/sg.conf)"
in_group cdrom && ok "$(id -un) is in cdrom" || fail "$(id -un) isn't in cdrom (sudo usermod -aG cdrom $(id -un), then log in again)"
found=0
for sys in /sys/class/block/sr*; do
    [ -e "$sys" ] || continue
    found=1
    sr=$(basename "$sys"); dev=$sys/device
    vendor=$(tr -s ' ' <"$dev/vendor" 2>/dev/null); model=$(tr -s ' ' <"$dev/model" 2>/dev/null)
    sg=$(find "$dev/scsi_generic" -mindepth 1 -maxdepth 1 -printf "%f\n" 2>/dev/null | head -1)
    path=$(readlink -f "$dev")
    bus=other; [[ "$path" == */usb* ]] && bus=USB
    drv=
    if [ "$bus" = USB ]; then
        # The USB interface the SCSI host hangs off. Its driver is uas or usb-storage.
        intf=$(echo "$path" | grep -oE '.*/[0-9]+-[0-9.]+:[0-9.]+' | head -1)
        drv=$(basename "$(readlink -f "$intf/driver" 2>/dev/null)" 2>/dev/null)
    fi
    ok "/dev/$sr${sg:+ + /dev/$sg}: $vendor $model ($bus${drv:+, $drv})"
    for n in "/dev/$sr" ${sg:+"/dev/$sg"}; do
        if [ -r "$n" ] && [ -w "$n" ]; then info "$n $(stat -c '%U:%G %a' "$n"): readable and writable"
        else fail "$n $(stat -c '%U:%G %a' "$n"): not readable+writable by $(id -un) (group $(stat -c %G "$n"))"; fi
    done
    [ -n "$sg" ] || fail "/dev/$sr has no sg device: is the sg module loaded?"
done
[ "$found" = 1 ] || warn "no optical drive found (/sys/class/block/sr*): plugged in and powered?"
if [ "$found" = 1 ] && [ -x "$VPY" ]; then
    info "as ripshelf sees them (SG_IO identity, drive id):"
    "$VPY" - <<'EOF' 2>&1 | sed 's/^/          /'
from ripshelf.hw.drives import SysfsDrives
for d in SysfsDrives().discover([]):
    print(f"{d.device} {d.sg_device}: id {d.id}, serial {d.serial or 'NONE (id follows the USB port)'}, "
          f"{', '.join(c.value for c in d.capabilities)}")
EOF
fi

section "MakeMKV"
if have makemkvcon; then
    ok "makemkvcon: $(command -v makemkvcon)"
    stamp=$(cat /usr/share/MakeMKV/ripshelf-installed-version 2>/dev/null)
    want=$(sed -n 's/^ARG MAKEMKV_VERSION=//p' "$ROOT/Dockerfile" 2>/dev/null | head -1)
    [ -n "$stamp" ] && info "installed by setup-pi.sh: $stamp (Dockerfile pins $want)"
    if [ "$MAKEMKV" = 1 ]; then
        info "asking makemkvcon for its version and drives (up to 60 s)..."
        out=$(timeout 60 makemkvcon -r --cache=1 info disc:9999 2>&1)
        rc=$?
        ver=$(grep -o 'MakeMKV v[0-9.]* [^"]*' <<<"$out" | head -1)
        [ -n "$ver" ] && ok "$ver" || warn "makemkvcon printed no version (rc $rc)"
        [ $rc = 124 ] && warn "makemkvcon timed out: a drive may be hanging it (docs/troubleshooting.md)"
        grep '^DRV:' <<<"$out" | awk -F, '$2 != 256' | sed 's/^/          /'
        grep -q 'evaluation period has expired\|registration key' <<<"$out" \
            && warn "MakeMKV wants a key (set it in ripshelf's Settings)"
    fi
else
    warn "makemkvcon not installed (native route: setup-pi.sh --native; the Docker image has its own)"
fi

section "Docker"
if have docker; then
    ok "$(docker --version)"
    if docker info >/dev/null 2>&1; then
        ok "docker daemon reachable as $(id -un)"
        docker compose version >/dev/null 2>&1 && ok "$(docker compose version)" || warn "no docker compose plugin"
        docker image inspect localhost/ripshelf:latest >/dev/null 2>&1 \
            && ok "image localhost/ripshelf:latest ($(docker image inspect -f '{{.Architecture}}' localhost/ripshelf:latest))" \
            || info "no localhost/ripshelf:latest image built yet"
    else
        warn "docker installed but not usable as $(id -un) (in the docker group? logged in again?)"
    fi
else
    info "docker not installed (only needed for the Docker route)"
fi

if [ "$WORKER" = 1 ]; then
    section "Remote worker"
    envfile="$ROOT/ripshelf.env"
    envval() {  # the variable from the environment, else from ripshelf.env
        local v=${!1:-}
        [ -z "$v" ] && [ -f "$envfile" ] && v=$(sed -n "s/^$1=//p" "$envfile" | tail -1 | tr -d '"'"'"'')
        printf '%s' "$v"
    }
    url=$(envval RIPSHELF_CONTROLLER_URL); token=$(envval RIPSHELF_WORKER_TOKEN); wid=$(envval RIPSHELF_WORKER_ID)
    [ -n "$wid" ] || wid=$(hostname)
    if [ -z "$url" ]; then fail "RIPSHELF_CONTROLLER_URL isn't set"
    elif ! have curl; then fail "curl missing"
    else
        url=${url%/}
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url/api/status")
        [ "$code" = 200 ] && ok "controller answers at $url" || fail "controller at $url: HTTP ${code:-none} from /api/status"
        if [ -z "$token" ]; then fail "RIPSHELF_WORKER_TOKEN isn't set (Settings -> Remote workers on the controller)"
        elif [ "$code" = 200 ]; then
            # Read-only, what a worker fetches after registering. The body (it holds the key) is dropped.
            code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
                -H "Authorization: Bearer $token" "$url/api/workers/$wid/makemkv")
            case "$code" in
                200) ok "token accepted for worker '$wid' (registered now)" ;;
                404) ok "token accepted for worker '$wid' (not registered yet: start the worker)" ;;
                401) fail "token refused (wrong, or revoked in Settings -> Remote workers)" ;;
                403) fail "token is for another worker id than '$wid', or remote workers are off" ;;
                *) fail "token check: HTTP $code" ;;
            esac
        fi
    fi
    case "$url" in http://*) info "plain http: the token and the MakeMKV key cross the network unencrypted (docs/workers.md)" ;; esac
fi

printf '\n%d failed, %d warnings\n' "$FAILS" "$WARNS"
[ "$FAILS" = 0 ]
