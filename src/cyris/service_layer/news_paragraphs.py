"""L4: the remaining news, grouped into units by title cosine and written as paragraphs.

Runs after the areas are stamped and before each area's quota cuts, so every unit of
an area is cited by some paragraph before the cut (milestone digest-by-topic-and-area,
N1 (c)). Article state is not touched: the filter still decides what is news here.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from cyris.domain.area_layout import layout_by_area
from cyris.domain.models import DigestContent, DigestItem, DigestSection, NewsParagraph, UsageStats
from cyris.domain.news_units import (
    GROUPING_COSINE,
    UNITS_PER_BATCH,
    batches,
    group_by_cosine,
    merge_unit,
)
from cyris.domain.selection import rank_by_preference
from cyris.service_layer.parse import echoed_position
from cyris.service_layer.ports import Embedder, LLMClient, complete_json
from cyris.service_layer.prompts import (
    build_news_paragraph_prompt,
    build_news_paragraph_system_prompt,
)

logger = logging.getLogger(__name__)

Unit = list[DigestItem]


@dataclass
class ParagraphReport:
    """What the paragraph step did, for `run_summary`."""

    units: int = 0
    paragraphs: int = 0
    calls: int = 0
    # Units the model left uncited, each given a paragraph of its own text.
    uncited: int = 0
    failed_batches: int = 0
    # Unit positions the vote preference changed (ADR-0022).
    moved: int = 0
    # "cosine", or why no headline was grouped.
    grouping: str = "cosine"
    # Titles this step embedded itself, the ones the run had not embedded already.
    embedded: int = 0
    # Per section label: units, calls and uncited units.
    areas: dict[str, dict[str, int]] = field(default_factory=dict)

    def as_summary(self) -> dict:
        return {
            "units": self.units,
            "paragraphs": self.paragraphs,
            "calls": self.calls,
            "uncited": self.uncited,
            "failed_batches": self.failed_batches,
            "grouping": self.grouping,
            "embedded": self.embedded,
            "areas": self.areas,
        }


async def write_news_paragraphs(
    content: DigestContent,
    llm: LLMClient | None,
    *,
    embedder: Embedder | None,
    embedding_model: str,
    known_vectors: Mapping[str, list[float]],
    titles: Mapping[str, str],
    preference: Mapping[str, float] | None,
    units_per_paragraph: int,
    output_language: str,
    style_prompt: str,
    usage: UsageStats | None = None,
) -> tuple[DigestContent, ParagraphReport]:
    """A copy whose news clusters and headlines are paragraphs, area by area.

    Within each section of the layout, a news cluster is one unit and the headlines
    are grouped greedily by their articles' title cosine. Units are ordered by their
    best member's preference, then written UNITS_PER_BATCH to a call. A unit the
    model leaves uncited, or whose batch failed or had no model, gets a paragraph of
    its own text, so every unit is cited exactly once. Never raises on the model or
    the embedder.

    Args:
        known_vectors: url -> title vector this run already embedded.
        titles: url -> the article's own title, embedded when its vector is unknown.
        preference: url -> the reader's vote preference; None or {} keeps the order.
    """
    report = ParagraphReport()
    sections = [
        (s, list(s.clusters), [r.item for r in s.rows if r.kind == "wire"])
        for s in layout_by_area(content).sections
    ]
    sections = [(s, c, h) for s, c, h in sections if c or h]
    if not sections:
        return content, report

    headlines = [h for _, _, hs in sections for h in hs]
    vectors, report.grouping, report.embedded = await _vectors(
        headlines, embedder, embedding_model, known_vectors, titles
    )
    threshold = GROUPING_COSINE.get(embedding_model)
    system = build_news_paragraph_system_prompt(output_language, style_prompt=style_prompt)

    paragraphs: list[NewsParagraph] = []
    for section, clusters, items in sections:
        units: list[Unit] = [list(cluster.items) for cluster in clusters]
        if vectors and threshold is not None:
            keys = [str(i) for i in range(len(items))]
            by_key = {
                k: vectors[i.urls[0]]
                for k, i in zip(keys, items, strict=True)
                if i.urls and i.urls[0] in vectors
            }
            units += [
                [items[int(k)] for k in group] for group in group_by_cosine(keys, by_key, threshold)
            ]
        else:
            units += [[item] for item in items]
        ranked = _ranked(units, preference or {})
        report.moved += sum(a is not b for a, b in zip(units, ranked, strict=True))

        label = section.label or section.role
        area = report.areas.setdefault(label, {"units": 0, "calls": 0, "uncited": 0})
        area["units"] += len(ranked)
        for batch in batches(ranked, UNITS_PER_BATCH):
            written, uncited, called = await _write_batch(
                batch, llm, system, units_per_paragraph, usage
            )
            paragraphs += written
            area["calls"] += called
            area["uncited"] += uncited
            report.calls += called
            report.uncited += uncited
            report.failed_batches += int(llm is not None and not called)

    report.units = sum(a["units"] for a in report.areas.values())
    report.paragraphs = len(paragraphs)
    logger.info(
        "News paragraphs: %d units in %d paragraphs, %d calls, %d uncited (%s)",
        report.units,
        report.paragraphs,
        report.calls,
        report.uncited,
        report.grouping,
    )
    return (
        content.model_copy(
            update={
                "news_clusters": [],
                "filtered_headlines": [],
                "news_paragraphs": paragraphs,
            }
        ),
        report,
    )


async def _vectors(
    headlines: list[DigestItem],
    embedder: Embedder | None,
    embedding_model: str,
    known: Mapping[str, list[float]],
    titles: Mapping[str, str],
) -> tuple[dict[str, list[float]], str, int]:
    """url -> title vector for every headline that can have one, the grouping note, and
    how many titles were embedded here."""
    if embedding_model not in GROUPING_COSINE:
        return {}, f"no grouping cosine measured for {embedding_model}", 0
    urls = [h.urls[0] for h in headlines if h.urls]
    vectors = {u: known[u] for u in urls if known.get(u)}
    missing = [u for u in urls if u not in vectors and u in titles]
    if not missing or embedder is None:
        return vectors, "cosine" if vectors else "no embedder", 0
    texts = list(dict.fromkeys(titles[u] for u in missing))
    try:
        embedded = await embedder.embed(texts)
        # Each vector is paired back to its title by position below.
        if len(embedded) != len(texts):
            raise ValueError("the embedder answered a different number of vectors than texts")
    except Exception as e:
        logger.warning("News grouping unavailable, every headline stands alone: %s", e)
        return {}, f"embedding failed: {e}", 0
    by_title = dict(zip(texts, embedded, strict=True))
    vectors.update({u: by_title[titles[u]] for u in missing if by_title[titles[u]]})
    return vectors, "cosine", len(texts)


def _ranked(units: list[Unit], preference: Mapping[str, float]) -> list[Unit]:
    """Units by their best member's preference; an unjudged unit keeps its slot."""
    wrapped = [DigestSection(heading="", items=unit) for unit in units]
    by_id = {id(w): unit for w, unit in zip(wrapped, units, strict=True)}
    return [by_id[id(w)] for w in rank_by_preference(wrapped, preference)]


def _own_paragraph(unit: Unit) -> NewsParagraph:
    """A unit's own text as its paragraph: each member's sentence, else its title."""
    return NewsParagraph(
        text=" ".join(m.summary or m.title for m in unit),
        units=[merge_unit(unit)],
        passed_through=any(m.passed_through for m in unit),
        items=len(unit),
    )


async def _write_batch(
    batch: list[Unit],
    llm: LLMClient | None,
    system: str,
    units_per_paragraph: int,
    usage: UsageStats | None,
) -> tuple[list[NewsParagraph], int, int]:
    """The batch's paragraphs, how many units were filled in uncited, and calls made.

    The paragraphs come in the batch's unit order, each placed by its best-ranked unit,
    whatever order the model wrote them in: the area quota cuts from the tail, so the
    vote order must still stand there (ADR-0022).
    """
    if llm is None:
        return [_own_paragraph(unit) for unit in batch], 0, 0
    prompt = build_news_paragraph_prompt(batch, units_per_paragraph=units_per_paragraph)
    try:
        data = await complete_json(llm, prompt, system=system, temperature=1.0, usage=usage)
    except Exception:
        logger.warning(
            "A news paragraph batch failed; its %d units stand as their own text",
            len(batch),
            exc_info=True,
        )
        return [_own_paragraph(unit) for unit in batch], 0, 0

    cited: set[int] = set()
    written: list[tuple[int, NewsParagraph]] = []
    raw_paragraphs = data.get("paragraphs") if isinstance(data, dict) else None
    for entry in raw_paragraphs if isinstance(raw_paragraphs, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        raw_units = entry.get("units")
        positions = []
        for raw in raw_units if isinstance(raw_units, list) else []:
            position = echoed_position(raw, len(batch))
            # One citation per unit: a repeat is dropped, so no article shows twice.
            if position is not None and position not in cited:
                cited.add(position)
                positions.append(position)
        if positions and entry["text"].strip():
            paragraph = NewsParagraph(
                text=entry["text"].strip(),
                units=[merge_unit(batch[p]) for p in positions],
                items=sum(len(batch[p]) for p in positions),
            )
            written.append((min(positions), paragraph))
        else:
            cited.difference_update(positions)
    # Subtraction, never the model's word, decides what is left: a unit it skipped
    # still reaches the page (cluster_news.py records why).
    left = [(i, _own_paragraph(unit)) for i, unit in enumerate(batch) if i not in cited]
    in_order = sorted(written + left, key=lambda placed: placed[0])
    return [paragraph for _, paragraph in in_order], len(left), 1
