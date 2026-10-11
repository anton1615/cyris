"""Stamp each digest item with its area, after the pipeline and before the save."""

from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel

from cyris.domain.areas import AreaCatalog
from cyris.domain.models import DigestContent, DigestItem, SourceConfig


class AreaStampCounts(BaseModel):
    """How many items took their area from the model's pick, their source, or neither."""

    picked: int = 0
    source: int = 0
    none: int = 0


def stamp_areas(
    content: DigestContent,
    *,
    placements: Mapping[str, str],
    sources: Mapping[str, SourceConfig],
    catalog: AreaCatalog,
) -> AreaStampCounts:
    """Set `area` on every item: the pick for its first article, else its source's area."""
    counts = AreaStampCounts()
    for item in _items(content):
        if item.urls and item.urls[0] in placements:
            item.area = placements[item.urls[0]]
            counts.picked += 1
            continue
        source = sources.get(item.sources[0]) if item.sources else None
        area = catalog.for_source(source.area_id) if source else None
        item.area = area.name if area else None
        if area:
            counts.source += 1
        else:
            counts.none += 1
    return counts


def _items(node: Any) -> Iterator[DigestItem]:
    """Every DigestItem under `node`, whatever field or list holds it."""
    if isinstance(node, DigestItem):
        yield node
    elif isinstance(node, BaseModel):
        for name in type(node).model_fields:
            yield from _items(getattr(node, name))
    elif isinstance(node, list | tuple):
        for child in node:
            yield from _items(child)
    elif isinstance(node, dict):
        for child in node.values():
            yield from _items(child)
