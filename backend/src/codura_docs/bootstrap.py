"""First-boot and every-boot setup run by the ``migrate`` service (owner role), idempotently.

1. ``alembic upgrade head``
2. Give the runtime roles LOGIN + their passwords (roles are created NOLOGIN by the migration).
3. Record the fixed embedding model in ``system_settings`` (ADR-0015).
4. Create the first Platform Admin (ADR-0020) and, optionally, the demo Tenant.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from codura_docs.config import Settings
from codura_docs.crypto import Keyring
from codura_docs.db.guards import EMBEDDING_KEY, embedding_fingerprint
from codura_docs.db.migrate import upgrade_to_head
from codura_docs.ids import new_id
from codura_docs.passwords import hash_password
from codura_docs.services.auth import sha256
from codura_docs.services.channel_keys import generate, hosted_aad

log = logging.getLogger(__name__)

DEMO_TENANT_SLUG = "demo"
DEMO_ADMIN_EMAIL = "admin@demo.test"
DEMO_USER_EMAIL = "user@demo.test"
DEMO_PASSWORD = "demo-password"  # noqa: S105 - local demo only, never seeded in production
DEMO_ASSISTANT_SLUG = "help"
# Fixed, dev-only widget key for the /demo/ page and the web e2e (never seeded in production).
DEMO_WIDGET_KEY = "pk_demo_widget_local_only_00000000000000000000000"
DEMO_ORIGINS = ["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:5174"]


def _set_role_passwords(conn: Connection, settings: Settings) -> None:
    for role, secret in (
        ("codura_app", settings.db_app_password),
        ("codura_worker", settings.db_worker_password),
        ("codura_platform", settings.db_platform_password),
    ):
        # Identifiers are constants; the password is passed through format() of a quoted literal.
        literal = conn.exec_driver_sql(
            "SELECT quote_literal(%s)", (secret.get_secret_value(),)
        ).scalar()
        conn.exec_driver_sql(f"ALTER ROLE {role} LOGIN PASSWORD {literal}")


def _record_embedding(conn: Connection, settings: Settings) -> None:
    fingerprint = embedding_fingerprint(settings.load_models().embedding)
    conn.execute(
        text(
            "INSERT INTO system_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"k": EMBEDDING_KEY, "v": json.dumps(fingerprint)},
    )


def _seed_model_profiles(conn: Connection, settings: Settings) -> None:
    """First boot only per profile id: the Platform Console is authoritative afterwards (ADR-0019)."""
    from codura_docs.crypto import Keyring
    from codura_docs.registry import profile_from_seed

    keyring = Keyring.from_settings(settings)
    for pid, seed in settings.load_models().chat_profiles.items():
        profile = profile_from_seed(pid, seed, settings)  # validates the licence allowlist
        params = {
            "temperature": profile.temperature,
            "max_output_tokens": profile.max_output_tokens,
            "disable_thinking": profile.disable_thinking,
            **dict(profile.extra),
        }
        creds = (
            keyring.encrypt(profile.api_key, aad=f"model_profiles:{pid}".encode())
            if profile.api_key
            else None
        )
        conn.execute(
            text(
                "INSERT INTO model_profiles (id, provider, model, base_url, params, license, is_local, "
                "credentials_enc, price_in_per_mtok_micros, price_out_per_mtok_micros) "
                "VALUES (:id, :p, :m, :u, CAST(:params AS jsonb), :l, :loc, :c, :pin, :pout) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": pid,
                "p": profile.provider,
                "m": profile.model,
                "u": profile.base_url,
                "params": json.dumps(params),
                "l": profile.license,
                "loc": profile.is_local,
                "c": creds,
                "pin": profile.price_in_per_mtok_micros,
                "pout": profile.price_out_per_mtok_micros,
            },
        )
    conn.execute(
        text(
            "INSERT INTO system_settings (key, value) VALUES ('default_model_profile', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"v": json.dumps(settings.load_models().default_profile)},
    )


def _ensure_user(
    conn: Connection, email: str, password: str, *, platform_admin: bool, name: str
) -> str:
    existing = conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar()
    if existing:
        return str(existing)
    user_id = new_id("usr")
    conn.execute(
        text(
            "INSERT INTO users (id, email, email_verified_at, name, password_hash, is_platform_admin) "
            "VALUES (:id, :e, now(), :n, :h, :p)"
        ),
        {"id": user_id, "e": email, "n": name, "h": hash_password(password), "p": platform_admin},
    )
    return user_id


def _seed_demo_assistant(conn: Connection, settings: Settings, tenant_id: str) -> None:
    """Demo Knowledge Base + Assistant 'help' with the Hosted Chat Page at /c/demo/help (M2-06)."""
    if conn.execute(
        text("SELECT 1 FROM assistants WHERE tenant_id = :t AND slug = :s"),
        {"t": tenant_id, "s": DEMO_ASSISTANT_SLUG},
    ).first():
        return
    kb_id, asst_id = new_id("kb"), new_id("asst")
    conn.execute(
        text(
            "INSERT INTO knowledge_bases (id, tenant_id, name, description) "
            "VALUES (:id, :t, 'Demo documents', 'Upload documents here to try the demo chat')"
        ),
        {"id": kb_id, "t": tenant_id},
    )
    welcome = {
        "default": "Hi! Ask me anything about the demo documents.",
        "hi": "नमस्ते! डेमो दस्तावेज़ों के बारे में कुछ भी पूछिए।",
        "es": "¡Hola! Pregúntame lo que quieras sobre los documentos de demostración.",
    }
    conn.execute(
        text(
            "INSERT INTO assistants (id, tenant_id, slug, name, knowledge_base_ids, welcome_text, "
            "theme, visitor_email, hosted_enabled) VALUES (:id, :t, :s, 'Demo Help', ARRAY[:kb], "
            "CAST(:w AS jsonb), CAST(:th AS jsonb), 'optional', true)"
        ),
        {
            "id": asst_id,
            "t": tenant_id,
            "s": DEMO_ASSISTANT_SLUG,
            "kb": kb_id,
            "w": json.dumps(welcome),
            "th": json.dumps({"title": "Demo Help", "accent": "#2563eb"}),
        },
    )
    hosted = generate("channel")
    encrypted = Keyring.from_settings(settings).encrypt(hosted, aad=hosted_aad(tenant_id))
    for plaintext, channel, origins, enc, name in (
        (hosted, "hosted", [], encrypted, "Hosted Chat Page"),
        (DEMO_WIDGET_KEY, "widget", DEMO_ORIGINS, None, "Demo widget (dev only)"),
    ):
        conn.execute(
            text(
                "INSERT INTO api_keys (id, tenant_id, kind, prefix, key_hash, name, channel, "
                "assistant_id, allowed_origins, public_key_enc) VALUES (:id, :t, 'channel', :p, "
                ":h, :n, :c, :a, :o, :e) ON CONFLICT (key_hash) DO NOTHING"
            ),
            {
                "id": new_id("key"),
                "t": tenant_id,
                "p": plaintext[:10],
                "h": sha256(plaintext),
                "n": name,
                "c": channel,
                "a": asst_id,
                "o": origins,
                "e": enc,
            },
        )


def _seed_demo_tenant(conn: Connection, settings: Settings) -> None:
    tenant_id = conn.execute(
        text("SELECT id FROM tenants WHERE slug = :s"), {"s": DEMO_TENANT_SLUG}
    ).scalar()
    if tenant_id is None:
        tenant_id = new_id("tnt")
        conn.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, ocr_languages) "
                "VALUES (:id, :s, 'Demo Organisation', 'active', 'eng+hin')"
            ),
            {"id": tenant_id, "s": DEMO_TENANT_SLUG},
        )
    admin = _ensure_user(
        conn, DEMO_ADMIN_EMAIL, DEMO_PASSWORD, platform_admin=False, name="Demo Admin"
    )
    user = _ensure_user(
        conn, DEMO_USER_EMAIL, DEMO_PASSWORD, platform_admin=False, name="Demo User"
    )
    for user_id, role in ((admin, "admin"), (user, "user")):
        conn.execute(
            text(
                "INSERT INTO tenant_members (tenant_id, user_id, role) VALUES (:t, :u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"t": tenant_id, "u": user_id, "r": role},
        )
    _seed_demo_assistant(conn, settings, str(tenant_id))


def run_bootstrap(settings: Settings) -> None:
    settings.validate_for_production()
    upgrade_to_head(settings)
    engine = create_engine(settings.database_owner_url)
    try:
        with engine.begin() as conn:
            _set_role_passwords(conn, settings)
            _record_embedding(conn, settings)
            _seed_model_profiles(conn, settings)
            _ensure_user(
                conn,
                settings.bootstrap_platform_admin_email,
                settings.bootstrap_platform_admin_password.get_secret_value(),
                platform_admin=True,
                name="Platform Admin",
            )
            if settings.seed_demo_tenant and settings.app_env != "production":
                _seed_demo_tenant(conn, settings)
    finally:
        engine.dispose()
    log.info("bootstrap complete", extra={"seed_demo_tenant": settings.seed_demo_tenant})
