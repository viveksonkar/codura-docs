"""Plan editor and margin floor (FR-61, FR-62; billing.md §4; M3-02). Platform Admin only.

Plans are versioned: a version a Subscription has used is immutable (DB trigger), so edits create
version n+1, which becomes the Plan's current version. Subscribers move to it at their next
period roll, or now via ``migrate``. Saving below the margin floor is refused unless the version
is marked loss-leader, which is audited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.billing.entitlements import CHANNELS, INCLUDED, REGISTRY, InvalidTerms, validate
from codura_docs.billing.money import CURRENCIES
from codura_docs.ids import new_id
from codura_docs.platform.service import audit


class PlanError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


@dataclass(frozen=True)
class Terms:
    included: dict[str, int]
    entitlements: dict[str, Any]
    overage_enabled: bool
    default_model_profile_id: str | None
    target_margin_pct: float
    loss_leader: bool
    prices: dict[str, tuple[int, dict[str, int]]]  # currency → (base_micros, {meter: micros})


def _json(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else json.loads(v or "{}")


async def rates(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(text("SELECT key, micros FROM cost_rates"))).all()
    return {r.key: int(r.micros) for r in rows}


async def _profile_prices(session: AsyncSession, profile_id: str | None) -> tuple[str, int, int]:
    pid = (
        profile_id
        or (
            await session.execute(
                text(
                    "SELECT value #>> '{}' FROM system_settings WHERE key = 'default_model_profile'"
                )
            )
        ).scalar()
    )
    row = (
        await session.execute(
            text(
                "SELECT id, price_in_per_mtok_micros AS pin, price_out_per_mtok_micros AS pout "
                "FROM model_profiles WHERE id = :id"
            ),
            {"id": pid},
        )
    ).first()
    if row is None:
        return str(pid or ""), 0, 0
    return str(row.id), int(row.pin), int(row.pout)


async def margin_report(session: AsyncSession, terms: Terms) -> dict[str, Any]:
    """Live margin check: Plan fee floor and per-unit overage floors, in micro-USD."""
    r = await rates(session)
    profile_id, pin, pout = await _profile_prices(session, terms.default_model_profile_id)
    tin, tout = r.get("typical_answer_tokens_in", 4000), r.get("typical_answer_tokens_out", 300)
    unit = {
        "answer": Decimal(tin * pin + tout * pout) / Decimal(1_000_000),
        "answer_custom_llm": Decimal(r.get("custom_llm_infra_per_answer", 0)),
        "page_ocr": Decimal(r.get("ocr_per_page", 0)),
        "pages_stored": Decimal(600 * r.get("embed_per_mtok", 0)) / Decimal(1_000_000),
        "storage_mb": Decimal(30 * r.get("storage_per_mb_day", 0)),
    }
    util = Decimal(r.get("expected_utilisation_pct", 60)) / 100
    margin = Decimal(str(terms.target_margin_pct)) / 100
    fee_pct = Decimal(r.get("payment_fee_bps", 0)) / 10_000
    expected = sum(
        (
            Decimal(terms.included.get(k, INCLUDED[k][1])) * util * unit[k]
            for k in unit
            if k in INCLUDED
        ),
        Decimal(0),
    )
    fixed = Decimal(r.get("fixed_cost_per_tenant_month", 0)) + Decimal(
        r.get("payment_fee_fixed", 0)
    )
    divisor = (Decimal(1) - margin - fee_pct) or Decimal("0.0001")
    floor = (expected + fixed) / divisor
    fx = Decimal(r.get("fx_usd_inr", 84_000_000)) / Decimal(1_000_000)
    report: dict[str, Any] = {
        "model_profile_id": profile_id,
        "unit_cogs_micros": {k: int(v) for k, v in unit.items()},
        "expected_cogs_micros": int(expected),
        "fixed_cost_micros": int(fixed),
        "floor_usd_micros": int(floor),
        "currencies": {},
        "ok": True,
    }
    for currency, (base, overage) in terms.prices.items():
        to_usd = Decimal(1) if currency == "USD" else Decimal(1) / fx
        price_usd = Decimal(base) * to_usd
        base_ok = price_usd >= floor
        over: dict[str, Any] = {}
        for meter, micros in overage.items():
            floor_unit = unit.get(meter, Decimal(0)) / ((Decimal(1) - margin) or Decimal("0.0001"))
            over_ok = Decimal(micros) * to_usd >= floor_unit
            over[meter] = {
                "price_usd_micros": int(Decimal(micros) * to_usd),
                "floor_usd_micros": int(floor_unit),
                "ok": over_ok,
            }
        if terms.overage_enabled:
            for meter in ("answer", "page_ocr"):
                if meter not in overage:
                    over[meter] = {
                        "price_usd_micros": 0,
                        "floor_usd_micros": int(unit[meter]),
                        "ok": False,
                    }
        margin_pct = (
            float((price_usd - expected - fixed) / price_usd * 100) if price_usd > 0 else None
        )
        ok = base_ok and all(v["ok"] for v in over.values())
        report["currencies"][currency] = {
            "base_micros": base,
            "price_usd_micros": int(price_usd),
            "margin_pct": round(margin_pct, 1) if margin_pct is not None else None,
            "base_ok": base_ok,
            "overage": over,
            "ok": ok,
        }
        report["ok"] = report["ok"] and ok
    return report


def parse_terms(body: dict[str, Any]) -> Terms:
    included = {k: int(v) for k, v in (body.get("included") or {}).items()}
    entitlements = dict(body.get("entitlements") or {})
    try:
        validate(entitlements, included)
    except InvalidTerms as exc:
        raise PlanError(422, "validation_error", str(exc)) from exc
    prices: dict[str, tuple[int, dict[str, int]]] = {}
    for currency, price in (body.get("prices") or {}).items():
        if currency not in CURRENCIES:
            raise PlanError(422, "validation_error", f"unsupported currency {currency!r}")
        base = int(price.get("base_micros", 0))
        overage = {str(m): int(v) for m, v in (price.get("overage_micros") or {}).items()}
        if base < 0 or any(v < 0 for v in overage.values()):
            raise PlanError(422, "validation_error", "prices must be non-negative")
        if any(m not in ("answer", "page_ocr", "answer_custom_llm") for m in overage):
            raise PlanError(
                422, "validation_error", "overage is priced for answer, page_ocr, answer_custom_llm"
            )
        prices[currency] = (base, overage)
    missing = [c for c in CURRENCIES if c not in prices]
    if missing:
        raise PlanError(422, "validation_error", f"prices are required for {', '.join(missing)}")
    margin = float(body.get("target_margin_pct", 70))
    if not 0 <= margin < 100:
        raise PlanError(422, "validation_error", "target_margin_pct must be in [0, 100)")
    return Terms(
        included=included,
        entitlements=entitlements,
        overage_enabled=bool(body.get("overage_enabled", False)),
        default_model_profile_id=body.get("default_model_profile_id") or None,
        target_margin_pct=margin,
        loss_leader=bool(body.get("loss_leader", False)),
        prices=prices,
    )


async def list_plans(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT p.id, p.code, p.name, p.description, p.sort_order, p.is_public, p.is_free, p.status, "
                    "p.current_version_id, v.version, v.included, v.entitlements, v.overage_enabled, "
                    "v.default_model_profile_id, v.target_margin_pct, v.loss_leader, v.used_at, "
                    "(SELECT count(*) FROM subscriptions s JOIN plan_versions sv ON sv.id = s.plan_version_id "
                    " WHERE sv.plan_id = p.id) AS subscribers "
                    "FROM plans p LEFT JOIN plan_versions v ON v.id = p.current_version_id ORDER BY p.sort_order, p.code"
                )
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        item = dict(r)
        item["prices"] = await _prices(session, r["current_version_id"])
        terms = _terms_from_row(r, item["prices"])
        item["margin"] = await margin_report(session, terms)
        out.append(item)
    return out


async def _prices(session: AsyncSession, version_id: str | None) -> dict[str, dict[str, Any]]:
    if not version_id:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT currency, base_micros, overage_micros FROM plan_prices WHERE plan_version_id = :v"
            ),
            {"v": version_id},
        )
    ).all()
    return {
        r.currency: {"base_micros": int(r.base_micros), "overage_micros": _json(r.overage_micros)}
        for r in rows
    }


def _terms_from_row(r: Any, prices: dict[str, dict[str, Any]]) -> Terms:
    return Terms(
        included={k: int(v) for k, v in _json(r["included"]).items()},
        entitlements=_json(r["entitlements"]),
        overage_enabled=bool(r["overage_enabled"]),
        default_model_profile_id=r["default_model_profile_id"],
        target_margin_pct=float(r["target_margin_pct"] or 70),
        loss_leader=bool(r["loss_leader"]),
        prices={
            c: (int(p["base_micros"]), {k: int(v) for k, v in p["overage_micros"].items()})
            for c, p in prices.items()
        },
    )


async def get_plan(session: AsyncSession, plan_id: str) -> dict[str, Any] | None:
    items = [p for p in await list_plans(session) if p["id"] == plan_id or p["code"] == plan_id]
    if not items:
        return None
    versions = (
        (
            await session.execute(
                text(
                    "SELECT v.id, v.version, v.used_at, v.created_at, v.loss_leader, v.target_margin_pct, "
                    "(SELECT count(*) FROM subscriptions s WHERE s.plan_version_id = v.id) AS subscribers "
                    "FROM plan_versions v WHERE v.plan_id = :p ORDER BY v.version DESC"
                ),
                {"p": items[0]["id"]},
            )
        )
        .mappings()
        .all()
    )
    return items[0] | {"versions": [dict(v) for v in versions]}


async def _check_profile(session: AsyncSession, profile_id: str | None) -> None:
    if profile_id is None:
        return
    ok = (
        await session.execute(
            text(
                "SELECT 1 FROM model_profiles WHERE id = :id AND tenant_id IS NULL AND status = 'active'"
            ),
            {"id": profile_id},
        )
    ).first()
    if not ok:
        raise PlanError(422, "validation_error", "unknown or inactive platform Model Profile")


async def _insert_version(session: AsyncSession, plan_id: str, terms: Terms, actor: str) -> str:
    await _check_profile(session, terms.default_model_profile_id)
    report = await margin_report(session, terms)
    if not report["ok"] and not terms.loss_leader:
        raise PlanError(
            422,
            "below_margin_floor",
            "The price is below the margin floor. Raise it, lower the quotas, or mark the plan loss-leader.",
            {"margin": report},
        )
    version = int(
        (
            await session.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM plan_versions WHERE plan_id = :p"),
                {"p": plan_id},
            )
        ).scalar()
        or 1
    )
    vid = new_id("plv")
    await session.execute(
        text(
            "INSERT INTO plan_versions (id, plan_id, version, included, entitlements, overage_enabled, "
            "default_model_profile_id, target_margin_pct, loss_leader, created_by) VALUES (:id, :p, :v, "
            "CAST(:inc AS jsonb), CAST(:ent AS jsonb), :ov, :mp, :tm, :ll, :by)"
        ),
        {
            "id": vid,
            "p": plan_id,
            "v": version,
            "inc": json.dumps(terms.included),
            "ent": json.dumps(terms.entitlements),
            "ov": terms.overage_enabled,
            "mp": terms.default_model_profile_id,
            "tm": terms.target_margin_pct,
            "ll": terms.loss_leader,
            "by": actor,
        },
    )
    for currency, (base, overage) in terms.prices.items():
        await session.execute(
            text(
                "INSERT INTO plan_prices (plan_version_id, currency, base_micros, overage_micros) "
                "VALUES (:v, :c, :b, CAST(:o AS jsonb))"
            ),
            {"v": vid, "c": currency, "b": base, "o": json.dumps(overage)},
        )
    await session.execute(
        text("UPDATE plans SET current_version_id = :v, updated_at = now() WHERE id = :p"),
        {"v": vid, "p": plan_id},
    )
    await audit(
        session,
        actor,
        "plan.version" + (".loss_leader" if terms.loss_leader and not report["ok"] else ""),
        None,
        {"plan_id": plan_id, "version": version, "margin_ok": report["ok"]},
    )
    return vid


async def create_plan(session: AsyncSession, actor: str, body: dict[str, Any]) -> str:
    code = str(body.get("code") or "").strip().lower()
    if not code or not code.replace("-", "").replace("_", "").isalnum() or len(code) > 40:
        raise PlanError(422, "validation_error", "code must be 1-40 letters, digits, - or _")
    exists = (
        await session.execute(text("SELECT 1 FROM plans WHERE code = :c"), {"c": code})
    ).first()
    if exists:
        raise PlanError(409, "conflict", "A plan with this code exists")
    terms = parse_terms(body)
    plan_id = f"plan_{code}"
    await session.execute(
        text(
            "INSERT INTO plans (id, code, name, description, sort_order, is_public, is_free) "
            "VALUES (:id, :c, :n, :d, :s, :pub, :free)"
        ),
        {
            "id": plan_id,
            "c": code,
            "n": str(body.get("name") or code.title())[:80],
            "d": str(body.get("description") or "")[:500],
            "s": int(body.get("sort_order") or 0),
            "pub": bool(body.get("is_public", True)),
            "free": bool(body.get("is_free", False)),
        },
    )
    await _insert_version(session, plan_id, terms, actor)
    await audit(session, actor, "plan.create", None, {"code": code})
    return plan_id


async def new_version(session: AsyncSession, actor: str, plan_id: str, body: dict[str, Any]) -> str:
    exists = (
        await session.execute(text("SELECT 1 FROM plans WHERE id = :p"), {"p": plan_id})
    ).first()
    if not exists:
        raise PlanError(404, "not_found", "Plan not found")
    return await _insert_version(session, plan_id, parse_terms(body), actor)


async def update_plan(
    session: AsyncSession, actor: str, plan_id: str, body: dict[str, Any]
) -> bool:
    fields: dict[str, Any] = {
        k: body[k]
        for k in ("name", "description", "sort_order", "is_public", "status")
        if k in body
    }
    if "status" in fields and fields["status"] not in ("active", "archived"):
        raise PlanError(422, "validation_error", "status must be active or archived")
    if not fields:
        return True
    sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys come from the fixed allowlist above
    result = await session.execute(
        text(f"UPDATE plans SET {sets}, updated_at = now() WHERE id = :id RETURNING id"),
        fields | {"id": plan_id},
    )
    ok = result.first() is not None
    if ok:
        await audit(session, actor, "plan.update", None, {"plan_id": plan_id, **fields})
    return ok


async def migrate(session: AsyncSession, actor: str, plan_id: str) -> int:
    """Move every subscriber of older versions of this Plan to its current version now."""
    result = await session.execute(
        text(
            "UPDATE subscriptions s SET plan_version_id = p.current_version_id, updated_at = now() "
            "FROM plan_versions v, plans p WHERE s.plan_version_id = v.id AND v.plan_id = p.id "
            "AND p.id = :p AND s.plan_version_id <> p.current_version_id RETURNING s.tenant_id"
        ),
        {"p": plan_id},
    )
    moved = len(result.all())
    await audit(session, actor, "plan.migrate", None, {"plan_id": plan_id, "subscriptions": moved})
    return moved


def registry_view() -> dict[str, Any]:
    """The Entitlement registry for the editor UI (specs/billing.md §3)."""
    return {
        "entitlements": {
            k: {"kind": s.kind, "default": s.default, "label": s.label} for k, s in REGISTRY.items()
        },
        "included": {k: {"kind": kind, "default": d} for k, (kind, d) in INCLUDED.items()},
        "channels": list(CHANNELS),
        "currencies": list(CURRENCIES),
    }


async def list_rates(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text("SELECT key, micros, unit, updated_at FROM cost_rates ORDER BY key")
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def set_rates(session: AsyncSession, actor: str, values: dict[str, int]) -> None:
    for key, micros in values.items():
        if micros < 0:
            raise PlanError(422, "validation_error", f"{key} must be non-negative")
        result = await session.execute(
            text(
                "UPDATE cost_rates SET micros = :m, updated_at = now() WHERE key = :k RETURNING key"
            ),
            {"m": int(micros), "k": key},
        )
        if result.first() is None:
            raise PlanError(422, "validation_error", f"unknown cost rate {key!r}")
    await audit(session, actor, "cost_rates.update", None, values)
