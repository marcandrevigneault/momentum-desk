"""Loader/validator for the curated congressional power-member allowlist.

``power.json`` is hand-curated (best-effort, web-researched — see its own
``sources``/``notes`` fields) rather than fetched live: there's no free,
reliable API for "who currently chairs which committee," and the design's
one robust conditioning signal (member power) only needs a stable,
reviewable list, not a real-time one. This module just loads + validates
that file's shape into the flat ``set[str]`` of kadoa ``filer_id`` slugs
that ``signals.build_events``'s ``power_only`` filter consumes.
"""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_PATH = Path(__file__).with_name("power.json")

_VALID_CHAMBERS = ("house", "senate")


def load_power(path: str | None = None) -> set[str]:
    """Load + flatten power.json's ``congresses -> chamber -> [filer_id]``
    tree into one set. Raises ValueError on any schema drift rather than
    silently returning an empty/partial set: this feeds a live strategy
    filter (``power_only``), so a silent mis-parse would produce a
    strategy that silently trades nothing instead of a loud failure at
    load time."""
    load_path = Path(path) if path is not None else _DEFAULT_PATH
    with load_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise ValueError("power.json: top-level value must be an object")

    congresses = data.get("congresses")
    if not isinstance(congresses, dict) or not congresses:
        raise ValueError("power.json: missing/empty 'congresses' object")

    out: set[str] = set()
    for congress_id, chambers in congresses.items():
        if not isinstance(chambers, dict):
            raise ValueError(f"power.json: congresses[{congress_id!r}] must be an object")
        for chamber, filer_ids in chambers.items():
            if chamber not in _VALID_CHAMBERS:
                raise ValueError(
                    f"power.json: congresses[{congress_id!r}] has unknown chamber key {chamber!r}"
                )
            if not isinstance(filer_ids, list) or not all(isinstance(f, str) for f in filer_ids):
                raise ValueError(
                    f"power.json: congresses[{congress_id!r}][{chamber!r}] must be a list of strings"
                )
            out.update(filer_ids)

    return out
