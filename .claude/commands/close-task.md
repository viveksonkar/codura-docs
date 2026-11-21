---
description: Verify a Task against its acceptance criteria and record the handoff
argument-hint: "[task-id]"
---

Close out the Task ($ARGUMENTS, or the one implied by the current `task/*` branch).

1. Re-read the Task in its milestone ledger file. For each acceptance criterion, state how it is
   proven (test name, command output). Run the checks from CLAUDE.md "Commands" and report results
   faithfully — if something fails or was skipped, say so and stop here.
2. Check the CLAUDE.md invariants against the diff (vendor imports outside `adapters/`, Tenant
   isolation (TenantContext, RLS, new routes/tables have isolation cases), a second embedding model, secrets in Channels, unsanitised rendering, external calls in
   the default config).
3. Docs in sync: if behaviour, schema, API or a Port changed, update the matching doc/spec in this
   change; if a decision changed, draft an ADR with `/adr`. If the breakdown was wrong (missing
   dependency, wrong Owns), fix the ledger.
4. Flip only this Task's checkbox to `[x] Done` (add a short note if anything was deferred).
5. Update `PROGRESS.md`: refresh "Current state" (next Task(s), blockers, date) and prepend a
   session-log entry using the template.
6. Summarise for the user: what's done, what's verified, what's next. Don't commit or push unless asked.
