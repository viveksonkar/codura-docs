# Implementation Ledger

**Status:** Draft v0.1 · **Last updated:** 2026-11-18 · **ADR:** 0001

The milestones in [roadmap.md](../roadmap.md) broken into Tasks small and bounded enough that
several agents can implement them at once without colliding.

| File | Milestone | Decomposition |
|---|---|---|
| [M0.md](./M0.md) | Foundations | Full |
| [M1.md](./M1.md) | Signup, roles, ingestion + OCR, answering, consoles, usage events | Full |
| [M2.md](./M2.md) | Web Channel (MVP): Hosted Chat Page + Widget + Visitor email | Full (done 2026-11-24) |
| [M3.md](./M3.md) | Monetisation: Plans, quotas, payments, finance, model management, Custom LLM | Full (done 2026-11-24; live Stripe run pending keys) |
| [M4.md](./M4.md) | WordPress plugin & Android | Full (decomposed at M3 exit) |
| [M5.md](./M5.md) | Quality & cost optimisation | Workstreams |
| [M6.md](./M6.md) | Production & observability | Workstreams |

Later milestones stay at workstream level on purpose: a Task is only as good as the spec it cites,
and those specs depend on what the previous milestone teaches us.

## 1. Task anatomy

```
### <M>-<NN> — <Title>
- **Wave:** <n>
- **Depends on:** <Task IDs | —>
- **Owns:** <paths this Task creates or edits>
- **Implements:** <FR/NFR/SEC/ADR IDs>
- **Spec:** <docs/specs/*.md section or doc section>
- **Acceptance criteria:**
  - <checkable condition>
- **Status:** [ ] Not started
```

- **Wave**: dependency tier within the milestone. Wave-0 Tasks start as soon as the previous
  milestone exits. A Wave-n Task depends only on Wave < n. If that's violated, fix the waves.
- **Owns**: a soft lock that keeps two agents off the same files. If the real boundary differs,
  update it in the same PR. Shared files (`docker-compose.yml`, `.env.example`, `pyproject.toml`,
  `registry.py`, router includes) may get **small additive edits** from any Task; note them in the PR.
- **Spec**: read it and every section it cites in full before coding.
- **Status**: `[ ] Not started` → `[x] Done` (with a note if something was deferred).

## 2. Freeze gates

A Task that depends on a spec-freezing Task (M0-03, M0-04, M1-00, M1-08) doesn't start until that
Task is **merged**, not "mostly done". From M1-18 on, the cross-Tenant probe suite is a required
check: no Task merges with it red.

## 3. Claiming and finishing

- **Claim**: branch `task/<id>-<slug>` (e.g. `task/m0-04-ports`), PR title starts with the Task ID.
  The branch/PR is the ground truth for "someone is on it"; check `git branch -a` / open PRs first.
- **Finish**: flip only your Task's checkbox in the same PR; append a session entry to
  `PROGRESS.md`; update "Current state" if the next Task changed.
- Solo, without PRs: commit on the task branch, merge to `main` when acceptance criteria pass.

## 4. How an agent starts (`/next-task` automates this)

1. Read `CLAUDE.md`, `CONTEXT.md`, `PROGRESS.md`.
2. Read this file and the current milestone file.
3. Pick an unclaimed `[ ] Not started` Task whose every dependency is `[x] Done`; prefer lower Waves.
4. Read its Spec and cited sections.
5. Implement only inside **Owns**; write tests that prove each acceptance criterion.
6. Run the checks from CLAUDE.md "Commands"; all must pass.
7. Flip the checkbox, update `PROGRESS.md`, and if the breakdown was wrong, fix the ledger in the
   same change (`/close-task`).
