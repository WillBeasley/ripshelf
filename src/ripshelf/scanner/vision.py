"""Reading the box with a vision model: a second reader, beside Tesseract.

One request per scan: the picture (trimmed, upright, at most 1600 pixels) and a prompt
asking for the listing as JSON in a fixed schema. Two kinds of provider:

- **OpenAI-compatible** (``/chat/completions`` with a JSON schema response format):
  Ollama, LM Studio or vLLM on the LAN, or a hosted service with a key.
- **Anthropic** (the Messages API, through its SDK, with structured outputs).

The reply is checked against the schema and tidied (an "episode" that is the film's own
title is dropped, regions and channels are written the way the rest of ripshelf writes
them); a reply that doesn't fit is thrown away. Nothing here decides what's kept: see
``listing.merge`` for how the two readings are combined.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx
from PIL import Image, ImageDraw
from pydantic import BaseModel, ValidationError

from ..models import AudioTrack, Listing, ListingDetails, ListingItem, SubtitleTrack
from .listing import EXTRAS_TYPES, LANGUAGE, QUOTED

log = logging.getLogger(__name__)

MAX_PX = 1600
LOCAL_TIMEOUT_S = 300  # a 4B model on a desktop CPU takes a minute or two a picture
CLOUD_TIMEOUT_S = 90
MAX_TOKENS = 8000


def _nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


def _obj(props: dict) -> dict:
    """An object in the strict form structured outputs want: every field required (null
    when not printed), nothing else allowed."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj({
    "items": {"type": "array", "items": _obj({
        "kind": {"type": "string", "enum": ["episode", "extra"]},
        "title": {"type": "string"},
        "number": _nullable("integer"),
        "runtime_min": _nullable("number"),
        "extra_type": {"anyOf": [{"type": "string", "enum": EXTRAS_TYPES}, {"type": "null"}]},
        "disc": _nullable("integer"),
        "show": _nullable("string"),
    })},
    "details": _obj({
        "title": _nullable("string"),
        "year": _nullable("integer"),
        "feature_runtime_min": _nullable("number"),
        "episode_count": _nullable("integer"),
        "barcode": _nullable("string"),
        "catalogue_number": _nullable("string"),
        "region": _nullable("string"),
        "video_system": {"anyOf": [{"type": "string", "enum": ["PAL", "NTSC"]}, {"type": "null"}]},
        "aspect_ratio": _nullable("string"),
        "audio": {"type": "array", "items": _obj({
            "language": {"type": "string"}, "format": _nullable("string"), "channels": _nullable("string")})},
        "subtitles": {"type": "array", "items": _obj({
            "language": {"type": "string"}, "hard_of_hearing": {"type": "boolean"}})},
    }),
})

PROMPT = (
    "This is a scan or photo of a DVD or Blu-ray box. List what the box says is on the disc, as JSON in the "
    "given schema. Read only what is printed; never guess or add anything that isn't there, and use null "
    "when something isn't printed.\n"
    "- items: every episode and every special feature or extra, in the order printed. kind is episode or "
    "extra. Never list the film itself. For an extra whose description quotes a title, use the quoted "
    "title. extra_type is one of Featurette (making-of, documentary), DeletedScene, Trailer, Interview, "
    "Scene, Music, Short, Other, Extra. runtime_min only when a running time is printed beside it. show is "
    "null, except for a bonus episode of a different show (\"Includes bonus episode\", \"Plus meet ... in an "
    "all new series\"): then kind is extra, extra_type Other, title is that episode's own title (often in "
    "quotes) and show is the other show's name. A bonus episode of this disc's own show is an episode. A "
    "running time for the whole disc goes in details, not on each item.\n"
    "- details: the title, the copyright year, the feature's running time in minutes (2hrs 45mins is 165), "
    "the number of episodes if stated, the barcode digits, the catalogue number, the region (the number "
    "in the globe logo, or as printed, such as 2 or 2 & 4 or B), PAL or NTSC, the aspect ratio such as "
    "16:9, the audio tracks (language, format such as Dolby Digital or DTS-HD MA, channels such as mono, "
    "2.0, 5.1) and the subtitle languages.")


class VisionError(Exception):
    pass


class _Reply(BaseModel):
    """The reply, as the schema asks for it (checked before it's used)."""

    class Item(BaseModel):
        kind: str
        title: str
        number: int | None = None
        runtime_min: float | None = None
        extra_type: str | None = None
        disc: int | None = None
        show: str | None = None

    class Audio(BaseModel):
        language: str
        format: str | None = None
        channels: str | None = None

    class Sub(BaseModel):
        language: str
        hard_of_hearing: bool = False

    class Details(BaseModel):
        title: str | None = None
        year: int | None = None
        feature_runtime_min: float | None = None
        episode_count: int | None = None
        barcode: str | None = None
        catalogue_number: str | None = None
        region: str | None = None
        video_system: str | None = None
        aspect_ratio: str | None = None
        audio: list[_Reply.Audio] = []
        subtitles: list[_Reply.Sub] = []

    items: list[Item]
    details: Details


_Reply.Details.model_rebuild()
_Reply.model_rebuild()

FORMATS = ["Dolby Atmos", "Dolby TrueHD", "DTS-HD MA", "DTS-HD HR", "DTS:X", "DTS", "LPCM", "Dolby Digital Plus",
           "Dolby Digital", "PCM", "MPEG"]


def _format(v: str | None) -> str | None:
    if not v:
        return None
    low = v.lower().replace("-", " ")
    for f in FORMATS:
        if f.lower().replace("-", " ") in low:
            return f
    if "ac3" in low or "ac 3" in low or low.strip() == "dd":
        return "Dolby Digital"
    return None


def _channels(v: str | None) -> str | None:
    if not v:
        return None
    low = v.lower()
    for want in ("7.1", "6.1", "5.1", "2.0"):
        if want in low:
            return want
    if "mono" in low or low.strip() in ("1.0", "1"):
        return "mono"
    if "stereo" in low or low.strip() == "2":
        return "2.0"
    return None


def _region(v: str | None) -> str | None:
    if not v:
        return None
    v = re.sub(r"(?i)\bregion\b|\bcode\b|[:]", " ", v).strip()
    codes = re.findall(r"\b([0-8]|[ABC])\b", v)
    return " & ".join(dict.fromkeys(codes)) if codes else None


def _same_name(a: str, b: str) -> bool:
    return bool(a and b) and re.sub(r"[^a-z0-9]", "", a.lower()) == re.sub(r"[^a-z0-9]", "", b.lower())


def tidy(raw: dict) -> Listing:
    """The reply as a listing, in ripshelf's own terms; VisionError if it isn't one."""
    try:
        r = _Reply.model_validate(raw)
    except ValidationError as e:
        raise VisionError(f"the reply doesn't fit the listing ({e.error_count()} problems)") from None
    d = r.details
    film = re.sub(r"[^a-z0-9]", "", (d.title or "").lower())
    items = []
    for it in r.items:
        title = it.title.strip()
        if it.kind == "extra":
            # "In-depth documentary on the making of the film 'Return to the Orchard'" is
            # called what it quotes, as in Tesseract's reading (a model may drop the last quote).
            # (An apostrophe in a word, "Peppa's Christmas", opens no quote.)
            quoted = QUOTED.findall(title) or re.findall(r"(?:[\"“]|(?<![A-Za-z])[‘'])([^\"“”‘’']{3,})$", title)
            if quoted and re.search(r"[A-Za-z]{3}", quoted[-1]):
                title = quoted[-1]
        title = title.strip("\"'“”‘’ ")
        if not title or (film and re.sub(r"[^a-z0-9]", "", title.lower()) == film):
            continue  # the film listed as one of its own extras
        kind = "extra" if it.kind == "extra" else "episode"
        show = (it.show or "").strip("\"'“”‘’ ") or None
        own = re.sub(r"[^a-z0-9]", "", (d.title or "").lower())
        if show and (kind != "extra" or _same_name(show, title)
                     or (own and re.sub(r"[^a-z0-9]", "", show.lower()) in own)):  # the disc's own show
            show = None  # not another show's episode, whatever the model says
        items.append(ListingItem(kind=kind, title=title[:300], number=it.number, show=show[:200] if show else None,
                                 runtime_min=it.runtime_min if it.runtime_min and it.runtime_min > 0 else None,
                                 extra_type=(it.extra_type if it.extra_type in EXTRAS_TYPES else "Extra")
                                 if kind == "extra" else None,
                                 disc=it.disc, source="vision"))
    audio = []
    for a in d.audio:
        m = LANGUAGE.search(a.language)
        if m:
            audio.append(AudioTrack(language=m.group(1).capitalize(), format=_format(a.format) or _format(a.language),
                                    channels=_channels(a.channels) or _channels(a.language)))
    subs = []
    for s in d.subtitles:
        m = LANGUAGE.search(s.language)
        if m:
            subs.append(SubtitleTrack(language=m.group(1).capitalize(),
                                      hard_of_hearing=s.hard_of_hearing or "hearing" in s.language.lower()))
    video = d.video_system if d.video_system in ("PAL", "NTSC") else None
    aspect = d.aspect_ratio if d.aspect_ratio and re.search(r"\d+(\.\d+)?\s*:\s*\d|widescreen|full ?screen",
                                                            d.aspect_ratio, re.I) else None
    barcode = re.sub(r"\D", "", d.barcode or "") or None
    details = ListingDetails(
        title=d.title, year=d.year if d.year and 1880 <= d.year <= 2100 else None,
        feature_runtime_min=d.feature_runtime_min if d.feature_runtime_min and d.feature_runtime_min > 0 else None,
        episode_count=d.episode_count if d.episode_count and d.episode_count > 0 else None,
        barcode=barcode if barcode and len(barcode) in (12, 13) else None,
        barcode_source="text" if barcode and len(barcode) in (12, 13) else None,
        catalogue_number=(d.catalogue_number or "").strip() or None, region=_region(d.region),
        video_system=video, aspect_ratio=aspect, audio=audio, subtitles=subs)
    return Listing(items=items, details=details)


def picture(img: Image.Image) -> bytes:
    """The picture as sent: at most MAX_PX, JPEG."""
    small = img.convert("RGB")
    small.thumbnail((MAX_PX, MAX_PX))
    out = io.BytesIO()
    small.save(out, "JPEG", quality=88)
    return out.getvalue()


def _json(text: str) -> dict:
    """The JSON in a reply, also when a model wraps it in a code fence."""
    text = text.strip()
    if m := re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S):
        text = m.group(1)
    try:
        out = json.loads(text)
    except json.JSONDecodeError as e:
        raise VisionError(f"the reply isn't JSON ({e.msg})") from None
    if not isinstance(out, dict):
        raise VisionError("the reply isn't a JSON object")
    return out


@dataclass
class Provider:
    """Where the vision model is. ``kind`` is local or openai (OpenAI-compatible, on the
    LAN or hosted) or anthropic."""

    kind: str
    url: str | None
    model: str
    key: str | None = None
    transport: object | None = None  # tests put a fake server here

    @property
    def cloud(self) -> bool:
        return self.kind != "local"

    @property
    def timeout(self) -> float:
        return CLOUD_TIMEOUT_S if self.cloud else LOCAL_TIMEOUT_S

    def describe(self) -> str:
        where = "Anthropic" if self.kind == "anthropic" else (self.url or "")
        return f"{self.model} at {where}"

    async def read(self, jpeg: bytes, prompt: str = PROMPT) -> tuple[dict, float]:
        """The model's reply (parsed JSON) and how long it took."""
        return await self.ask(prompt, jpeg)

    async def ask(self, prompt: str, jpeg: bytes | None = None, schema: dict = SCHEMA,
                  name: str = "listing") -> tuple[dict, float]:
        """The model's reply to ``prompt`` (and the picture, if any) as JSON in ``schema``,
        and how long it took. Without a picture, any chat model will do."""
        if not self.model:
            raise VisionError("no model is set")
        started = time.monotonic()
        if self.kind == "anthropic":
            out = await self._anthropic(jpeg, prompt, schema)
        else:
            out = await self._openai(jpeg, prompt, schema, name)
        return out, time.monotonic() - started

    async def _openai(self, jpeg: bytes | None, prompt: str, schema: dict, name: str) -> dict:
        if not self.url:
            raise VisionError("no address is set")
        base = self.url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        content: str | list = prompt
        if jpeg is not None:
            content = [{"type": "text", "text": prompt},
                       {"type": "image_url", "image_url": {
                           "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]
        body = {"model": self.model, "temperature": 0, "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": content}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": name, "strict": True, "schema": schema}}}
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as c:
                r = await c.post(f"{base}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException:
            raise VisionError(f"no answer within {self.timeout:.0f} s") from None
        except httpx.HTTPError as e:
            raise VisionError(f"couldn't reach {base} ({type(e).__name__})") from None
        if r.status_code in (401, 403):
            raise VisionError(f"{base} refused the key (HTTP {r.status_code})")
        if r.status_code == 404:
            raise VisionError(f"{base} has no model {self.model} (HTTP 404)")
        if r.status_code >= 400:
            raise VisionError(f"{base} answered HTTP {r.status_code}: {r.text[:200]}")
        try:
            choice = r.json()["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise VisionError("the reply isn't a chat completion") from None
        if choice.get("finish_reason") == "length":
            raise VisionError("the model ran out of room before finishing (a 'thinking' model? use an "
                              "instruct one)")
        return _json(text)

    async def _anthropic(self, jpeg: bytes | None, prompt: str, schema: dict) -> dict:
        import anthropic

        kw = {"api_key": self.key, "timeout": self.timeout, "max_retries": 1}
        if self.url:
            kw["base_url"] = self.url
        if self.transport is not None:
            kw["http_client"] = anthropic.DefaultAsyncHttpxClient(transport=self.transport)
        client = anthropic.AsyncAnthropic(**kw)
        content: list[dict] = [{"type": "text", "text": prompt}]
        if jpeg is not None:
            content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                           "data": base64.b64encode(jpeg).decode()}})
        try:
            response = await client.messages.create(
                model=self.model, max_tokens=MAX_TOKENS,
                output_config={"format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": content}])
        except anthropic.AuthenticationError:
            raise VisionError("Anthropic refused the key") from None
        except anthropic.NotFoundError:
            raise VisionError(f"Anthropic has no model {self.model}") from None
        except anthropic.RateLimitError:
            raise VisionError("Anthropic's rate limit was reached; try again later") from None
        except anthropic.APIStatusError as e:
            raise VisionError(f"Anthropic answered HTTP {e.status_code}: {e.message[:200]}") from None
        except anthropic.APITimeoutError:
            raise VisionError(f"no answer within {self.timeout:.0f} s") from None
        except anthropic.APIConnectionError:
            raise VisionError("couldn't reach Anthropic") from None
        finally:
            await client.close()
        if response.stop_reason == "refusal":
            raise VisionError("the model declined to answer")
        if response.stop_reason == "max_tokens":
            raise VisionError("the model ran out of room before finishing")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _json(text)


async def read_listing(provider: Provider, img: Image.Image) -> tuple[Listing, float]:
    raw, secs = await provider.read(picture(img))
    return tidy(raw), secs


def test_card() -> Image.Image:
    """A tiny back cover for the Test button: a heading, one extra and a runtime."""
    img = Image.new("RGB", (640, 360), (250, 250, 248))
    d = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont
        big, small = ImageFont.load_default(34), ImageFont.load_default(24)
    except TypeError:  # pragma: no cover - Pillow before 10.1
        big = small = None
    d.text((30, 30), "SPECIAL FEATURES", fill=(20, 20, 20), font=big)
    d.text((30, 100), "- Theatrical trailer", fill=(20, 20, 20), font=small)
    d.text((30, 260), "Running time: 95 mins approx", fill=(20, 20, 20), font=small)
    return img


async def check(provider: Provider) -> tuple[bool, str, list[str]]:
    """The Test button: read the test card and see a listing come back."""
    try:
        listing, secs = await read_listing(provider, test_card())
    except VisionError as e:
        return False, str(e)[:1].upper() + str(e)[1:], []
    found = [f"{i.title} ({i.extra_type or i.kind})" for i in listing.items]
    details = [f"Took {secs:.0f} s", "Read: " + (", ".join(found) or "no items")]
    if listing.details.feature_runtime_min:
        details.append(f"Running time: {listing.details.feature_runtime_min:g} min")
    ok = any("trailer" in i.title.lower() for i in listing.items)
    return ok, (f"{provider.describe()} read the test card" if ok else
                f"{provider.describe()} answered, but didn't read the test card's trailer"), details
