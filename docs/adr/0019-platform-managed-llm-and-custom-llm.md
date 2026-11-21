# ADR-0019 — Platform-managed LLMs; per-Tenant Custom LLM only when the Platform Admin enables it

- **Status:** Accepted
- **Date:** 2026-11-19
- **Amends:** ADR-0004 (where Model Profiles live and who picks them)
- **Relates to:** FR-23, FR-26…29, SEC-11, SEC-17, ADR-0021

## Context

Owner decision (2026-11-19): the LLM is managed by the SaaS platform, and Tenants can't choose or
configure it. The platform charges for answers, so it must control which model (and therefore which
cost) each Tenant uses. Some organisations will ask to use their own LLM. That is allowed only once
the Platform Admin enables it for that specific Tenant.

## Decision

- **Model Profiles live in the database** (`model_profiles`) and are managed by the Platform Admin
  in the Platform Console. `config/models.yaml` only seeds them on first boot. Provider
  credentials are stored encrypted (SEC-17), never in the profile's plain fields.
- **Which profile answers a Tenant's question** is resolved in this order:
  1. the Tenant's **Custom LLM** profile, if the `custom_llm` Entitlement is enabled and the profile
     is active;
  2. a **Platform Admin override** for that Tenant (`tenants.model_profile_override`);
  3. the **Plan's** default profile (`plans.model_profile`).

  Assistants don't choose a model, and Tenant Admins never see provider names, keys or costs. They
  see only the plan's "answer quality" label.
- **Custom LLM ("bring your own LLM")**: a Tenant-scoped Model Profile (`model_profiles.tenant_id`
  set) using `openai_compat` or `anthropic`. Only the Platform Admin can enable the Entitlement and
  create, edit or test the profile. The Tenant's credentials are encrypted with the platform key.
  Answers through a Custom LLM are metered as `answer_custom_llm`. Token costs aren't COGS for us,
  but a platform fee applies (billing.md §4).
- Every profile carries pricing metadata (`input/output cost per 1M tokens`) used for COGS
  (ADR-0021). Self-hosted profiles carry an amortised GPU cost per 1M tokens set by the Platform
  Admin.

## Alternatives considered

### Tenant Admins pick from a menu of models — rejected
The owner wants the platform to own model choice. A menu also turns pricing into a per-model matrix.
Plans with quality tiers do the same job more simply.

### Keep profiles in a config file — rejected
Changing a Tenant's model would need a redeploy, and per-Tenant Custom LLM credentials can't
sensibly live in a file.

## Consequences

New platform tables and UI. The model is resolved once per question, and the result is recorded on
the Message (profile ID, cost snapshot). Custom LLM moves data off our servers to the Tenant's
provider, which the Tenant chose. This is shown in their settings and recorded in the audit log.
