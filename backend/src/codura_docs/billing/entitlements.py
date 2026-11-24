"""Entitlements: what a Tenant's Plan allows, resolved in one place (FR-62, FR-65; specs/billing.md §3).

``for_tenant`` merges the Subscription's Plan version (or the trial Plan during a Trial) with the
Platform Admin's per-Tenant overrides. Every feature check goes through ``require`` /
``require_channel``. Read-only: works on any session (RLS limits it to the caller's Tenant).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CHANNELS = ("hosted", "widget", "wordpress", "android", "api")
EMAIL_MODES = ("off", "optional", "required")


class PlanRequired(Exception):
    """The current Plan lacks an Entitlement → HTTP 403 ``plan_required``."""

    def __init__(self, entitlement: str, message: str | None = None) -> None:
        super().__init__(message or f"Your plan does not include {entitlement.replace('_', ' ')}")
        self.entitlement = entitlement


class InvalidTerms(ValueError):
    """An Entitlement or included quantity failed validation (Plan editor)."""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _is_channels(v: Any) -> bool:
    return isinstance(v, list) and all(c in CHANNELS for c in v) and len(set(v)) == len(v)


@dataclass(frozen=True)
class Spec:
    kind: Literal["bool", "int", "enum", "channels"]
    default: Any
    check: Callable[[Any], bool]
    label: str


REGISTRY: dict[str, Spec] = {
    "channels": Spec("channels", ["hosted", "widget"], _is_channels, "Channels"),
    "remove_branding": Spec("bool", False, lambda v: isinstance(v, bool), "Remove branding"),
    "visitor_email": Spec("enum", "off", lambda v: v in EMAIL_MODES, "Visitor email capture"),
    "analytics_retention_days": Spec("int", 7, _is_int, "Analytics retention (days)"),
    "custom_llm": Spec("bool", False, lambda v: isinstance(v, bool), "Custom LLM"),
    "max_output_tokens": Spec("int", 500, _is_int, "Max output tokens per answer"),
    "max_input_tokens": Spec("int", 4000, _is_int, "Max input tokens per answer"),
    "context_token_budget": Spec("int", 2000, _is_int, "Context token budget"),
    "max_concurrent_answers": Spec("int", 2, _is_int, "Concurrent answers"),
}
# Included quantities: flow meters reset each period; stock limits measure what exists now.
INCLUDED: dict[str, tuple[Literal["flow", "stock"], int]] = {
    "answer": ("flow", 100),
    "page_ocr": ("flow", 50),
    "pages_stored": ("stock", 200),
    "storage_mb": ("stock", 100),
    "seats": ("stock", 2),
    "assistants": ("stock", 1),
}
FLOW_METERS = tuple(k for k, (kind, _) in INCLUDED.items() if kind == "flow")
OVERAGE_METERS = ("answer", "page_ocr", "answer_custom_llm")
# Meters that share one included allowance (Custom LLM answers count as answers).
SHARED_ALLOWANCE: dict[str, tuple[str, ...]] = {
    "answer": ("answer", "answer_custom_llm"),
    "page_ocr": ("page_ocr",),
}


def validate(entitlements: Mapping[str, Any], included: Mapping[str, Any]) -> None:
    for key, value in entitlements.items():
        spec = REGISTRY.get(key)
        if spec is None:
            raise InvalidTerms(f"unknown entitlement {key!r}")
        if not spec.check(value):
            raise InvalidTerms(f"invalid value for {key!r}")
    for key, value in included.items():
        if key not in INCLUDED:
            raise InvalidTerms(f"unknown included quantity {key!r}")
        if not _is_int(value):
            raise InvalidTerms(f"included {key!r} must be a non-negative integer")


def _json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


@dataclass(frozen=True)
class Terms:
    """The effective Plan for one Tenant right now."""

    tenant_id: str
    plan_code: str
    plan_name: str
    plan_version_id: str
    is_free: bool
    status: str  # trialing | active | past_due | none
    trial_ends_at: datetime | None
    period_start: date
    included: dict[str, int]
    entitlements: dict[str, Any]
    overage_enabled: bool
    prices: dict[str, tuple[int, dict[str, int]]] = field(default_factory=dict)
    currency: str | None = None
    spend_cap_micros: int | None = None
    pending_plan_code: str | None = None
    cancel_at_period_end: bool = False
    default_model_profile_id: str | None = None
    trial_plan_code: str | None = None

    @property
    def trialing(self) -> bool:
        return self.status == "trialing"

    @property
    def past_due(self) -> bool:
        return self.status == "past_due"

    def get(self, key: str) -> Any:
        spec = REGISTRY[key]
        return self.entitlements.get(key, spec.default)

    def limit(self, key: str) -> int:
        return int(self.included.get(key, INCLUDED[key][1]))

    def overage_price(self, meter: str) -> int:
        """Micros per unit in the Tenant's currency (0 when unknown)."""
        if not self.currency or self.currency not in self.prices:
            return 0
        return int(self.prices[self.currency][1].get(meter, 0))

    def allows_channel(self, channel: str) -> bool:
        return channel in self.get("channels")

    def email_mode(self, assistant_mode: str) -> str:
        """Effective Visitor email mode: the stricter of the Assistant setting and the Plan."""
        allowed = EMAIL_MODES.index(self.get("visitor_email"))
        return EMAIL_MODES[min(EMAIL_MODES.index(assistant_mode), allowed)]

    def public(self) -> dict[str, Any]:
        """What the Tenant may see: quantities and features, never COGS or models."""
        return {
            "plan": {"code": self.plan_code, "name": self.plan_name, "is_free": self.is_free},
            "status": self.status,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "trial_plan": self.trial_plan_code if self.trialing else None,
            "included": self.included,
            "entitlements": {k: self.get(k) for k in REGISTRY},
            "overage_enabled": self.overage_enabled,
            "currency": self.currency,
            "spend_cap_micros": self.spend_cap_micros,
            "pending_plan": self.pending_plan_code,
            "cancel_at_period_end": self.cancel_at_period_end,
        }


def require(terms: Terms, key: str) -> None:
    if not terms.get(key):
        raise PlanRequired(key)


def require_channel(terms: Terms, channel: str) -> None:
    if not terms.allows_channel(channel):
        raise PlanRequired("channels", f"Your plan does not include the {channel} channel")


async def billing_settings(session: AsyncSession) -> dict[str, Any]:
    value = (
        await session.execute(text("SELECT value FROM system_settings WHERE key = 'billing'"))
    ).scalar()
    return {
        "free_plan_code": "free",
        "trial_plan_code": "growth",
        "trial_days": 14,
        "grace_days": 7,
        **_json(value),
    }


async def prices_for(
    session: AsyncSession, plan_version_id: str
) -> dict[str, tuple[int, dict[str, int]]]:
    rows = (
        await session.execute(
            text(
                "SELECT currency, base_micros, overage_micros FROM plan_prices WHERE plan_version_id = :v"
            ),
            {"v": plan_version_id},
        )
    ).all()
    return {
        r.currency: (int(r.base_micros), {k: int(v) for k, v in _json(r.overage_micros).items()})
        for r in rows
    }


_VERSION_SQL = (
    "SELECT v.id, v.included, v.entitlements, v.overage_enabled, v.default_model_profile_id, "
    "p.code, p.name, p.is_free FROM plan_versions v JOIN plans p ON p.id = v.plan_id "
)


async def _version(
    session: AsyncSession, *, version_id: str | None = None, plan_code: str | None = None
) -> Any:
    if version_id:
        params: dict[str, Any] = {"v": version_id}
        sql = _VERSION_SQL + "WHERE v.id = :v"
    else:
        params = {"c": plan_code}
        sql = _VERSION_SQL + "WHERE p.code = :c AND v.id = p.current_version_id"
    return (await session.execute(text(sql), params)).first()


async def for_tenant(
    session: AsyncSession, tenant_id: str, *, now: datetime | None = None
) -> Terms:
    now = now or datetime.now(UTC)
    cfg = await billing_settings(session)
    sub = (
        await session.execute(
            text(
                "SELECT s.plan_version_id, s.status, s.trial_ends_at, s.period_start, s.spend_cap_micros, "
                "s.cancel_at_period_end, pp.code AS pending_code, b.currency "
                "FROM subscriptions s "
                "LEFT JOIN plan_versions pv ON pv.id = s.pending_plan_version_id "
                "LEFT JOIN plans pp ON pp.id = pv.plan_id "
                "LEFT JOIN billing_accounts b ON b.tenant_id = s.tenant_id "
                "WHERE s.tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    if sub is None:
        row = await _version(session, plan_code=cfg["free_plan_code"])
        status, trial_ends_at, period = "none", None, date(now.year, now.month, 1)
    else:
        row = await _version(session, version_id=sub.plan_version_id)
        status, trial_ends_at, period = sub.status, sub.trial_ends_at, sub.period_start
    if row is None:  # catalogue missing (fresh test DB): fall back to registry defaults
        terms = Terms(
            tenant_id,
            "free",
            "Free",
            "",
            True,
            status,
            trial_ends_at,
            period,
            {k: d for k, (_, d) in INCLUDED.items()},
            {},
            False,
        )
    else:
        terms = Terms(
            tenant_id=tenant_id,
            plan_code=str(row.code),
            plan_name=row.name,
            plan_version_id=row.id,
            is_free=bool(row.is_free),
            status=status,
            trial_ends_at=trial_ends_at,
            period_start=period,
            included={k: int(v) for k, v in _json(row.included).items()},
            entitlements=_json(row.entitlements),
            overage_enabled=bool(row.overage_enabled),
            prices=await prices_for(session, row.id),
            default_model_profile_id=row.default_model_profile_id,
        )
    if sub is not None:
        terms = replace(
            terms,
            currency=sub.currency,
            spend_cap_micros=sub.spend_cap_micros,
            pending_plan_code=sub.pending_code,
            cancel_at_period_end=bool(sub.cancel_at_period_end),
        )
    # A live Trial runs on the trial Plan's limits, without overage (billing.md §5).
    if terms.trialing and trial_ends_at is not None and trial_ends_at > now:
        trial = await _version(session, plan_code=cfg["trial_plan_code"])
        if trial is not None:
            terms = replace(
                terms,
                included={k: int(v) for k, v in _json(trial.included).items()},
                entitlements=_json(trial.entitlements),
                overage_enabled=False,
                trial_plan_code=str(trial.code),
            )
    override = (
        await session.execute(
            text(
                "SELECT included, entitlements, overage_enabled FROM tenant_billing_overrides "
                "WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    if override is not None:
        terms = replace(
            terms,
            included={**terms.included, **{k: int(v) for k, v in _json(override.included).items()}},
            entitlements={**terms.entitlements, **_json(override.entitlements)},
            overage_enabled=terms.overage_enabled
            if override.overage_enabled is None
            else bool(override.overage_enabled),
        )
    return terms
