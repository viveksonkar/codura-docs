"""Assistants: a Tenant's chat configuration. No model field: the platform picks it (ADR-0019)."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.db.session import rowcount
from codura_docs.ids import new_id

# Built-in texts for the launch languages; Tenants override per language (FR-19).
DEFAULT_FALLBACK = {
    "default": "Sorry, I couldn't find that in our documents. Please rephrase or contact us directly.",
    "hi": "क्षमा करें, यह जानकारी हमारे दस्तावेज़ों में नहीं मिली। कृपया प्रश्न दोबारा लिखें या सीधे हमसे संपर्क करें।",
    "es": "Lo siento, no encontré eso en nuestros documentos. Reformula la pregunta o contáctanos directamente.",
    "fr": "Désolé, je n'ai pas trouvé cela dans nos documents. Reformulez ou contactez-nous directement.",
    "de": "Leider habe ich das in unseren Dokumenten nicht gefunden. Bitte formulieren Sie neu oder kontaktieren Sie uns.",
    "ar": "عذرًا، لم أجد ذلك في مستنداتنا. يُرجى إعادة صياغة السؤال أو التواصل معنا مباشرة.",
    "zh": "抱歉，我们的文档中没有找到相关内容。请换个问法或直接联系我们。",
}
DEFAULT_LIMIT = {
    "default": "This assistant has reached its usage limit for now. Please try again later.",
    "hi": "यह सहायक अभी अपनी उपयोग सीमा तक पहुँच गया है। कृपया बाद में पुनः प्रयास करें।",
    "es": "Este asistente alcanzó su límite de uso por ahora. Inténtalo más tarde.",
    "fr": "Cet assistant a atteint sa limite d'utilisation. Réessayez plus tard.",
    "de": "Dieser Assistent hat sein Nutzungslimit erreicht. Bitte versuchen Sie es später erneut.",
    "ar": "وصل هذا المساعد إلى حد الاستخدام حاليًا. يُرجى المحاولة لاحقًا.",
    "zh": "该助手暂时已达到使用上限，请稍后再试。",
}
DEFAULT_WELCOME = {"default": "Hi! Ask me anything about our documents."}
EDITABLE = (
    "name",
    "slug",
    "knowledge_base_ids",
    "system_prompt",
    "default_language",
    "welcome_text",
    "fallback_text",
    "limit_reached_text",
    "visitor_email",
    "visitor_email_consent",
    "privacy_url",
    "retrieval",
    "theme",
    "hosted_enabled",
)
JSON_FIELDS = {
    "welcome_text",
    "fallback_text",
    "limit_reached_text",
    "visitor_email_consent",
    "retrieval",
    "theme",
}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
COLUMNS = "id, " + ", ".join(EDITABLE) + ", created_at, updated_at"


class AssistantError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def _check(fields: dict[str, Any]) -> None:
    if "slug" in fields and not _SLUG.match(fields["slug"]):
        raise AssistantError(
            422, "validation_error", "Slug: 2–40 lowercase letters, digits or hyphens"
        )


async def create(session: AsyncSession, tenant_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    fields = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
    base = re.sub(r"[^a-z0-9]+", "-", fields.get("name", "assistant").lower()).strip("-")[:30]
    fields.setdefault("slug", base if len(base) >= 2 else f"{base}-assistant".strip("-"))
    fields.setdefault("welcome_text", DEFAULT_WELCOME)
    _check(fields)
    fields["id"], fields["tenant_id"] = new_id("asst"), tenant_id
    cols = ", ".join(fields)
    vals = ", ".join(f"CAST(:{k} AS jsonb)" if k in JSON_FIELDS else f":{k}" for k in fields)
    params = {k: json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()}
    try:
        row = (
            (
                await session.execute(
                    text(f"INSERT INTO assistants ({cols}) VALUES ({vals}) RETURNING {COLUMNS}"),
                    params,
                )
            )
            .mappings()
            .one()
        )
    except IntegrityError as exc:
        if "same tenant" in str(exc):
            raise AssistantError(422, "validation_error", "Unknown knowledge base") from exc
        raise AssistantError(409, "conflict", "An assistant with this slug already exists") from exc
    except DBAPIError as exc:
        if "same tenant" in str(exc):
            raise AssistantError(422, "validation_error", "Unknown knowledge base") from exc
        raise
    return dict(row)


async def update(
    session: AsyncSession, assistant_id: str, fields: dict[str, Any]
) -> dict[str, Any] | None:
    fields = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
    _check(fields)
    if not fields:
        return await get(session, assistant_id)
    sets = ", ".join(
        f"{k} = CAST(:{k} AS jsonb)" if k in JSON_FIELDS else f"{k} = :{k}" for k in fields
    )
    params = {k: json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()} | {
        "id": assistant_id
    }
    try:
        row = (
            (
                await session.execute(
                    text(
                        f"UPDATE assistants SET {sets}, updated_at = now() WHERE id = :id RETURNING {COLUMNS}"
                    ),
                    params,
                )
            )
            .mappings()
            .first()
        )
    except IntegrityError as exc:
        if "same tenant" in str(exc):
            raise AssistantError(422, "validation_error", "Unknown knowledge base") from exc
        raise AssistantError(409, "conflict", "An assistant with this slug already exists") from exc
    except DBAPIError as exc:
        if "same tenant" in str(exc):
            raise AssistantError(422, "validation_error", "Unknown knowledge base") from exc
        raise
    return dict(row) if row else None


async def get(session: AsyncSession, assistant_id: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(f"SELECT {COLUMNS} FROM assistants WHERE id = :id"),
                {"id": assistant_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def list_all(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(text(f"SELECT {COLUMNS} FROM assistants ORDER BY created_at")))
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def delete(session: AsyncSession, assistant_id: str) -> bool:
    return bool(
        rowcount(
            await session.execute(
                text("DELETE FROM assistants WHERE id = :id"), {"id": assistant_id}
            )
        )
    )
