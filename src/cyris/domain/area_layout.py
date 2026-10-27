"""The reader's layout of an issue: one read-only view over the stored content, by area.

`layout_by_area` places each card, group, cluster, paragraph and row under its area. It is the
only placement rule, so the page and the quota (`cap_lower_layers`) cannot disagree.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from cyris.domain.areas import fold_name
from cyris.domain.models import DigestContent, DigestItem, DigestSection, NewsParagraph
from cyris.domain.selection import _by_score


@dataclass(frozen=True)
class LowerRow:
    item: DigestItem
    kind: Literal["radar", "wire"]

    @property
    def untranslated(self) -> bool:
        return self.kind == "radar" or self.item.passed_through


@dataclass(frozen=True)
class AreaSection:
    # "named": `label` is the reader's area. "unplaced": entries no active area claims
    # (no area, or one renamed or dismissed since the issue was stamped); `label` is None
    # and the surfaces title it. "unnamed": the issue has no area order at all.
    label: str | None
    role: Literal["named", "unplaced", "unnamed"]
    features: tuple[DigestSection, ...] = ()
    following: tuple[DigestSection, ...] = ()
    clusters: tuple[DigestSection, ...] = ()
    paragraphs: tuple[NewsParagraph, ...] = ()
    rows: tuple[LowerRow, ...] = ()

    @property
    def lower(self) -> tuple[NewsParagraph | DigestSection | LowerRow, ...]:
        """The lower layer in page order, the entries a quota counts.

        The read lane's radar rows come before the news paragraphs, as the issue cap
        never lets news crowd out the read lane. An issue from before the paragraphs
        keeps its own order: clusters, then radar and headline rows.
        """
        radar = tuple(r for r in self.rows if r.kind == "radar")
        wire = tuple(r for r in self.rows if r.kind == "wire")
        return (*self.clusters, *radar, *self.paragraphs, *wire)


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
    paragraphs: list[NewsParagraph] = field(default_factory=list)
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
        if not content.area_order or key == unplaced:
            return None
        return content.area_order[key]

    def role(key: int) -> Literal["named", "unplaced", "unnamed"]:
        if not content.area_order:
            return "unnamed"
        return "named" if key < unplaced else "unplaced"

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
    for paragraph in content.news_paragraphs:
        if paragraph.units:
            buckets[place(paragraph.units[0].area)].paragraphs.append(paragraph)
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
                role=role(key),
                features=tuple(bucket.features),
                following=tuple(bucket.following),
                clusters=tuple(bucket.clusters),
                paragraphs=tuple(bucket.paragraphs),
                rows=(
                    *(
                        LowerRow(item, "radar")
                        for item in sorted(bucket.radar, key=_by_score, reverse=True)
                    ),
                    *(LowerRow(item, "wire") for item in bucket.headlines),
                ),
            )
        )
    return AreaLayout(
        headline=headline,
        tracking=tuple(content.tracked_topics),
        sections=tuple(sections),
    )


# Each named area shows at most this many lower entries, a news paragraph counting as
# one. Milestone L4's measure: a 15-paragraph area reads as a wall of text; 5 is the
# reader's pick (N7, 2026-10-15). Fixed until L6.
LOWER_ROWS_PER_AREA = 5


def cap_lower_layers(
    content: DigestContent, rows_per_area: int = LOWER_ROWS_PER_AREA
) -> DigestContent:
    """A copy with each named area's lower layer cut to its first `rows_per_area` rows.

    The cut uses the layout's own placement and row order, so `layout_by_area` of the
    result shows no area past the cap. Entries under no named area are never cut.
    """
    if rows_per_area < 0:
        raise ValueError(f"rows_per_area must be >= 0, got {rows_per_area}")
    removed: set[int] = set()
    removed_articles = 0
    for section in layout_by_area(content).sections:
        if section.role != "named":
            continue
        for entry in section.lower[rows_per_area:]:
            removed.add(_id_of(entry))
            removed_articles += _items_of(entry)
    return _without(content, removed, removed_articles)


def cap_unplaced_news(content: DigestContent, max_items: int) -> DigestContent:
    """A copy whose news outside every named area is cut from its tail to the issue cap.

    News is not counted by the issue cap and only a named area's quota cuts it, so news
    in the unnamed section, under "Other", or in an issue the stamp never reached would
    run unbounded. Here it shares the cap with the read lane, by the owner's decision of
    2026-10-27: unplaced news entries are dropped, last first, while the read lane's
    items and the unplaced news together count more than `max_items`. News in a named
    area and the follow lane take no slot.
    """
    unplaced = [
        entry
        for section in layout_by_area(content).sections
        if section.role != "named"
        for entry in section.lower
        if not (isinstance(entry, LowerRow) and entry.kind == "radar")
    ]
    read_lane = sum(
        len(section.items)
        for section in (
            *content.featured_articles,
            *content.thematic_summaries,
            *content.attention_sections,
        )
    )
    over = read_lane + sum(_items_of(entry) for entry in unplaced) - max_items
    removed: set[int] = set()
    removed_articles = 0
    for entry in reversed(unplaced):
        if removed_articles >= over:
            break
        removed.add(_id_of(entry))
        removed_articles += _items_of(entry)
    return _without(content, removed, removed_articles)


def _id_of(entry: "NewsParagraph | DigestSection | LowerRow") -> int:
    """The id `_without` finds an entry by: a row's item, else the entry itself."""
    return id(entry.item) if isinstance(entry, LowerRow) else id(entry)


def _items_of(entry: "NewsParagraph | DigestSection | LowerRow") -> int:
    """The digest items an entry counted for in `articles_included`."""
    if isinstance(entry, LowerRow):
        return 1
    if isinstance(entry, NewsParagraph):
        return entry.items or 0
    return len(entry.items)


def _without(content: DigestContent, removed: set[int], removed_articles: int) -> DigestContent:
    """A copy without the lower entries whose ids are in `removed`."""
    if not removed:
        return content.model_copy()

    attention = []
    for section in content.attention_sections:
        items = [item for item in section.items if id(item) not in removed]
        if items:
            attention.append(section.model_copy(update={"items": items}))
    return content.model_copy(
        update={
            "news_clusters": [s for s in content.news_clusters if id(s) not in removed],
            "news_paragraphs": [p for p in content.news_paragraphs if id(p) not in removed],
            "attention_sections": attention,
            "filtered_headlines": [i for i in content.filtered_headlines if id(i) not in removed],
            "articles_included": content.articles_included - removed_articles,
        }
    )
