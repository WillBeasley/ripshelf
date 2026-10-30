"""Export the API models as one JSON Schema (input for the UI's generated types)."""

from __future__ import annotations

import json
import sys

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from .. import models
from ..makemkv.parse import RipProgress


def api_models() -> list[type[BaseModel]]:
    out = [RipProgress]
    for v in vars(models).values():
        if isinstance(v, type) and issubclass(v, BaseModel) and v is not BaseModel \
                and v.__module__ == models.__name__:
            out.append(v)
    return out


# In request bodies, fields with defaults may be left out. Every other model is a
# response, where the server always sends every field.
REQUESTS = {"SettingsUpdate", "ConfirmRequest", "SearchRequest", "NewRemoteWorkerRequest"}


def _tidy(node):
    """Drop per-field titles and defaults, which make json2ts emit one alias type per field."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for v in props.values():
                if isinstance(v, dict):
                    v.pop("title", None)
                    v.pop("default", None)
        for v in node.values():
            _tidy(v)
    elif isinstance(node, list):
        for v in node:
            _tidy(v)


def schema() -> dict:
    _, defs = models_json_schema([(m, "serialization") for m in api_models()],
                                 ref_template="#/$defs/{model}")
    # A root that references every model, so json-schema-to-typescript emits them all.
    for name, d in defs["$defs"].items():
        if name not in REQUESTS and "properties" in d:
            d["required"] = list(d["properties"])
    _tidy(defs["$defs"])
    names = sorted(defs["$defs"])
    return {
        "title": "Api",
        "type": "object",
        "properties": {n: {"$ref": f"#/$defs/{n}"} for n in names},
        "$defs": defs["$defs"],
    }


if __name__ == "__main__":
    json.dump(schema(), sys.stdout, indent=2)
