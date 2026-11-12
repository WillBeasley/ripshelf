"""A pretend vision model for UI development and tests: an OpenAI-compatible endpoint
(``/fake-vision/v1/chat/completions``) and an Anthropic one (``/fake-anthropic/v1/messages``).

Every picture "reads" as the pretend scanner's Moonlit Orchard sheet: its three extras,
and the details a vision model reads off the box that Tesseract can't (the region in the
globe, the PAL logo), plus one line the box doesn't have, as models sometimes invent.
A request without a picture asks what a disc label stands for, and gets ``label``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from fastapi import APIRouter, Request, Response

READING = {
    "items": [
        {"kind": "extra", "title": "Growing the Orchard", "number": None, "runtime_min": 22,
         "extra_type": "Featurette", "disc": None, "show": None},
        {"kind": "extra", "title": "Deleted scenes", "number": None, "runtime_min": None,
         "extra_type": "DeletedScene", "disc": None, "show": None},
        {"kind": "extra", "title": "Theatrical trailer", "number": None, "runtime_min": None,
         "extra_type": "Trailer", "disc": None, "show": None},
        {"kind": "extra", "title": "Orchard photo gallery", "number": None, "runtime_min": None,
         "extra_type": "Other", "disc": None, "show": None},
    ],
    "details": {"title": "Moonlit Orchard", "year": 1998, "feature_runtime_min": 112, "episode_count": None,
                "barcode": "5012345678900", "catalogue_number": "40112DVD", "region": "Region 2",
                "video_system": "PAL", "aspect_ratio": "16:9",
                "audio": [{"language": "English", "format": "Dolby Digital", "channels": "5.1"}],
                "subtitles": [{"language": "English", "hard_of_hearing": True}]},
}


@dataclass
class FakeVision:
    reading: dict = field(default_factory=lambda: json.loads(json.dumps(READING)))
    delay_s: float = 0.0
    status: int = 200  # answer with this instead, to test failures
    raw: str | None = None  # answer with this text instead of the reading
    requests: list[dict] = field(default_factory=list)
    # The reading of every disc label (BTTF2 is the Settings test's label).
    label: dict = field(default_factory=lambda: {"titles": ["Back to the Future Part II", "Moonlit Orchard"],
                                                 "kind": "movie"})

    def _answer(self, picture: bool) -> str:
        if self.raw is not None:
            return self.raw
        return json.dumps(self.reading if picture else self.label)

    def router(self) -> APIRouter:
        r = APIRouter()

        @r.post("/fake-vision/v1/chat/completions")
        async def openai(request: Request):
            body = await request.json()
            self.requests.append({"api": "openai", "body": body, "auth": request.headers.get("authorization")})
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.status != 200:
                return Response(json.dumps({"error": {"message": "pretend failure"}}), self.status,
                                media_type="application/json")
            content = body["messages"][0]["content"]
            text = self._answer(isinstance(content, list) and any(c.get("type") == "image_url"
                                                                          for c in content))
            return {"id": "chatcmpl-fake", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": text}}],
                    "usage": {"prompt_tokens": 1900, "completion_tokens": 300, "total_tokens": 2200}}

        @r.post("/fake-anthropic/v1/messages")
        async def anthropic(request: Request):
            body = await request.json()
            self.requests.append({"api": "anthropic", "body": body, "auth": request.headers.get("x-api-key") or request.headers.get("authorization")})
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.status != 200:
                kind = {401: "authentication_error", 404: "not_found_error", 429: "rate_limit_error"}.get(
                    self.status, "api_error")
                return Response(json.dumps({"type": "error", "error": {"type": kind, "message": "pretend failure"}}),
                                self.status, media_type="application/json")
            text = self._answer(any(c.get("type") == "image" for c in body["messages"][0]["content"]))
            return {"id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model"),
                    "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                    "stop_sequence": None, "usage": {"input_tokens": 1900, "output_tokens": 300}}

        return r
