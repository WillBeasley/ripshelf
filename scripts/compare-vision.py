"""Read the same cover scans with several Claude models and compare what they read.

    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/compare-vision.py data/jobs/*/scans/*.jpg
    python scripts/compare-vision.py --models claude-haiku-5-5,claude-opus-5-5 --px 2576 back.jpg
    python scripts/compare-vision.py --effort low --json results.json scans/*.jpg

Each scan is trimmed to the box the way the app trims a new scan (``images.trim_box``),
made into the same JPEG the app sends (``vision.picture``, at ``--px``), and sent with the
app's own prompt and schema, once to each model. Run from the checkout with ripshelf
installed (``pip install -e .``).

It prints, in Markdown: for each scan, the episodes and extras each model read and the
details where the models disagree; then for each model its cost, time, failures and how
far it agrees with the reference model (``--reference``, by default the last one listed).
Agreement isn't correctness: the reference can be wrong too, so look at the disagreements.

Every scan is one paid request per model, about $0.001 on Haiku 5.5, $0.02 on Sonnet 5.5
and $0.04 on Opus 5.5 for a back cover at 1600 px.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

import anthropic

from ripshelf.scanner import images, vision

# US dollars per million tokens, input and output (prompts under 100K tokens).
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5-2": (8.00, 40.00),
}
DEFAULT_MODELS = "claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5"
DETAILS = ("title", "year", "feature_runtime_min", "episode_count", "barcode", "catalogue_number",
           "region", "video_system", "aspect_ratio")


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def prepare(path: Path, px: int, trim: bool) -> bytes:
    img = images.open_image(path.read_bytes())
    if trim and (box := images.trim_box(img)):
        img = img.crop(tuple(box))
    vision.MAX_PX = px
    return vision.picture(img)


async def ask(client: anthropic.AsyncAnthropic, model: str, jpeg: bytes, effort: str | None) -> dict:
    """One model's reading of one picture: the listing, tokens, cost and seconds, or the error."""
    output_config: dict = {"format": {"type": "json_schema", "schema": vision.SCHEMA}}
    if effort:
        output_config["effort"] = effort
    started = time.monotonic()
    try:
        r = await client.messages.create(
            model=model, max_tokens=vision.MAX_TOKENS, output_config=output_config,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(jpeg).decode()}},
                {"type": "text", "text": vision.PROMPT}]}])
    except anthropic.APIError as e:
        return {"error": f"{type(e).__name__}: {e}"[:300], "secs": time.monotonic() - started}
    out = {"secs": time.monotonic() - started, "input_tokens": r.usage.input_tokens,
           "output_tokens": r.usage.output_tokens}
    price = PRICES.get(model)
    out["cost"] = (r.usage.input_tokens * price[0] + r.usage.output_tokens * price[1]) / 1e6 if price else None
    if r.stop_reason in ("refusal", "max_tokens"):
        return {**out, "error": f"stopped: {r.stop_reason}"}
    try:
        text = next((b.text for b in r.content if b.type == "text"), "")
        out["listing"] = vision.tidy(vision._json(text)).model_dump(mode="json")
    except vision.VisionError as e:
        out["error"] = str(e)
    return out


def items(listing: dict) -> list[str]:
    return [("Episode " + (f"{i['number']}: " if i["number"] else "") if i["kind"] == "episode" else "Extra: ")
            + i["title"]
            + (f" ({i['runtime_min']:g} min)" if i.get("runtime_min") else "") for i in listing["items"]]


def agreement(listing: dict, ref: dict) -> tuple[int, int, int, int, int]:
    """Items matched by title, items only the reference has, items only this one has,
    then details that agree and details the reference has."""
    mine = {norm(i["title"]) for i in listing["items"]}
    theirs = {norm(i["title"]) for i in ref["items"]}
    agree = have = 0
    for k in DETAILS:
        if ref["details"].get(k) is not None:
            have += 1
            agree += norm(str(listing["details"].get(k))) == norm(str(ref["details"][k]))
    return len(mine & theirs), len(theirs - mine), len(mine - theirs), agree, have


def report(results: dict[str, dict[str, dict]], models: list[str], reference: str) -> str:
    lines: list[str] = []
    totals = {m: {"cost": 0.0, "secs": 0.0, "n": 0, "failed": 0, "items": [0, 0, 0], "details": [0, 0],
                  "in": 0, "out": 0} for m in models}
    for scan, by_model in results.items():
        lines += [f"## {scan}", ""]
        for m in models:
            r = by_model[m]
            t = totals[m]
            t["n"] += 1
            t["secs"] += r["secs"]
            t["cost"] += r.get("cost") or 0
            t["in"] += r.get("input_tokens", 0)
            t["out"] += r.get("output_tokens", 0)
            if "listing" not in r:
                t["failed"] += 1
                lines += [f"**{m}**: failed ({r['error']})", ""]
                continue
            lines += [f"**{m}** ({r['secs']:.1f} s, {r.get('input_tokens')} in / {r.get('output_tokens')} out)", ""]
            lines += [f"- {x}" for x in items(r["listing"])] or ["- (no episodes or extras)"]
            lines.append("")
        ref = by_model[reference].get("listing")
        if ref:
            for m in models:
                if m != reference and "listing" in by_model[m]:
                    a = agreement(by_model[m]["listing"], ref)
                    totals[m]["items"] = [x + y for x, y in zip(totals[m]["items"], a[:3], strict=True)]
                    totals[m]["details"] = [x + y for x, y in zip(totals[m]["details"], a[3:], strict=True)]
            rows = []
            for k in DETAILS:
                vals = {m: by_model[m]["listing"]["details"].get(k) for m in models if "listing" in by_model[m]}
                if len({norm(str(v)) for v in vals.values()}) > 1:
                    rows.append(f"| {k} | " + " | ".join(str(vals.get(m, "")) for m in models) + " |")
            if rows:
                lines += ["Details the models disagree on:", "", "| | " + " | ".join(models) + " |",
                          "|---" * (len(models) + 1) + "|", *rows, ""]
    lines += ["## Summary", "", f"Agreement is with {reference}.", "",
              "| model | scans | failed | tokens in / out (mean) | cost | per scan | mean time "
              "| items matched | missed | extra | details agreeing |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        t = totals[m]
        n = max(t["n"], 1)
        cost = f"${t['cost']:.4f}" if m in PRICES else "?"
        per = f"${t['cost'] / n:.4f}" if m in PRICES else "?"
        it = "- | - | -" if m == reference else " | ".join(str(x) for x in t["items"])
        det = "-" if m == reference else f"{t['details'][0]} of {t['details'][1]}"
        lines.append(f"| {m} | {t['n']} | {t['failed']} | {t['in'] // n} / {t['out'] // n} | {cost} | {per} "
                     f"| {t['secs'] / n:.1f} s | {it} | {det} |")
    return "\n".join(lines) + "\n"


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("scans", nargs="+", type=Path, help="cover scans (JPEG, PNG, ...)")
    p.add_argument("--models", default=DEFAULT_MODELS, help=f"comma-separated (default {DEFAULT_MODELS})")
    p.add_argument("--reference", help="the model to compare the others with (default: the last of --models)")
    p.add_argument("--px", type=int, default=vision.MAX_PX,
                   help=f"long edge of the picture sent (default {vision.MAX_PX}, as the app sends)")
    p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"),
                   help="thinking effort (default: the model's own, as the app sends)")
    p.add_argument("--no-trim", action="store_true", help="send the scans as they are, not trimmed to the box")
    p.add_argument("--json", type=Path, help="also write every reply, tokens and cost here")
    a = p.parse_args()
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    reference = a.reference or models[-1]
    if reference not in models:
        p.error("--reference must be one of --models")

    client = anthropic.AsyncAnthropic(max_retries=2, timeout=vision.CLOUD_TIMEOUT_S * 2)
    results: dict[str, dict[str, dict]] = {}
    for path in a.scans:
        jpeg = prepare(path, a.px, not a.no_trim)
        print(f"{path}: {len(jpeg) // 1024} KB, asking {len(models)} models", file=sys.stderr)
        replies = await asyncio.gather(*(ask(client, m, jpeg, a.effort) for m in models))
        name = str(path) if str(path) not in results else f"{path} ({len(results) + 1})"
        results[name] = dict(zip(models, replies, strict=True))
    await client.close()

    sys.stdout.write(report(results, models, reference))
    if a.json:
        a.json.write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
