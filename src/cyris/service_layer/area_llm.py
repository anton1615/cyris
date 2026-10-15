"""Model-backed area suggestions for /settings: one source at a time, or every source at once.

Each function takes the LLM client it is given and returns what the model named, matched
against the names it was offered. Failures are the caller's to report; nothing here logs.
"""

import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from cyris.domain.areas import fold_name
from cyris.domain.models import SourceConfig
from cyris.service_layer.ports import LLMClient, complete_json
from cyris.service_layer.prompts import (
    PROPOSE_AREAS_SYSTEM,
    build_propose_areas_prompt,
    build_suggest_area_prompt,
    build_suggest_area_system_prompt,
)


def _host(url: str) -> str:
    """Only the host goes to the model: a feed URL can carry a token in its path or query."""
    return urlsplit(url).hostname or ""


async def suggest_area(
    llm: LLMClient,
    source: SourceConfig,
    area_names: Sequence[str],
) -> str | None:
    """The one active area the model files `source` under, or None when it picks none of them."""
    prompt = build_suggest_area_prompt(
        name=source.name,
        url=_host(source.url or ""),
        source_type=source.type,
        tags=source.tags,
        homepage=source.homepage,
    )
    data = await complete_json(llm, prompt, system=build_suggest_area_system_prompt(area_names))
    picked = data.get("area") if isinstance(data, dict) else None
    if not isinstance(picked, str):
        return None
    wanted = fold_name(picked)
    return next((name for name in area_names if fold_name(name) == wanted), None)


async def propose_areas(
    llm: LLMClient,
    sources: Mapping[str, SourceConfig],
    *,
    titles: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """The areas the model groups `sources` into, each with its source names, in the model's order.

    A name that is not one of `sources` is dropped, and so is an area that gets no source.
    Names that differ only in case or outer space are one area, its sources united.
    """
    prompt = build_propose_areas_prompt(
        [(s.name, _host(s.url or ""), s.tags) for s in sources.values()], titles=titles
    )
    data = await complete_json(llm, prompt, system=PROPOSE_AREAS_SYSTEM)
    if not isinstance(data, dict):
        raise ValueError("the model's reply is not an object")
    grouping: dict[str, list[str]] = {}
    spelling: dict[str, str] = {}
    for area, named in data.items():
        if not isinstance(area, str) or not isinstance(named, list):
            continue
        area = re.sub(r"[\r\n]+", " ", area).strip()
        if not area:
            continue
        members = [n for n in named if isinstance(n, str) and n in sources]
        if members:
            first = spelling.setdefault(fold_name(area), area)
            united = grouping.setdefault(first, [])
            united.extend(n for n in members if n not in united)
    return grouping
