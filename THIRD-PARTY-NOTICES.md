# Third-party notices

ripshelf's own code is under the [MIT licence](LICENSE). It uses, and its container image contains, software and data from other people under their own terms. This page lists them. It is a summary, not legal advice, and the licence texts themselves are what count.

## In the container image

The image is built from `python:3.12-slim-bookworm` (Debian 12) by the [Dockerfile](Dockerfile). Each Debian package keeps its copyright file in `/usr/share/doc/<package>/copyright` inside the image, and its source is available from [Debian](https://sources.debian.org) and [snapshot.debian.org](https://snapshot.debian.org).

| Component | Licence | Notes |
|---|---|---|
| **MakeMKV** (`makemkvcon`, `sdftool`, `appdata.tar`) | MakeMKV EULA, proprietary, by GuinpinSoft inc | Built from the official `makemkv-bin` release. The EULA allows redistribution of the unmodified program and repackaging of its Linux part, with its program files and licence unchanged. The EULA text is in `/usr/share/doc/makemkv/eula_en_linux.txt`. Building the image does not accept it. ripshelf runs MakeMKV only once you set `RIPSHELF_ACCEPT_MAKEMKV_EULA=yes`, which says you have read and accepted it. MakeMKV needs its own key, which ripshelf does not supply. |
| **MakeMKV open-source part** (`libmakemkv`, `libdriveio`, `libmmbd`, `mmgplsrv`) | LGPL-2.1 and GPL-2.0, per file | Built from the official `makemkv-oss` source release, whose SHA-256 is in the Dockerfile. Its source tarball is in the image at `/usr/share/doc/makemkv/source/`, and at [makemkv.com/download](https://www.makemkv.com/download/). Licence text in `/usr/share/doc/makemkv/oss-License.txt`. |
| **FFmpeg libavcodec and libavutil, LGPL build** | LGPL-2.1-or-later | Built from the official FFmpeg release (version and SHA-256 in the Dockerfile) without `--enable-gpl` or any external library, and linked statically into `libmakemkv`. Debian's own libavcodec is GPL, which may not be combined with MakeMKV's proprietary `makemkvcon`, hence the separate build. The FFmpeg source tarball is in the image at `/usr/share/doc/makemkv/source/`. `libmakemkv.so.1` is a shared library under the LGPL, so it can be rebuilt and replaced. |
| **blues.jar** (MakeMKV's BD-J helper) | GPL-2.0 | Ships inside the MakeMKV release, as listed in its EULA's Appendix A. |
| **FFmpeg program** (`ffmpeg`, `ffprobe`, `libavcodec59` and friends) | GPL-2.0-or-later as Debian builds it (`--enable-gpl`, with x264 and x265) | Debian 12 packages, unmodified, used for transcoding. ripshelf runs `ffmpeg` as a separate program, and MakeMKV does not use these libraries. |
| **x264, x265** | GPL-2.0-or-later | Debian packages, pulled in by FFmpeg. |
| **OpenJDK 17** (`default-jre-headless`) | GPL-2.0 with the Classpath Exception | Debian package. MakeMKV uses it for BD-J menus. |
| **Intel media driver** (`intel-media-va-driver-non-free`, amd64 only) | MIT and BSD-3-Clause | Debian `non-free` package, because it contains GPU kernels without their source. Freely redistributable. |
| **libva, vainfo** | MIT | Debian packages. |
| **p7zip** (`p7zip-full`) | LGPL-2.1-or-later | Debian package. The RAR code (`p7zip-rar`) is not included. |
| **Python 3.12** | PSF-2.0 | From the base image. |

### Python packages

Installed with pip from PyPI, unmodified. All are permissive.

| Package | Licence |
|---|---|
| FastAPI, Pydantic, pydantic-core, pydantic-settings, RapidFuzz, anyio, h11, httptools, PyYAML, watchfiles, annotated-types, annotated-doc, typing-inspection | MIT |
| uvloop | MIT or Apache-2.0 |
| Starlette, Uvicorn, HTTPX, httpcore, click, idna, python-dotenv, websockets | BSD-3-Clause |
| python-multipart, opentelemetry-api | Apache-2.0 |
| certifi | MPL-2.0 (unmodified) |
| typing-extensions | PSF-2.0 |

### Web UI

The web UI is built with [Svelte](https://svelte.dev) and [SvelteKit](https://svelte.dev/docs/kit), both MIT. Their runtime code is bundled into the static files the image serves. The build tools (Vite, TypeScript and their dependencies) are MIT, Apache-2.0, ISC, BSD or Python-2.0, and are not shipped.

## Data and services

ripshelf talks to these services with keys that each user supplies. Their data is not shipped with ripshelf, apart from a few recorded responses used as test fixtures.

| Service | Terms | What ripshelf does |
|---|---|---|
| [TMDB](https://www.themoviedb.org) | [API terms](https://www.themoviedb.org/api-terms-of-use) | Shows TMDB's logo and the notice "This product uses the TMDB API but is not endorsed or certified by TMDB" in the About section at the bottom of Settings, and the notice in the README. Posters are cached for at most 90 days (TMDB allows six months). Free for non-commercial use. |
| [TheTVDB](https://thetvdb.com) | [API information](https://thetvdb.com/api-information) | Shows "Metadata provided by TheTVDB" with a link in Settings, About. |
| [TVmaze](https://www.tvmaze.com) | [CC BY-SA 4.0](https://www.tvmaze.com/api#licensing) | Credited with a link in Settings, About. |
| [OMDb](https://www.omdbapi.com) | [CC BY-NC 4.0](https://www.omdbapi.com) ([terms](https://www.omdbapi.com/legal.htm)) | Credited with a link in Settings, About. Non-commercial use only. |
| [TheDiscDB](https://thediscdb.com) | [data repository](https://github.com/lfoust/thediscdb-data) is MIT | Credited with a link in Settings, About. Contributions back are opt-in. |

## Trademarks

MakeMKV, TMDB, TheTVDB, TVmaze, OMDb, TheDiscDB, Emby, Jellyfin, Plex, Blu-ray, Ultra HD Blu-ray and DVD are trademarks of their owners. ripshelf is not affiliated with or endorsed by any of them. The ripshelf logo and icons in `web/static/brand` are original to this project and under its MIT licence. The TMDB logo in `web/static/credits/tmdb.svg` belongs to TMDB, is not covered by the MIT licence, and is used only for the attribution TMDB's terms ask for.
