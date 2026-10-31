"""Simulated drives for UI development and tests (RIPSHELF_FAKE_HARDWARE=1).

Each fake drive replays a real MakeMKV scan log (tests/fixtures);
``fake_makemkvcon`` plays the matching rip with synthetic progress.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .. import discfs
from ..hw.drives import DiscIdentity, DriveHw
from ..models import Capability, DriveStatus

FIXTURES = Path(os.environ.get("RIPSHELF_FAKE_FIXTURES", Path(__file__).parents[3] / "tests" / "fixtures"))


@dataclass
class FakeDisc:
    label: str
    fixture: str | None  # None means the scan never finishes (tests the scan timeout)
    size: int
    bdmt: str | None = None


DRIVES = {
    "/dev/fake-sr2": (
        DriveHw(id="fake-pioneer", device="/dev/fake-sr2", sg_device="/dev/fake-sg7", vendor="PIONEER",
                model="BD-RW  BDR-S11JX", revision="1.53", serial=None,
                capabilities=[Capability.dvd, Capability.bluray, Capability.uhd], port="fake:2"),
        FakeDisc("THE_BRASS_HERON", "scan-uhd-brass-heron.log", 84_505_309_184, bdmt="The Brass Heron"),
    ),
    "/dev/fake-sr1": (
        DriveHw(id="fake-ihas122", device="/dev/fake-sr1", sg_device="/dev/fake-sg6", vendor="ATAPI",
                model="iHAS122", revision="ZL0E", serial=None, capabilities=[Capability.dvd], port="fake:1"),
        FakeDisc("MOONLIT_ORCHARD", "scan-dvd-moonlit-orchard.log", 3_892_871_168),
    ),
    "/dev/fake-sr0": (
        DriveHw(id="fake-ghc0n", device="/dev/fake-sr0", sg_device="/dev/fake-sg5", vendor="HL-DT-ST",
                model="DVD-RAM GHC0N", revision="MA00", serial=None, capabilities=[Capability.dvd],
                port="fake:0"),
        FakeDisc("PIPPA_&_GOOSE_KITE_TRAM", "scan-dvd-pippa-goose.log", 4_000_000_000),
    ),
    "/dev/fake-sr3": (
        DriveHw(id="fake-ggc-h20n", device="/dev/fake-sr3", sg_device="/dev/fake-sg1", vendor="HL-DT-ST",
                model="BDDVDRW GGC-H20N", revision="1.01", serial=None,
                capabilities=[Capability.dvd, Capability.bluray], port="fake:3"),
        FakeDisc("PAPER_KITES", None, 45_000_000_000),
    ),
}


class FakeDrives:
    def __init__(self, present: set[str] | None = None):
        self.present = set(DRIVES) if present is None else present
        self.swapped: dict[str, str] = {}  # device -> label of a different disc put in

    def discover(self, uhd_models: list[str]) -> list[DriveHw]:
        return [hw for hw, _ in DRIVES.values()]

    def status(self, device: str) -> DriveStatus:
        return DriveStatus.disc if device in self.present else DriveStatus.empty

    def eject(self, device: str) -> None:
        self.present.discard(device)

    def close_tray(self, device: str) -> None:
        pass  # fake trays are never open, and an ejected fake drive is just empty

    def insert(self, device: str, label: str | None = None) -> None:
        """Put the drive's disc back in, or (with ``label``) a different disc."""
        self.present.add(device)
        if label:
            self.swapped[device] = label
        else:
            self.swapped.pop(device, None)

    async def disc_identity(self, device: str) -> DiscIdentity:
        disc = DRIVES[device][1]
        label = self.swapped.get(device, disc.label)
        uuid = hashlib.sha1(label.encode()).hexdigest()[:16]
        return DiscIdentity(label=label, uuid=uuid, fs_type="udf", fs_version="2.50", size_bytes=disc.size)

    async def extract_structure(self, device: str, out_dir: Path) -> list[str]:
        await asyncio.sleep(0.5)
        disc = DRIVES[device][1]
        files: dict[str, bytes] = {}
        if disc.bdmt:
            files["BDMV/index.bdmv"] = b"INDX0300" + disc.label.encode()
            files["AACS/Unit_Key_RO.inf"] = b"AACS" + disc.label.encode()  # gives the disc an AACS Disc ID
            files["BDMV/META/DL/bdmt_eng.xml"] = (
                '<?xml version="1.0"?><disclib><di:discinfo xmlns:di="urn:BDA:bdmv;discinfo">'
                f"<di:title><di:name>{disc.bdmt}</di:name></di:title></di:discinfo></disclib>"
            ).encode()
        else:
            files["VIDEO_TS/VIDEO_TS.IFO"] = (b"DVDVIDEO-VMG" + disc.label.encode()).ljust(256, b"\0")
        for rel, data in files.items():
            p = out_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        # The video files' sizes, as a real read lists them (TheDiscDB's content hash).
        video = "BDMV/STREAM/0000{}.m2ts" if disc.bdmt else "VIDEO_TS/VTS_01_{}.VOB"
        seed = sum(disc.label.encode())
        (out_dir / discfs.SIZES).write_text("".join(f"{video.format(i)}\t{(seed + i) * 1048576}\n"
                                                    for i in range(1, 4)))
        return sorted(files)
