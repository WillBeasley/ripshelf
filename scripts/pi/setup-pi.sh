#!/usr/bin/env bash
# Set up a Raspberry Pi 5 (arm64, Raspberry Pi OS Bookworm or Trixie) for ripshelf.
# Safe to run again, because each step checks what is already there. See docs/raspberry-pi.md.
#
#   scripts/pi/setup-pi.sh --tests            # what the test suite needs, then run it
#   scripts/pi/setup-pi.sh --native           # run ripshelf from this checkout (builds MakeMKV)
#   scripts/pi/setup-pi.sh --docker           # Docker, and pull the published image
#   scripts/pi/setup-pi.sh --all              # all three
#
# Options:
#   --accept-makemkv-eula   don't ask before accepting MakeMKV's licence (read it first:
#                           makemkv-bin-<version>/src/eula_en_linux.txt)
#   --usb-current           set usb_max_current_enable=1 in /boot/firmware/config.txt
#   --build-image           with --docker: build the image on the Pi instead of pulling it
#   --no-image              with --docker: install Docker but don't pull or build the image
#   --yes                   answer yes to every question (reboot included)
#   --dry-run               print the commands instead of running them
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DO_TESTS=0 DO_NATIVE=0 DO_DOCKER=0 IMAGE=pull USB_CURRENT=0 YES=0 DRY=0 EULA=0
REBOOT_WANTED=0 RELOGIN_WANTED=0

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

[ $# -gt 0 ] || usage 1
for a in "$@"; do
    case "$a" in
        --tests) DO_TESTS=1 ;;
        --native) DO_NATIVE=1 ;;
        --docker) DO_DOCKER=1 ;;
        --all) DO_TESTS=1 DO_NATIVE=1 DO_DOCKER=1 ;;
        --build-image) IMAGE=build ;;
        --no-image) IMAGE=none ;;
        --usb-current) USB_CURRENT=1 ;;
        --accept-makemkv-eula) EULA=1 ;;
        --yes|-y) YES=1 ;;
        --dry-run|-n) DRY=1 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown option: $a" >&2; usage 1 ;;
    esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
run() {
    printf '    $ %s\n' "$*"
    [ "$DRY" = 1 ] || "$@"
}
ask() {  # ask "question" -> 0 for yes
    [ "$YES" = 1 ] && return 0
    [ -t 0 ] || return 1
    local r; read -r -p "    $1 [y/N] " r; [[ "$r" =~ ^[Yy] ]]
}
in_dir() {  # use inside ( ... ): cd, printed
    printf '    $ cd %s\n' "$1"
    [ "$DRY" = 1 ] || cd "$1"
}
have() { command -v "$1" >/dev/null 2>&1; }
SUDO=sudo; [ "$(id -u)" = 0 ] && SUDO=
if [ -n "$SUDO" ] && [ "$DRY" = 0 ] && ! sudo -n true 2>/dev/null; then
    if [ -t 0 ]; then sudo -v
    else echo "sudo needs a password and there's no terminal to ask on (e.g. over plain ssh). Run this from a terminal, or use ssh -t" >&2; exit 1; fi
fi

# --- the machine ----------------------------------------------------------------
step "Checking the machine"
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
# shellcheck disable=SC1091
. /etc/os-release
CODENAME=${VERSION_CODENAME:-unknown}
note "architecture $ARCH, $PRETTY_NAME, kernel $(uname -r), page size $(getconf PAGESIZE)"
[ -r /proc/device-tree/model ] && note "model: $(tr -d '\0' </proc/device-tree/model)"
if [ "$ARCH" != arm64 ]; then
    note "This script is for arm64 (a Pi 5 on 64-bit Raspberry Pi OS); this is $ARCH."
    ask "Carry on anyway?" || exit 1
fi
case "$CODENAME" in
    bookworm|trixie) ;;
    *) note "Tested shape is Debian bookworm/trixie; this is '$CODENAME'. Package names may differ." ;;
esac

APT_UPDATED=0
apt_install() {  # apt_install pkg... : only the missing ones
    local missing=()
    for p in "$@"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing+=("$p"); done
    if [ ${#missing[@]} -eq 0 ]; then note "already installed: $*"; return; fi
    if [ "$APT_UPDATED" = 0 ]; then run $SUDO apt-get update; APT_UPDATED=1; fi
    run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
}

GROUPS_DONE=" "
add_group() {  # add_group group : add the current user to it
    local u=${SUDO_USER:-$USER}
    [[ "$GROUPS_DONE" == *" $1 "* ]] && return 0
    GROUPS_DONE+="$1 "
    getent group "$1" >/dev/null || return 0
    if id -nG "$u" | tr ' ' '\n' | grep -qx "$1"; then note "$u is in $1"; return; fi
    run $SUDO usermod -aG "$1" "$u"; RELOGIN_WANTED=1
}

# --- common to every route --------------------------------------------------------
step "Base packages"
apt_install ca-certificates curl wget git util-linux p7zip-full ffmpeg sg3-utils lsscsi usbutils

step "Optical drive access"
if [ -e /sys/module/sg ]; then note "sg module loaded"; else run $SUDO modprobe sg; fi
if [ -f /etc/modules-load.d/sg.conf ]; then note "sg loads at boot (/etc/modules-load.d/sg.conf)"
else
    printf '    $ echo sg | sudo tee /etc/modules-load.d/sg.conf\n'
    [ "$DRY" = 1 ] || echo sg | $SUDO tee /etc/modules-load.d/sg.conf >/dev/null
fi
add_group cdrom
# Some setups give /dev/sg* to group disk instead of cdrom.
for sg in /sys/class/block/sr*/device/scsi_generic/sg*; do
    [ -e "$sg" ] || continue
    g=$(stat -c %G "/dev/$(basename "$sg")" 2>/dev/null || true)
    [ -n "$g" ] && [ "$g" != root ] && add_group "$g"
done
compgen -G "/sys/class/block/sr*" >/dev/null || note "no optical drive plugged in yet (that's fine)"

if [ "$USB_CURRENT" = 1 ]; then
    step "USB current (usb_max_current_enable=1)"
    CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
    if [ ! -f "$CFG" ]; then note "no $CFG (not Raspberry Pi OS?): skipped"
    elif grep -q '^usb_max_current_enable=1' "$CFG"; then note "already set in $CFG"
    else
        printf '    $ echo usb_max_current_enable=1 | sudo tee -a %s\n' "$CFG"
        [ "$DRY" = 1 ] || echo usb_max_current_enable=1 | $SUDO tee -a "$CFG" >/dev/null
        REBOOT_WANTED=1
    fi
fi

# --- Python (tests and native) -------------------------------------------------------
PY=
python_ok() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; }
setup_python() {
    step "Python 3.12+ and the virtualenv in $ROOT/.venv"
    if [ -x "$ROOT/.venv/bin/python" ] && python_ok "$ROOT/.venv/bin/python"; then
        note ".venv exists ($("$ROOT/.venv/bin/python" -V))"
    elif python_ok python3; then
        # On Trixie python3 is 3.13.
        apt_install python3-venv python3-dev
        run python3 -m venv "$ROOT/.venv"
    else
        # On Bookworm python3 is 3.11, and ripshelf needs 3.12. uv brings its own Python.
        note "python3 is $(python3 -V 2>&1 | cut -d' ' -f2); ripshelf needs 3.12+, so using uv"
        if ! have uv && [ ! -x "$HOME/.local/bin/uv" ]; then
            printf '    $ curl -LsSf https://astral.sh/uv/install.sh | sh\n'
            [ "$DRY" = 1 ] || curl -LsSf https://astral.sh/uv/install.sh | sh
        fi
        UV=$(command -v uv || echo "$HOME/.local/bin/uv")
        run "$UV" python install 3.12
        run "$UV" venv --seed --python 3.12 "$ROOT/.venv"
    fi
    PY="$ROOT/.venv/bin/python"
    run "$PY" -m pip install -q --upgrade pip
    # The locked versions (scripts/lock-deps.sh), then ripshelf itself.
    run "$PY" -m pip install -q -r "$ROOT/requirements-dev.lock"
    run "$PY" -m pip install -q --no-deps -e "$ROOT"
}

# --- Node (UI build and svelte-check) ------------------------------------------------
setup_node() {
    step "Node.js 20+ for the web UI"
    local major=0
    have node && major=$(node -p 'process.versions.node.split(".")[0]')
    if [ "$major" -ge 20 ]; then note "node $(node -v)"
    else
        # Bookworm's nodejs is 18; CI and the Dockerfile use 22.
        printf '    $ curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -\n'
        [ "$DRY" = 1 ] || curl -fsSL https://deb.nodesource.com/setup_22.x | $SUDO -E bash -
        APT_UPDATED=1
        run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
    fi
    (in_dir "$ROOT/web" && run npm ci --no-audit --no-fund)
}

# --- MakeMKV, built from source (native route) ---------------------------------------
dockerfile_arg() { sed -n "s/^ARG $1=//p" "$ROOT/Dockerfile" | head -1; }
setup_makemkv() {
    local v oss_sha bin_sha stamp=/usr/share/MakeMKV/ripshelf-installed-version
    v=$(dockerfile_arg MAKEMKV_VERSION); oss_sha=$(dockerfile_arg MAKEMKV_OSS_SHA256)
    bin_sha=$(dockerfile_arg MAKEMKV_BIN_SHA256)
    step "MakeMKV $v (the version the Dockerfile pins), built from source"
    if have makemkvcon && [ "$(cat "$stamp" 2>/dev/null)" = "$v" ]; then
        note "makemkvcon $v already installed"; return
    fi
    # --disable-gui, because only makemkvcon is needed, so no Qt.
    apt_install build-essential pkg-config libc6-dev libssl-dev libexpat1-dev zlib1g-dev \
        libavcodec-dev default-jre-headless
    local b=/tmp/makemkv-build
    [ "$DRY" = 1 ] || b=$(mktemp -d)
    note "building in $b"
    for part in oss bin; do
        local f="makemkv-$part-$v.tar.gz" sha
        [ $part = oss ] && sha=$oss_sha || sha=$bin_sha
        # Only the newest release is under /download; older ones move to /download/old.
        run wget -q -O "$b/$f" "https://www.makemkv.com/download/$f" \
            || run wget -q -O "$b/$f" "https://www.makemkv.com/download/old/$f"
        if [ "$DRY" = 0 ]; then echo "$sha  $b/$f" | sha256sum -c - ; fi
        run tar xzf "$b/$f" -C "$b"
    done
    if [ "$DRY" = 0 ] && [ "$EULA" = 0 ]; then
        note "MakeMKV's licence: $b/makemkv-bin-$v/src/eula_en_linux.txt"
        ask "Accept the MakeMKV EULA?" || { echo "Not accepted; MakeMKV not installed." >&2; exit 1; }
    fi
    (in_dir "$b/makemkv-oss-$v" && run ./configure --disable-gui && run make -j"$(nproc)" && run $SUDO make install)
    (in_dir "$b/makemkv-bin-$v" && run mkdir -p tmp && { [ "$DRY" = 1 ] || echo accepted > tmp/eula_accepted; } \
        && run $SUDO make install)
    run $SUDO ldconfig
    [ "$DRY" = 1 ] || echo "$v" | $SUDO tee "$stamp" >/dev/null
    run rm -rf "$b"
}

# --- Docker -------------------------------------------------------------------------
setup_docker() {
    step "Docker"
    if have docker; then note "$(docker --version)"
    else
        # get.docker.com gives the compose and buildx plugins; Debian's docker.io lacks compose v2 on Bookworm.
        printf '    $ curl -fsSL https://get.docker.com | sudo sh\n'
        [ "$DRY" = 1 ] || curl -fsSL https://get.docker.com | $SUDO sh
    fi
    add_group docker
    local d=(docker)
    # Just added to the docker group, which only takes effect at the next login.
    docker info >/dev/null 2>&1 || { [ -z "$SUDO" ] || d=(sudo docker); }
    if [ "$IMAGE" = pull ]; then
        step "Pulling ghcr.io/willbeasley/ripshelf:latest"
        run "${d[@]}" pull ghcr.io/willbeasley/ripshelf:latest
    elif [ "$IMAGE" = build ]; then
        step "Building localhost/ripshelf:latest on the Pi (MakeMKV compiles: 15-30 minutes)"
        (in_dir "$ROOT" && run "${d[@]}" build -t localhost/ripshelf:latest .)
    fi
}

# Plain ifs, because a function called from an && list runs with set -e switched off.
if [ "$DO_TESTS" = 1 ] || [ "$DO_NATIVE" = 1 ]; then setup_python; setup_node; fi
if [ "$DO_NATIVE" = 1 ]; then setup_makemkv; fi
if [ "$DO_DOCKER" = 1 ]; then setup_docker; fi

if [ "$DO_NATIVE" = 1 ]; then
    step "Web UI build (served from web/build by a native run)"
    (in_dir "$ROOT/web" && run npm run build)
fi

if [ "$DO_TESTS" = 1 ]; then
    step "Running the checks CI runs"
    # Simulated UHD rips check that scratch (under the temp folder) has room for a real one,
    # so a small /tmp (Trixie makes it a tmpfs) fails them. var/ is git-ignored.
    tmp_free=$(df -BG --output=avail "${TMPDIR:-/tmp}" | tail -1 | tr -dc 0-9)
    if [ "${tmp_free:-0}" -lt 150 ]; then
        note "${TMPDIR:-/tmp} has ${tmp_free} GB free; the tests use $ROOT/var/tmp instead"
        run mkdir -p "$ROOT/var/tmp"; export TMPDIR="$ROOT/var/tmp"
    fi
    (in_dir "$ROOT" && run "$PY" -m ruff check src tests && run "$PY" -m pytest -q)
    (in_dir "$ROOT/web" && run npm run check)
fi

step "Done"
[ "$DO_NATIVE" = 1 ] && note "Start it: cd $ROOT && .venv/bin/ripshelf   (UI on http://<pi>:8380)"
[ "$DO_DOCKER" = 1 ] && note "Docker: see docs/raspberry-pi.md (the compose file pulls the image)"
[ "$DO_DOCKER" = 1 ] && [ "$IMAGE" = build ] && note "Use the image built here: RIPSHELF_IMAGE=localhost/ripshelf:latest in .env"
note "Check everything: scripts/pi/check-pi.sh"
[ "$RELOGIN_WANTED" = 1 ] && note "Group changes take effect after you log out and in again (or reboot)."
if [ "$REBOOT_WANTED" = 1 ]; then
    note "config.txt changed: a reboot is needed."
    if [ "$DRY" = 0 ] && ask "Reboot now?"; then run $SUDO reboot; fi
fi
