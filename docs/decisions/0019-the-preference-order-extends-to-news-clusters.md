---
status: accepted
date: 2026-10-20
decision-makers: ""
---

# The preference order extends to news clusters

## Context and Problem Statement

The vote order (`[digest] rank_by_preference`, architecture.md §3) ordered two of the issue's
layers: the Features and the headlines. The news clusters were left in the order the clustering
model emitted them and the cap cut them from the tail, so a reader's votes could not save a
cluster, and on a heavy news day the cluster the reader voted into was as likely to be dropped as
any other.

`cold-start-onboarding.md` makes votes the first thing a new reader does: the onboarding wizard
collects a handful of 👍/👎 on recent articles and the first issue is built from them. If the
clusters ignore those votes, a reader whose interests are news — the layer wire sources feed —
sees no effect from voting at all, which is the complaint the milestone starts from.

Three things had to be settled: whether the clusters are ordered on every deployment or only once
a reader has voted enough, what score stands for a cluster of several articles, and whether the
clusters' quota against the headlines changes with the order.

## Considered Options

* Leave the clusters in the model's order (the status quo)
* Order the clusters by their best-scoring member before the cut, everywhere
* Order the clusters only above some number of stored votes
* Reserve the first few model-ordered clusters and order the rest
* Give the clusters a quota of their own, taken from the headlines

## Decision Outcome

Chosen option: "order the clusters by their best-scoring member before the cut, everywhere",
because it is the smallest change that makes a vote visible in the news layer, and it reuses the
ordering the Features and headlines already go through (`rank_by_preference`).

* The clusters are ordered before truncation, so the order decides which cluster the cap keeps
  whole, which it cuts mid-way and which it drops.
* A cluster's score is the highest preference among **every** member the clustering step gave it,
  including members the output cap later drops. The whole cluster is one entry: a cluster whose
  best article the reader is close to leads, and the members inside it keep the model's order.
* Every deployment, production included. A vote-count threshold would mean the feature is off
  exactly where it was asked for — a new reader with a handful of onboarding votes — and
  "enough votes" is a number nobody has data for yet.
* The quotas do not move. The clusters stay priority 2 and the headlines priority 4
  (`domain/selection.py`); this decision changes the order inside the clusters' existing slots and
  nothing about how many slots they get.
* An unjudged cluster keeps its slot, as an unjudged headline does, and with no votes or with
  vote similarity off or skipped every cluster keeps the model's order. The order rejects nothing
  and stamps no `triaged_at`.

### Consequences

* Good, because a vote now reaches the layer most of a news reader's issue is made of.
* Good, because it is one ordering rule for the whole issue, not a second one for clusters.
* Bad, because it changes what production readers see with no setting to turn it off: the
  only way back is a revert.
* Bad, because `preference_source = clef` now asks one question per cluster member, not per
  kept headline and Feature. The pass holds `CLEF_PASS_SECONDS = 60` with
  `CLEF_CONCURRENCY = 8` in flight (`service_layer/vote_order.py`), and a pass that runs past it
  falls the whole issue back to the cosine order. No cap on the asked members is added here:
  a cap would silently order part of the issue on one scale, and the budget is a measurement
  the owner has not taken yet.
* Neutral, because "the best member represents the cluster" is a proof-of-concept choice.
  `cold-start-onboarding.md` lists comparing it against other representatives (mean, the
  cluster's lead article) on real data as open.

### Confirmation

`run_summary` gains `preference_rank_moved_clusters` beside `_headlines` and `_features`: the
number of cluster positions the order changed. Two rollback triggers in the milestone are read
off it — a production issue whose cluster order got worse, and major news (the typhoon case)
pushed past the cut — and each would be answered by reserving the first model-ordered clusters,
the option above this one.

`tests/test_selection.py` pins that a cluster holding a voted topic survives a cap too small for
every cluster, and that with no preference the clusters keep the model's order.
`tests/test_digest_pipeline.py` pins that every cluster member reaches the preference.

## More Information

* [architecture.md §3](../architecture.md) describes the order the clusters now join.
* [cold-start-onboarding.md](../milestones/cold-start-onboarding.md) is where this came from:
  "投票的偏好排序延伸到新聞分群", and its "什麼會推翻這個決定" lists what would reverse it.
* [ADR-0006](0006-embedding-thresholds-are-per-model-calibrations.md) is why the cosine behind
  the preference is only comparable within one embedding model.
