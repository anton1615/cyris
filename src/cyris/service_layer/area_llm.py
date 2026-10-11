"""Model-backed area suggestions for /settings: one source at a time, or every source at once.

Each function takes the LLM client it is given and returns what the model named, matched
against the names it was offered. Failures are the caller's to report; nothing here logs.
"""

from collections.abc import Sequence

from cyris.domain.models import SourceConfig
from cyris.service_layer.ports import LLMClient, complete_json
from cyris.service_layer.prompts import (
    build_suggest_area_prompt,
    build_suggest_area_system_prompt,
)


async def suggest_area(
    llm: LLMClient,
    source: SourceConfig,
    area_names: Sequence[str],
) -> str | None:
    """The one active area the model files `source` under, or None when it picks none of them."""
    prompt = build_suggest_area_prompt(
        name=source.name,
        url=source.url or "",
        source_type=source.type,
        tags=source.tags,
        homepage=source.homepage,
    )
    data = await complete_json(llm, prompt, system=build_suggest_area_system_prompt(area_names))
    picked = data.get("area") if isinstance(data, dict) else None
    if not isinstance(picked, str):
        return None
    wanted = picked.strip().casefold()
    return next((name for name in area_names if name.casefold() == wanted), None)
