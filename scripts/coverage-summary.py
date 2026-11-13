"""Combine CI's Linux and Windows coverage and write a Markdown summary.

    python scripts/coverage-summary.py .coverage.linux .coverage.windows > summary.md

Leaves the combined data in .coverage (for ``coverage html``) and prints: the total for
each platform alone and combined, the files Windows covers more than GAP points less
than Linux, and the combined report. Run from the checkout, which the files map to
([tool.coverage.paths] in pyproject.toml).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

GAP = 5.0


def coverage(*args: str) -> str:
    return subprocess.run([sys.executable, "-m", "coverage", *args], check=True,
                          capture_output=True, text=True).stdout


def files(data: str, tmp: Path) -> tuple[float, dict[str, float]]:
    """The total and each file's percentage, from a data file mapped to the checkout."""
    out = tmp / f"{Path(data).name}.json"
    coverage("json", f"--data-file={data}", "-o", str(out))
    report = json.loads(out.read_text())
    return (report["totals"]["percent_covered"],
            {f: v["summary"]["percent_covered"] for f, v in report["files"].items()})


def main(linux: str, windows: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # Each alone first, through combine, which maps its file names to the checkout's.
        mapped = {}
        for name, data in (("Linux", linux), ("Windows", windows)):
            mapped[name] = str(tmp / name)
            coverage("combine", "--keep", f"--data-file={mapped[name]}", data)
        coverage("combine", "--keep", linux, windows)
        (lin_total, lin), (win_total, win) = files(mapped["Linux"], tmp), files(mapped["Windows"], tmp)
        both_total, _ = files(".coverage", tmp)
    print("## Coverage\n")
    print("| Linux | Windows | Combined |\n|---:|---:|---:|")
    print(f"| {lin_total:.1f}% | {win_total:.1f}% | {both_total:.1f}% |\n")
    gaps = sorted(((lin[f] - win.get(f, 0.0), f) for f in lin if lin[f] - win.get(f, 0.0) > GAP), reverse=True)
    if gaps:
        print(f"### Covered on Linux, not on Windows (more than {GAP:g} points less)\n")
        print("| File | Linux | Windows |\n|---|---:|---:|")
        for _, f in gaps:
            print(f"| `{f}` | {lin[f]:.1f}% | {win.get(f, 0.0):.1f}% |")
        print()
    print("<details><summary>Combined, by file (fully covered files left out)</summary>\n")
    print(coverage("report", "--skip-covered", "--format=markdown"))
    print("</details>")


if __name__ == "__main__":
    main(*sys.argv[1:3])
