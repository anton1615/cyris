"""The remaining news's units: headlines grouped by their titles' cosine, then batched.

A unit is what one paragraph citation names: one story, told by one or more articles.
"""

from collections.abc import Mapping

from cyris.domain.models import DigestItem
from cyris.domain.similarity import cosine

# Grade A (docs/architecture.md §5). Measured once, on gemini-embedding-001: the
# milestone's MVP of 2026-10-11 found real story groups at 0.70 (one reception, 9
# reports; one resignation, 6). A cosine is only comparable within the model it was
# measured on (ADR-0006), so a run embedding with any other model groups nothing.
GROUPING_COSINE: dict[str, float] = {"gemini-embedding-001": 0.70}

# Grade A: the MVP's batch, which wrote every unit of an area into readable
# paragraphs. Bounds each paragraph prompt, as filtering.BATCH_SIZE bounds the filter's.
UNITS_PER_BATCH = 40


def group_by_cosine(
    keys: list[str], vectors: Mapping[str, list[float]], threshold: float
) -> list[list[str]]:
    """Greedy groups, in first-seen order.

    Each key joins the first group whose leader (first member) is at least
    `threshold` close to it, else leads a group of its own. Closeness is to the
    leader only, so a group cannot drift along a chain of near neighbours. A key
    with no vector stands alone.
    """
    groups: list[list[str]] = []
    for key in keys:
        vector = vectors.get(key)
        home = None
        if vector:
            home = next(
                (
                    group
                    for group in groups
                    if vectors.get(group[0]) and cosine(vectors[group[0]], vector) >= threshold
                ),
                None,
            )
        if home is None:
            groups.append([key])
        else:
            home.append(key)
    return groups


def batches[T](units: list[T], size: int) -> list[list[T]]:
    """Consecutive slices of at most `size`, in order."""
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    return [units[start : start + size] for start in range(0, len(units), size)]


def merge_unit(members: list[DigestItem]) -> DigestItem:
    """One item for a whole unit: every member's links and sources, in lockstep.

    Titled by its first member. Reference links are merged only when a member has
    any, a member without contributing its own http URL, as a news cluster's are.
    """
    scores = [m.score for m in members if m.score is not None]
    ref_urls: list[str] = []
    if any(m.ref_urls for m in members):
        ref_urls = list(
            dict.fromkeys(
                url
                for m in members
                for url in (
                    m.ref_urls or [u for u in m.urls if u.startswith(("http://", "https://"))]
                )
            )
        )
    return DigestItem(
        title=members[0].title,
        summary="",
        sources=[s for m in members for s in m.sources],
        urls=[u for m in members for u in m.urls],
        ref_urls=ref_urls,
        score=max(scores) if scores else None,
        passed_through=any(m.passed_through for m in members),
        area=members[0].area,
    )
