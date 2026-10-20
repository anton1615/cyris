---
status: accepted
date: 2026-10-20
decision-makers: ""
---

# New deployments start from onboarding, not from default feeds

## Context and Problem Statement

A new deployment used to start with feeds someone else chose. The trial wizard
(`scripts/trial-wizard.sh`) copied an example `sources.yaml` into every trial and pushed it at
the first boot, then started a run as soon as the trial was handed over; `sources.example.yaml`
and both install guides told a deployer to copy that same file as the starting point.

The first trial tester's feedback, recorded in `cold-start-onboarding.md`, is that this start
missed: the feeds they were given were mostly ones they had no interest in, and it took several
rounds of votes to steer the digest towards what they read. The trial's own D1 showed the cost,
one vote cast between the trial's start and 2026-10-18. Default feeds also make the first issue,
the one a new reader judges the product by, an issue about somebody else's interests.

The same milestone built the alternative: `/onboarding`, a wizard in which the reader picks
feeds from a project-collected list of publishers' own feeds (`src/cyris/onboarding_feeds.json`),
votes on a few older articles from them, and starts the first issue. An empty `sources` table
became a normal state, "waiting for onboarding", rather than an error. Whether a new deployment
should still arrive with feeds already in it had to be settled.

## Considered Options

* Keep shipping default feeds (the trial wizard's copy, `sources.example.yaml`)
* Ship no feed and have the reader pick them in onboarding
* Ship default feeds and let onboarding replace them

## Decision Outcome

Chosen option: "ship no feed and have the reader pick them in onboarding", because the reader
is the only one who knows what they read, and onboarding asks them before the first issue
rather than after several bad ones.

* The trial wizard pushes the settings alone at the first boot and no longer starts a run: the
  tester picks feeds in `/onboarding`, whose last step starts the first issue.
* `sources.example.yaml` holds no feed. It documents the format and points a `json` deployer,
  who has no wizard, at the list's entries as examples to copy by hand.
* The install guides say a new D1 deployment starts with an empty `sources` table and that
  `/settings` sends it to `/onboarding`.
* The feed list is read only by the onboarding step, only while `sources` is empty. Once the
  reader has picked, the deployment's feeds live in its own D1 `sources` like any other.
* The wizard's last step starts the first issue at once, whatever the hour, under a key no
  scheduled run that day takes: today's latest publish hour already begun, or yesterday's
  evening while both of today's are still to come. For that, a manual run may name its issue's
  date (`POST /run?period=&date=`, `cyris run --date`), today or yesterday only. The owner
  chose yesterday's evening on 2026-10-20 over leaving the first issue to the morning's tick.

### Consequences

* Good, because the first issue is built from feeds and votes the reader chose.
* Good, because "waiting for onboarding" is the one start state: no deployment has feeds
  nobody chose, which would also be fetched, scored and paid for.
* Bad, because a reader who skips the sources step has a deployment that fetches nothing until
  they add a feed on `/settings`. Every run says so in its log; none raises an alert.
* Bad, because a `json` deployment, which has no wizard, now has to write its own
  `sources.yaml` before its first run produces anything.
* Bad, because a first issue built before the morning hour is dated yesterday: its archive
  row, its page's URL, and its Discord message's and mail's subjects all carry yesterday's
  date, for an issue the reader has just made. Known and accepted: the alternative is waiting
  for the morning's tick, or a key a tick would overwrite.
* Neutral, because a manual run can now name a date, which only the onboarding step uses. The
  app Worker takes only today or yesterday, and when it cannot read the deployment's time zone
  from D1 it refuses the dated run with 503 rather than take a date it cannot check, so the
  first issue waits for D1 to answer.
* Neutral, because the list is now part of the product: a stale entry costs a new reader one
  empty pick, and the milestone's "清單失效得比發版快" is the trigger for publishing it
  outside the release instead.

### Confirmation

`tests/test_trial_wizard_guard.py` refuses a wizard that pushes sources, writes a source entry
or posts `/run`, and `tests/test_trial_wizard.py` walks a trial from the first boot to the
receipts with no source pushed and no run started. The owner's check on a real trial is a fresh
deployment taken from an empty `sources` table through onboarding to a `digest_runs` row with
`status = ok`.

## Pros and Cons of the Options

### Keep shipping default feeds

* Good, because a new deployment produces an issue at its first publish hour with no input.
* Bad, because that issue is about the feeds' chooser, and the first trial showed a reader does
  not vote their way out of it quickly.
* Bad, because the defaults are one person's reading, in one language, presented as everyone's.

### Ship no feed and have the reader pick them in onboarding

* Good, because the reader's own choice and votes shape the first issue.
* Bad, because the first issue now waits on the reader finishing a wizard.

### Ship default feeds and let onboarding replace them

* Good, because a reader who skips onboarding still gets an issue.
* Bad, because the wizard's list shows only while `sources` is empty, so it would never show;
  making it show anyway means a second rule for which feeds are "default" and may be removed.
* Bad, because a reader who skips the step keeps feeds they did not choose, which is the
  complaint this decision answers.

## More Information

* [cold-start-onboarding.md](../milestones/cold-start-onboarding.md) holds the tester's feedback,
  the wizard's design and the list's collection rules; its "什麼會推翻這個決定" lists what would
  reverse this, among them testers whose publishers are missing from the list.
* [architecture.md §5](../architecture.md) grades the feed list A and describes the empty
  `sources` table as the waiting-for-onboarding state.
* [ADR-0019](0019-the-preference-order-extends-to-news-clusters.md) is the other decision from
  the same milestone: the onboarding votes order the news clusters too.
