---
description: Pick the next ledger Task, load its spec, and plan the work
argument-hint: "[task-id, e.g. M0-04]"
---

Start an implementation session following docs/ledger/README.md §4.

1. Read `CLAUDE.md`, `CONTEXT.md` and `PROGRESS.md` (current state, open questions, last handoff).
2. Read `docs/ledger/README.md` and the current milestone file named in PROGRESS.md.
3. If a Task ID was given ($ARGUMENTS), use it. Otherwise list Tasks that are `[ ] Not started`
   and whose every **Depends on** entry is `[x] Done`, lowest Wave first, and pick one.
4. Check it isn't claimed: `git branch -a` and, if a GitHub remote exists, `gh pr list --search "<id>"`.
   If claimed, pick another.
5. Read the Task's **Spec** and every doc/ADR section it cites, in full.
6. Report back briefly: Task ID and title, why it's unblocked, the Owns paths, the acceptance
   criteria as a checklist, any spec ambiguity or conflict found (propose the doc fix), and a
   short implementation plan with the tests that will prove each criterion.
7. Create branch `task/<id-lowercase>-<slug>` once the plan is clear, then implement inside the
   Owns paths only.
