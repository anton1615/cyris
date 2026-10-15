"""The reader's layout of an issue: one read-only view over the stored content, by area.

`layout_by_area` places each card, group, cluster and row under its area. It is the
only placement rule, so the page and the quota (`cap_lower_layers`) cannot disagree.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from cyris.domain.areas import fold_name
from cyris.domain.models import DigestContent, DigestItem, DigestSection
from cyris.domain.selection import _by_score

# The label of the section that closes an issue with a non-empty area order: entries no
# active area claims (no area, or one renamed or dismissed since the issue was stamped).
UNPLACED_LABEL = "Other"


@dataclass(frozen=True)
class AreaSection:
    label: str | None
    features: tuple[DigestSection, ...] = ()
    following: tuple[DigestSection, ...] = ()
    clusters: tuple[DigestSection, ...] = ()
    rows: tuple[DigestItem, ...] = ()


@dataclass(frozen=True)
class AreaLayout:
    headline: DigestSection | None
    tracking: tuple[DigestSection, ...]
    sections: tuple[AreaSection, ...]


@dataclass
class _Bucket:
    features: list[DigestSection] = field(default_factory=list)
    following: list[DigestSection] = field(default_factory=list)
    clusters: list[DigestSection] = field(default_factory=list)
    radar: list[DigestItem] = field(default_factory=list)
    headlines: list[DigestItem] = field(default_factory=list)


def layout_by_area(content: DigestContent) -> AreaLayout:
    index = {fold_name(name): i for i, name in enumerate(content.area_order)}
    unplaced = len(content.area_order)

    def place(area: str | None) -> int:
        if not content.area_order:
            return 0
        if area is None or fold_name(area) not in index:
            return unplaced
        return index[fold_name(area)]

    def label(key: int) -> str | None:
        if not content.area_order:
            return None
        return content.area_order[key] if key < unplaced else UNPLACED_LABEL

    buckets: dict[int, _Bucket] = defaultdict(_Bucket)

    def place_section(section: DigestSection, into: str) -> None:
        if not section.items:
            return
        getattr(buckets[place(section.items[0].area)], into).append(section)

    pool = [*content.featured_articles, *content.thematic_summaries]
    headline = pool[0] if pool else None
    for section in pool[1:]:
        place_section(section, "features")
    for section in content.fan_sections:
        place_section(section, "following")
    for section in content.news_clusters:
        place_section(section, "clusters")
    for section in content.attention_sections:
        for item in section.items:
            buckets[place(item.area)].radar.append(item)
    for item in content.filtered_headlines:
        buckets[place(item.area)].headlines.append(item)

    sections = []
    for key in sorted(buckets):
        bucket = buckets[key]
        sections.append(
            AreaSection(
                label=label(key),
                features=tuple(bucket.features),
                following=tuple(bucket.following),
                clusters=tuple(bucket.clusters),
                rows=(*sorted(bucket.radar, key=_by_score, reverse=True), *bucket.headlines),
            )
        )
    return AreaLayout(
        headline=headline,
        tracking=tuple(content.tracked_topics),
        sections=tuple(sections),
    )
