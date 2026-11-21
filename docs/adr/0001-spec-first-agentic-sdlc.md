# ADR-0001 — Spec-first agentic SDLC with ledger, ADRs and progress log

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** all

## Context

Most implementation will be done by AI coding agents, often several at once, across four
languages. Agents start every session cold. Without a shared, written design they guess shapes
differently, collide on files, and silently drift from intent. The sibling `codura-agents` repo
already uses a spec-first approach that works.

## Decision

1. `docs/` is the source of truth; code follows it, and a change that diverges updates the doc in
   the same PR.
2. `CONTEXT.md` is a binding glossary.
3. Requirements carry stable IDs; decisions are ADR files in `docs/adr/`.
4. Shared boundaries are frozen in `docs/specs/` before dependent work starts.
5. Work is decomposed in `docs/ledger/` into Tasks with Wave, Depends on, Owns, Implements, Spec,
   acceptance criteria and Status.
6. `PROGRESS.md` is the narrative handoff: current state, open questions, session log.
7. `.claude/commands/` provides `/next-task`, `/close-task`, `/adr` to make the loop routine.

## Alternatives considered

### Issue tracker only (GitHub Issues/Projects) — rejected
Agents can't reliably read it offline or atomically with the code; specs end up scattered across
comments. Issues can mirror ledger Tasks later, but the ledger stays canonical.

### Inline ADRs inside architecture.md (as in codura-agents) — rejected here
Works, but separate files make supersession, linking and agent edits cleaner and keep
architecture.md readable.

## Consequences

Up-front writing cost; every PR must keep docs current. In return, Tasks can be worked in
parallel with low collision risk, and onboarding (human or agent) is "read CLAUDE.md".
