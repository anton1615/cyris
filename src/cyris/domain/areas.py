"""Areas: the closed set of reader-facing topics, and what a name or source id means."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

AreaStatus = Literal["proposed", "active", "dismissed", "merged"]
PlacementKind = Literal["active", "proposed", "dismissed", "unknown"]


class Area(BaseModel):
    id: int
    name: str
    position: int
    status: AreaStatus
    merged_into: int | None
    proposal_articles: int
    proposal_issues: int
    last_proposed_run: str | None


class Placement(BaseModel):
    kind: PlacementKind
    area: Area | None


class AreaCatalog:
    def __init__(self, areas: Sequence[Area]) -> None:
        self._by_id = {area.id: area for area in areas}
        self._areas = list(areas)

    def names(self) -> list[str]:
        active = [area for area in self._areas if area.status == "active"]
        return [area.name for area in sorted(active, key=lambda area: area.position)]

    def place(self, name: str) -> Placement:
        wanted = name.strip().casefold()
        if not wanted:
            return Placement(kind="unknown", area=None)
        for area in self._areas:
            if area.name.strip().casefold() != wanted:
                continue
            if area.status == "merged":
                target = self._by_id.get(area.merged_into) if area.merged_into is not None else None
                if target is not None and target.status == "active":
                    return Placement(kind="active", area=target)
                return Placement(kind="dismissed", area=area)
            if area.status == "active":
                return Placement(kind="active", area=area)
            if area.status == "proposed":
                return Placement(kind="proposed", area=area)
            return Placement(kind="dismissed", area=area)
        return Placement(kind="unknown", area=None)

    def for_source(self, area_id: int | None) -> Area | None:
        if area_id is None:
            return None
        area = self._by_id.get(area_id)
        if area is not None and area.status == "active":
            return area
        return None
