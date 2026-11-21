---
description: Draft a new Architecture Decision Record
argument-hint: "<short decision title>"
---

Draft an ADR for: $ARGUMENTS

1. Read `docs/adr/README.md` and `docs/adr/template.md`. The next number is the highest existing
   `NNNN` + 1 (never reuse a number).
2. Read the docs and ADRs the decision touches. If it supersedes an Accepted ADR, say which.
3. Write `docs/adr/NNNN-<kebab-title>.md` from the template with Status `Proposed`, today's date,
   real context, the decision stated so compliance can be checked, at least two rejected
   alternatives with specific reasons, and consequences.
4. Add the row to the index table in `docs/adr/README.md`. If superseding, change only the old
   ADR's Status line to `Superseded by ADR-NNNN`.
5. List the docs/specs/ledger entries that must change once it's Accepted, and ask the user to
   accept or amend it.
