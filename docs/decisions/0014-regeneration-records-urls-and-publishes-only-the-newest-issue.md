---
status: accepted
date: 2026-10-16
decision-makers: ""
---

# A regeneration records the URLs its run was given, and a publishing rerun publishes only the newest issue

## Context and Problem Statement

A stored issue could be re-rendered from D1 `digests`, but its raw companion page could not be
rebuilt, and nothing recorded which articles the pipeline had been given. So an issue whose
digest had to be regenerated, to fix a template or a wrong judgement, could not be reproduced,
and a republish linked no raw page.

Two questions had to be settled. What does a run record so that the issue can be rebuilt? And
what happens when a rerun publishes an issue that is not the newest one?

## Decision Drivers

* A rebuilt issue must show the articles the run was given, not whatever is in the feed today
* A rerun must not publish over a newer issue, nor let the page's "latest" lead point backwards
* The record must stay small and must not become a second copy of article state
* A dry regeneration must write nothing but its own run row

## Considered Options

* Record the URLs of each publishing run in D1 `digest_inputs` and rerun over them (chosen)
* Snapshot each article's full content and state at run time
* Re-fetch the sources for the period and rerun over what they return now
* Keep the raw page as a stored file next to the digest

## Decision Outcome

Chosen option: "record the URLs the run was given, and rerun over them, publishing only the
newest issue", because the URL list is enough to reconstruct the input set, costs one statement
per run, and leaves article state live.

The record is a `digest_inputs` row per issue, keyed `(date, period)`, last write wins. It holds
the URLs in the order the run took them, after the per-issue cap and the vote-similarity
suppression, and nothing else. A rerun reads each article by URL with its current state, so votes
and verdicts made since the run are visible to it.

A publishing rerun runs only when `is_latest_issue` says the issue is the newest by date and
period; otherwise it returns `not_latest` before any LLM call or any write but its run row. A
dry rerun never publishes and writes nothing but its run row. A publish counts as done only when
the live page serves the bytes just rendered.

### Consequences

* Good, because a rerun reproduces the input set with a single D1 statement per run
* Good, because article state is not duplicated, so a human verdict is not overwritten by a copy
* Good, because a rerun cannot move the public "latest" issue backwards
* Bad, because a rerun's verdicts and its articles' current states can differ from the original run
* Bad, because a row whose article `cyris articles clean` deleted drops out of the rerun
* Bad, because an issue recorded before this change has no record and cannot be rerun

### Confirmation

`tests/test_digest_input_store.py` holds the record's round trip and its size, and
`tests/test_publish_rerun.py` and `tests/test_rerun_digest.py` hold the newest-issue guard and the
rerun's inputs.

## Pros and Cons of the Options

### Snapshot each article's full content and state at run time

* Good, because a rerun reproduces the exact articles and verdicts of the original run
* Bad, because it duplicates article state that is already in D1 and goes stale on every vote
* Bad, because a snapshot of every article per run grows with the digest size

Rejected: the rerun's purpose is to rebuild the page from the input set, and a snapshot of
article state would override the human verdicts that a rerun has to respect.

### Re-fetch the sources for the period and rerun over what they return now

* Good, because it needs no record at all
* Bad, because feeds only keep a window, so the articles of an older period are gone

### Keep the raw page as a stored file next to the digest

* Good, because the page is exactly what was published
* Bad, because it is a second home for issue content outside `digests`, which `docs/architecture.md`
  §4 forbids without a row

## More Information

See `docs/architecture.md` §3.2 and §4 for the record's row and the rerun's place in the run.
