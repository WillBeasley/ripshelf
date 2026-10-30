"""Stand-in for makemkvcon used with fake drives.

    python -m ripshelf.dev.fake_makemkvcon -r --cache=1 info dev:/dev/fake-sr2
    python -m ripshelf.dev.fake_makemkvcon -r --progress=-same mkv dev:/dev/fake-sr2 0 /out
    python -m ripshelf.dev.fake_makemkvcon -r --progress=-same --decrypt backup disc:1 /out

RIPSHELF_FAKE_RIP_SECONDS sets how long a rip takes (default 45).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..makemkv.parse import parse_info
from .fake import DRIVES, FIXTURES


def out(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    cmd, source = args[0], args[1]
    device = by_index(int(source.removeprefix("disc:"))) if source.startswith("disc:") else source.removeprefix("dev:")
    _, disc = DRIVES[device]
    out('MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"')
    if disc.fixture is None:
        time.sleep(10**6)  # a scan that never finishes
        return 1
    text = (FIXTURES / disc.fixture).read_text()
    if cmd == "info":
        lines = text.splitlines()
        delay = 3.0 / max(len(lines), 1)
        for line in lines:
            out(line)
            time.sleep(delay)
        return 0
    if cmd == "backup":
        return backup(disc, Path(args[2]))
    which, dest = args[2], Path(args[3])
    scan = parse_info(text)
    titles = scan.titles if which == "all" else [t for t in scan.titles if t.index == int(which)]
    seconds = float(os.environ.get("RIPSHELF_FAKE_RIP_SECONDS", "45"))
    out('PRGT:5018,0,"Scanning CD-ROM devices"')
    out("PRGV:0,0,65536")
    out(f'MSG:5014,131072,2,"Saving {len(titles)} titles into directory file:///scratch","Saving %1 titles into directory %2","{len(titles)}","file:///scratch"')
    out('PRGT:5024,0,"Saving all titles to MKV files"')
    out('PRGC:5017,0,"Saving to MKV file"')
    # As with MakeMKV, the titles are written one after another, each as its share of the time passes.
    steps = 200
    episodes = [t for t in scan.titles if t.duration_s < max(x.duration_s for x in scan.titles)]
    per = max(steps // max(len(titles), 1), 1)
    done = 0
    for t in titles:
        for _ in range(per):
            done += 1
            v = int(65536 * min(done, steps) / steps)
            out(f"PRGV:{v},{v},65536")
            time.sleep(seconds / steps)
        target = dest / (t.output_file or f"title_t{t.index:02d}.mkv")
        write_video(target, t, episodes if t.duration_s == max(x.duration_s for x in scan.titles) else [t])
    out("PRGV:65536,65536,65536")
    out(f'MSG:5005,128,1,"{len(titles)} titles saved","%1 titles saved","{len(titles)}"')
    out(f'MSG:5036,260,1,"Copy complete. {len(titles)} titles saved.","Copy complete. %1 titles saved.","{len(titles)}"')
    return 0


def by_index(index: int) -> str:
    """The fake drive that is disc:N in its own scan log (the logs come from real drives)."""
    for device, (_, disc) in DRIVES.items():
        if disc.fixture:
            drive = parse_info((FIXTURES / disc.fixture).read_text()).drives
            if any(d.index == index and d.disc_name == disc.label for d in drive.values()):
                return device
    raise SystemExit(f"no disc:{index}")


def backup(disc, dest: Path) -> int:
    """A tiny BDMV folder, with MakeMKV's backup messages."""
    seconds = float(os.environ.get("RIPSHELF_FAKE_RIP_SECONDS", "45"))
    out('PRGT:5018,0,"Scanning CD-ROM devices"')
    out("PRGV:0,65536,65536")
    out('PRGT:3404,0,"Opening Blu-ray disc"')
    out("PRGV:0,65536,65536")
    out('MSG:5085,0,1,"Backing up disc into folder \\"/scratch\\"","Backing up disc into folder \\"%1\\"","/scratch"')
    out('PRGT:5026,0,"Copying all files"')
    out('PRGC:5027,0,"Copying file"')
    steps = 50
    for i in range(1, steps + 1):
        v = int(65536 * i / steps)
        out(f"PRGV:{v},{v},65536")
        time.sleep(seconds / steps)
    files = {"BDMV/index.bdmv": b"INDX0300" + disc.label.encode(), "BDMV/MovieObject.bdmv": b"MOBJ0300",
             "BDMV/PLAYLIST/00800.mpls": b"MPLS0300", "BDMV/STREAM/00001.m2ts": os.urandom(64 * 1024),
             "CERTIFICATE/id.bdmv": b"BDID0200"}
    for rel, data in files.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(data)
    out('MSG:5080,0,0,"Backup done.","Backup done."')
    return 0


def colour(index: int) -> str:
    return f"0x{(index * 2654435761) & 0xFFFFFF:06x}"


def write_video(target: Path, title, parts) -> None:
    """A tiny real video when ffmpeg is there (2 s of a colour per title; a play-all
    is the parts back to back), so thumbnails and the order check work in fake mode."""
    ffmpeg = shutil.which("ffmpeg")
    parts = parts or [title]
    if ffmpeg and len(parts) <= 40:
        inputs = []
        for p in parts:
            inputs += ["-f", "lavfi", "-i", f"color=c={colour(p.index)}:s=160x90:d=2:r=5"]
        filt = "".join(f"[{i}:v]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1[v]"
        res = subprocess.run([ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", filt, "-map", "[v]",
                              "-c:v", "mpeg2video", str(target)], capture_output=True)
        if res.returncode == 0:
            return
    target.write_bytes(os.urandom(16 * 1024))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
