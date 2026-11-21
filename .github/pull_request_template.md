## Task

<!-- e.g. M0-04 — Ports, fakes, registry, contract suites -->
Task: 
Implements: <!-- FR-xx / NFR-xx / SEC-xx / ADR-NNNN -->

## What changed

## Acceptance criteria

<!-- Copy from the ledger; tick each with how it's proven (test / command). -->
- [ ] 

## Checklist

- [ ] Stayed inside the Task's **Owns** paths (or updated the ledger and explained why)
- [ ] CLAUDE.md invariants hold (no vendor imports outside `adapters/`, Tenant isolation (TenantContext, RLS, isolation case for new routes/tables), fixed embedding model, no secrets in Channels, sanitised rendering, local-first defaults)
- [ ] Docs/specs updated where behaviour changed; ADR added if a decision changed
- [ ] Ledger checkbox flipped for this Task only; PROGRESS.md updated
- [ ] Tests added; CI green
