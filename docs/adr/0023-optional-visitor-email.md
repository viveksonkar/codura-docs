# ADR-0023 — Visitors stay anonymous; optional email capture is contact data, never identity

- **Status:** Accepted
- **Date:** 2026-11-19
- **Amends:** ADR-0012 (public Chat API: pre-chat form)
- **Relates to:** FR-53, FR-54, SEC-19, PROGRESS Q7

## Context

Owner decision (2026-11-19): Visitors are always anonymous, but may give an email when the chat
window opens. Tenants value this as a lead and follow-up channel. An unverified email must not let
someone read another person's Conversations.

## Decision

- Per Assistant: `visitor_email` = `off` | `optional` | `required`, plus consent text (per
  language) and a privacy-policy URL.
- The Channel shows a pre-chat form when the setting isn't `off`. The email and consent timestamp
  are sent with the first message (`visitor_email`, `consent`). The server validates the format
  and stores them on the Conversation.
- **The email is never an identity.** History restore still uses Visitor ID + Conversation ID on
  the same device. Entering an email never reveals other Conversations. No emails are sent to
  Visitors in the MVP.
- Tenant Admins (not Tenant Users, unless the Tenant allows it) can view and export captured emails.
  Captured emails are deleted under Conversation retention or on request (DSAR endpoint for
  Tenant Admins).
- `required` blocks chatting until an email is entered. Rate limits still apply per Visitor/IP.

## Alternatives considered

### Email login / magic link for Visitors — rejected
Contradicts "always anonymous". It adds email cost and abuse vectors. Possible later as a separate
Entitlement.

### Email as history key across devices — rejected
Without verification, anyone could read anyone's history.

## Consequences

PII now exists in Conversations, which drives encryption at rest for that column, export, deletion
and retention (SEC-19). The public API gains two optional fields (an additive v1 change).
