"""Invoices, lines and payments: our ledger computes every amount (FR-66, ADR-0021/0026).

Writes run on the billing engine (``codura_worker``) inside a tenant_session, or on the platform
engine for Platform Admin actions. Issuing is idempotent per ``idempotency_key``.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.billing.money import Currency, format_minor, to_minor
from codura_docs.ids import new_id
from codura_docs.ports.payments import ExternalInvoiceRef, InvoiceDraft, InvoiceLineDraft


@dataclass(frozen=True)
class Line:
    kind: str  # base | overage | proration_credit | proration_charge | adjustment
    description: str
    quantity: Decimal
    unit_micros: int
    meter: str | None = None
    amount_minor: int | None = None  # computed from quantity × unit when None

    @property
    def amount(self) -> int:
        return (
            self.amount_minor
            if self.amount_minor is not None
            else to_minor(self.quantity, self.unit_micros)
        )


@dataclass(frozen=True)
class Issued:
    id: str
    number: str
    total_minor: int
    currency: Currency
    created: bool  # False: an invoice with this idempotency key already existed


async def _number(session: AsyncSession, period: date) -> str:
    seq = (await session.execute(text("SELECT nextval('invoice_number_seq')"))).scalar()
    return f"CD-{period:%Y%m}-{int(seq or 0):06d}"


async def issue(
    session: AsyncSession,
    *,
    tenant_id: str,
    kind: str,
    period_start: date,
    currency: Currency,
    lines: list[Line],
    idempotency_key: str,
    provider: str,
    subscription_id: str | None,
    status: str = "open",
    due_days: int = 7,
) -> Issued:
    existing = (
        await session.execute(
            text(
                "SELECT id, number, total_minor, currency FROM invoices WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": tenant_id, "k": idempotency_key},
        )
    ).first()
    if existing is not None:
        return Issued(
            existing.id, existing.number, int(existing.total_minor), existing.currency, False
        )
    total = sum(line.amount for line in lines)
    invoice_id = new_id("bill")
    now = datetime.now(UTC)
    final_status = "paid" if total == 0 and status == "open" else status
    await session.execute(
        text(
            "INSERT INTO invoices (id, tenant_id, number, subscription_id, kind, period_start, currency, status, "
            "subtotal_minor, total_minor, issued_at, due_at, paid_at, provider, idempotency_key) VALUES "
            "(:id, :t, :n, :s, :k, :p, :c, :st, :sub, :tot, :iss, :due, :paid, :prov, :key)"
        ),
        {
            "id": invoice_id,
            "t": tenant_id,
            "n": await _number(session, period_start),
            "s": subscription_id,
            "k": kind,
            "p": period_start,
            "c": currency,
            "st": final_status,
            "sub": total,
            "tot": total,
            "iss": now if final_status != "draft" else None,
            "due": now + timedelta(days=due_days) if final_status != "draft" else None,
            "paid": now if final_status == "paid" else None,
            "prov": provider,
            "key": idempotency_key,
        },
    )
    for i, line in enumerate(lines):
        await session.execute(
            text(
                "INSERT INTO invoice_lines (id, tenant_id, invoice_id, kind, meter, description, quantity, "
                "unit_micros, amount_minor, sort) VALUES (:id, :t, :inv, :k, :m, :d, :q, :u, :a, :s)"
            ),
            {
                "id": new_id("line"),
                "t": tenant_id,
                "inv": invoice_id,
                "k": line.kind,
                "m": line.meter,
                "d": line.description,
                "q": line.quantity,
                "u": line.unit_micros,
                "a": line.amount,
                "s": i,
            },
        )
    number = (
        await session.execute(
            text("SELECT number FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).scalar()
    return Issued(invoice_id, str(number), total, currency, True)


async def draft_for_provider(
    session: AsyncSession, invoice_id: str, customer_id: str
) -> InvoiceDraft:
    inv = (
        await session.execute(
            text(
                "SELECT id, number, tenant_id, currency, total_minor FROM invoices WHERE id = :id"
            ),
            {"id": invoice_id},
        )
    ).one()
    lines = (
        await session.execute(
            text(
                "SELECT description, amount_minor FROM invoice_lines WHERE invoice_id = :id ORDER BY sort"
            ),
            {"id": invoice_id},
        )
    ).all()
    return InvoiceDraft(
        invoice_id=inv.id,
        number=inv.number,
        tenant_id=inv.tenant_id,
        customer_id=customer_id,
        currency=inv.currency,
        lines=[InvoiceLineDraft(r.description, int(r.amount_minor)) for r in lines],
        total_minor=int(inv.total_minor),
    )


async def set_external(session: AsyncSession, invoice_id: str, ref: ExternalInvoiceRef) -> None:
    await session.execute(
        text(
            "UPDATE invoices SET provider_invoice_id = COALESCE(:pid, provider_invoice_id), "
            "hosted_url = COALESCE(:url, hosted_url), updated_at = now() WHERE id = :id"
        ),
        {"pid": ref.provider_invoice_id, "url": ref.hosted_url, "id": invoice_id},
    )
    if ref.status == "paid":
        await session.execute(
            text(
                "UPDATE invoices SET status = 'paid', paid_at = COALESCE(paid_at, now()) "
                "WHERE id = :id AND status IN ('draft','open')"
            ),
            {"id": invoice_id},
        )


async def open_draft(session: AsyncSession, invoice_id: str) -> None:
    await session.execute(
        text(
            "UPDATE invoices SET status = 'open', issued_at = COALESCE(issued_at, now()), "
            "due_at = COALESCE(due_at, now() + interval '7 days'), updated_at = now() "
            "WHERE id = :id AND status = 'draft'"
        ),
        {"id": invoice_id},
    )


async def mark_paid(
    session: AsyncSession,
    invoice_id: str,
    *,
    provider: str,
    provider_payment_id: str | None,
    method: str,
    recorded_by: str | None,
    note: str = "",
    amount_minor: int | None = None,
) -> bool:
    """Mark paid + record the payment. Idempotent: a paid invoice stays paid, no duplicate payment."""
    inv = (
        await session.execute(
            text(
                "SELECT id, tenant_id, status, total_minor, currency FROM invoices WHERE id = :id FOR UPDATE"
            ),
            {"id": invoice_id},
        )
    ).first()
    if inv is None or inv.status in ("void",):
        return False
    if inv.status != "paid":
        await session.execute(
            text(
                "UPDATE invoices SET status = 'paid', paid_at = now(), issued_at = COALESCE(issued_at, now()), "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": invoice_id},
        )
    exists = (
        provider_payment_id
        and (
            await session.execute(
                text("SELECT 1 FROM payments WHERE provider = :p AND provider_payment_id = :pp"),
                {"p": provider, "pp": provider_payment_id},
            )
        ).first()
    )
    already = (
        await session.execute(
            text("SELECT 1 FROM payments WHERE invoice_id = :id AND status = 'succeeded'"),
            {"id": invoice_id},
        )
    ).first()
    if not exists and not already and int(inv.total_minor) > 0:
        await session.execute(
            text(
                "INSERT INTO payments (id, tenant_id, invoice_id, provider, provider_payment_id, amount_minor, "
                "currency, status, method, recorded_by, note) VALUES (:id, :t, :inv, :p, :pp, :a, :c, "
                "'succeeded', :m, :by, :note)"
            ),
            {
                "id": new_id("pay"),
                "t": inv.tenant_id,
                "inv": invoice_id,
                "p": provider,
                "pp": provider_payment_id,
                "a": amount_minor if amount_minor is not None else int(inv.total_minor),
                "c": inv.currency,
                "m": method,
                "by": recorded_by,
                "note": note[:500],
            },
        )
    return True


async def record_failed_payment(
    session: AsyncSession, invoice_id: str, *, provider: str, provider_payment_id: str | None
) -> None:
    inv = (
        await session.execute(
            text("SELECT tenant_id, total_minor, currency FROM invoices WHERE id = :id"),
            {"id": invoice_id},
        )
    ).first()
    if inv is None:
        return
    await session.execute(
        text(
            "INSERT INTO payments (id, tenant_id, invoice_id, provider, provider_payment_id, amount_minor, "
            "currency, status, method) VALUES (:id, :t, :inv, :p, :pp, :a, :c, 'failed', 'card') "
            "ON CONFLICT (provider, provider_payment_id) DO NOTHING"
        ),
        {
            "id": new_id("pay"),
            "t": inv.tenant_id,
            "inv": invoice_id,
            "p": provider,
            "pp": provider_payment_id,
            "a": int(inv.total_minor),
            "c": inv.currency,
        },
    )


async def void(session: AsyncSession, invoice_id: str) -> bool:
    result = await session.execute(
        text(
            "UPDATE invoices SET status = 'void', updated_at = now() WHERE id = :id "
            "AND status IN ('draft','open','uncollectible') RETURNING id"
        ),
        {"id": invoice_id},
    )
    return result.first() is not None


_LIST = (
    "SELECT id, number, kind, period_start, currency, status, total_minor, issued_at, due_at, paid_at, "
    "hosted_url, created_at FROM invoices "
)


async def list_invoices(session: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(_LIST + "WHERE status <> 'draft' ORDER BY created_at DESC LIMIT :l"),
                {"l": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_invoice(session: AsyncSession, invoice_id: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(_LIST + "WHERE id = :id AND status <> 'draft'"), {"id": invoice_id}
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    lines = (
        (
            await session.execute(
                text(
                    "SELECT kind, meter, description, quantity, unit_micros, amount_minor FROM invoice_lines "
                    "WHERE invoice_id = :id ORDER BY sort"
                ),
                {"id": invoice_id},
            )
        )
        .mappings()
        .all()
    )
    payments = (
        (
            await session.execute(
                text(
                    "SELECT provider, amount_minor, currency, status, method, created_at FROM payments "
                    "WHERE invoice_id = :id ORDER BY created_at"
                ),
                {"id": invoice_id},
            )
        )
        .mappings()
        .all()
    )
    return dict(row) | {"lines": [dict(r) for r in lines], "payments": [dict(p) for p in payments]}


def render_html(
    invoice: dict[str, Any], *, tenant_name: str, account: dict[str, Any] | None
) -> str:
    """A printable, self-contained invoice. Every value is escaped (SEC-07)."""
    e = html.escape
    cur: Currency = invoice["currency"]
    rows = "".join(
        f"<tr><td>{e(str(line['description']))}</td><td class=n>{e(_qty(line['quantity']))}</td>"
        f"<td class=n>{e(format_minor(int(line['amount_minor']), cur))}</td></tr>"
        for line in invoice["lines"]
    )
    bill_to = e(str((account or {}).get("company_name") or tenant_name))
    country = e(str((account or {}).get("country") or ""))
    tax = (account or {}).get("tax_id")
    tax_html = f"<div>Tax ID: {e(str(tax))}</div>" if tax else ""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<title>Invoice {e(invoice['number'])}</title><style>"
        "body{font:14px/1.5 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#111}"
        "table{width:100%;border-collapse:collapse;margin-top:24px}td,th{padding:8px;border-bottom:1px solid #ddd;"
        "text-align:left}.n{text-align:right}.tot td{font-weight:600;border-bottom:0}.muted{color:#555}"
        "</style></head><body>"
        f"<h1>Invoice {e(invoice['number'])}</h1>"
        f"<div class=muted>Status: {e(str(invoice['status']))} · Issued: "
        f"{e(str(invoice['issued_at'] or '')[:10])} · Due: {e(str(invoice['due_at'] or '')[:10])}</div>"
        f"<h3>Bill to</h3><div>{bill_to}</div><div>{country}</div>{tax_html}"
        "<table><thead><tr><th>Description</th><th class=n>Qty</th><th class=n>Amount</th></tr></thead>"
        f"<tbody>{rows}<tr class=tot><td>Total ({e(cur)})</td><td></td>"
        f"<td class=n>{e(format_minor(int(invoice['total_minor']), cur))}</td></tr></tbody></table>"
        "<p class=muted>Codura Docs. Amounts are computed from our usage ledger.</p></body></html>"
    )


def _qty(value: Any) -> str:
    d = Decimal(str(value))
    return f"{d.normalize():f}" if d != d.to_integral() else f"{int(d):,}"
