# ripshelf, the controller and embedded worker, with MakeMKV (CLI) built from source.
# Build: docker build -t localhost/ripshelf:latest .   (docs/building.md)
# Builds for linux/amd64 and linux/arm64; the Intel VAAPI driver is amd64 only.

FROM docker.io/library/node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
ENV NPM_CONFIG_UPDATE_NOTIFIER=false
RUN npm ci --no-audit --no-fund
COPY web/ ./
# svelte-kit sync writes .svelte-kit/tsconfig.json, which web/tsconfig.json extends.
RUN npx svelte-kit sync && npm run build

# MakeMKV's open-source part links libavcodec. Debian's libavcodec is built as GPL,
# which may not be combined with MakeMKV's proprietary makemkvcon, so it gets its
# own LGPL build (no --enable-gpl, no external libraries), linked in statically.
# Debian's ffmpeg program stays for transcoding, as a separate program.
FROM docker.io/library/python:3.12-slim-bookworm AS ffmpeg-lgpl
ARG FFMPEG_VERSION=7.1.5
ARG FFMPEG_SHA256=de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential pkg-config wget ca-certificates xz-utils nasm \
 && mkdir /build && cd /build \
 && wget -q https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz \
 && echo "${FFMPEG_SHA256}  ffmpeg-${FFMPEG_VERSION}.tar.xz" | sha256sum -c - \
 && tar xJf ffmpeg-${FFMPEG_VERSION}.tar.xz && cd ffmpeg-${FFMPEG_VERSION} \
 && ./configure --prefix=/opt/ffmpeg-lgpl --enable-static --disable-shared --enable-pic \
        --disable-autodetect --disable-programs --disable-doc --disable-network \
        --disable-avdevice --disable-avformat --disable-avfilter --disable-swscale \
        | tee /tmp/configure.txt \
 && grep -q '^License: LGPL version 2.1 or later' /tmp/configure.txt \
 && make -j"$(nproc)" && make install \
 && mkdir -p /opt/sources && mv /build/ffmpeg-${FFMPEG_VERSION}.tar.xz /opt/sources/

FROM docker.io/library/python:3.12-slim-bookworm

ARG MAKEMKV_VERSION=2.0.0
# Releases other than the newest live under /download/old (pass --build-arg for them).
ARG MAKEMKV_URL_BASE=https://www.makemkv.com/download
ARG MAKEMKV_OSS_SHA256=435316b2d219eb48c880526557addd076b5f5e6de5171424c7651f9cac95b161
ARG MAKEMKV_BIN_SHA256=f1265e74875a186efdfbbbec7459a64e969033515e53cbc4d805f0a374f0a124

COPY --from=ffmpeg-lgpl /opt/ffmpeg-lgpl /opt/ffmpeg-lgpl
COPY --from=ffmpeg-lgpl /opt/sources /usr/share/doc/makemkv/source

# makemkv-bin is installed by hand as its Makefile does, without accepting the EULA
# on anyone's behalf. Whoever runs the image accepts it by setting
# RIPSHELF_ACCEPT_MAKEMKV_EULA=yes; until then ripshelf doesn't run MakeMKV. The
# EULA text stays in /usr/share/doc/makemkv, as it requires when repackaged, and
# the sources of the LGPL and GPL parts are kept in /usr/share/doc/makemkv/source.
# Build deps are removed again; the runtime libraries stay. The Intel VAAPI driver
# (non-free) is for hardware transcoding.
RUN sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential pkg-config wget ca-certificates libc6-dev libssl-dev \
        libexpat1-dev zlib1g-dev \
        libssl3 libexpat1 zlib1g p7zip-full util-linux ffmpeg \
        vainfo default-jre-headless \
 && if [ "$(dpkg --print-architecture)" = amd64 ]; then \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            intel-media-va-driver-non-free; fi \
 && mkdir /build && cd /build \
 && wget -q ${MAKEMKV_URL_BASE}/makemkv-oss-${MAKEMKV_VERSION}.tar.gz \
            ${MAKEMKV_URL_BASE}/makemkv-bin-${MAKEMKV_VERSION}.tar.gz \
 && echo "${MAKEMKV_OSS_SHA256}  makemkv-oss-${MAKEMKV_VERSION}.tar.gz" | sha256sum -c - \
 && echo "${MAKEMKV_BIN_SHA256}  makemkv-bin-${MAKEMKV_VERSION}.tar.gz" | sha256sum -c - \
 && tar xzf makemkv-oss-${MAKEMKV_VERSION}.tar.gz \
 && tar xzf makemkv-bin-${MAKEMKV_VERSION}.tar.gz \
 && cp makemkv-oss-${MAKEMKV_VERSION}.tar.gz /usr/share/doc/makemkv/source/ \
 && cd makemkv-oss-${MAKEMKV_VERSION} \
 && export PKG_CONFIG_PATH=/opt/ffmpeg-lgpl/lib/pkgconfig \
 && ./configure --disable-gui \
        ffmpeg_CFLAGS="$(pkg-config --cflags libavcodec libavutil)" \
        ffmpeg_LIBS="$(pkg-config --static --libs libavcodec libavutil)" \
 && make -j"$(nproc)" && make install \
 && cd ../makemkv-bin-${MAKEMKV_VERSION} \
 && arch=$(dpkg --print-architecture) \
 && install -d /usr/share/MakeMKV \
 && install bin/${arch}/makemkvcon /usr/bin/ \
 && install -m 644 src/share/appdata.tar src/share/blues.jar src/share/blues.policy /usr/share/MakeMKV/ \
 && ln -sf makemkvcon /usr/bin/sdftool \
 && install -m 644 src/eula_en_linux.txt /usr/share/doc/makemkv/ \
 && install -m 644 ../makemkv-oss-${MAKEMKV_VERSION}/License.txt /usr/share/doc/makemkv/oss-License.txt \
 && cd / && rm -rf /build /opt/ffmpeg-lgpl && ldconfig \
 && if ldd /usr/lib/libmakemkv.so.1 | grep -q libavcodec; then \
        echo "libmakemkv links a shared libavcodec, not the LGPL build" >&2; exit 1; fi \
 && apt-get purge -y build-essential pkg-config wget libc6-dev libssl-dev libexpat1-dev zlib1g-dev \
 && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.licenses="MIT AND LicenseRef-MakeMKV-EULA AND GPL-2.0-or-later" \
      org.opencontainers.image.description="ripshelf with MakeMKV. See /usr/share/doc/ripshelf/THIRD-PARTY-NOTICES.md"

WORKDIR /app
COPY LICENSE THIRD-PARTY-NOTICES.md /usr/share/doc/ripshelf/
# Exact versions with hashes from requirements.lock (scripts/lock-deps.sh), in a layer
# of their own so a code change doesn't reinstall them.
ENV PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock
COPY pyproject.toml README.md ./
COPY src/ src/
COPY --from=web /web/build/ src/ripshelf/static/
RUN pip install --no-deps --no-build-isolation . && pip check && rm -rf /app/src

# Mount points. The MakeMKV homes (one per drive, holding _private_data.tar)
# live under /data/makemkv, so they persist with the database. /backups is a
# bind mount too, but not a VOLUME: if it isn't mounted the UI says so.
ENV RIPSHELF_DATA_DIR=/data \
    RIPSHELF_SCRATCH_DIR=/scratch \
    RIPSHELF_LIBRARY_DIR=/library \
    RIPSHELF_BACKUP_DIR=/backups \
    RIPSHELF_ENV_FILE=/data/ripshelf.env \
    RIPSHELF_MAKEMKV_EULA_FILE=/usr/share/doc/makemkv/eula_en_linux.txt \
    RIPSHELF_PORT=8380 \
    HOME=/data
VOLUME ["/data", "/scratch", "/library"]
EXPOSE 8380
CMD ["ripshelf"]
