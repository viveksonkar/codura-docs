"""M2 web Channel: retrievable hosted keys (ADR-0025), log-viewer and insights indexes.

Revision ID: 0003_web_channel
Revises: 0002_content
"""

from __future__ import annotations

from alembic import op

revision = "0003_web_channel"
down_revision = "0002_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_keys ADD COLUMN public_key_enc bytea")
    op.execute(
        "CREATE UNIQUE INDEX api_keys_one_hosted ON api_keys (tenant_id, assistant_id) "
        "WHERE channel = 'hosted' AND revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX conv_tenant_recent ON conversations (tenant_id, last_message_at DESC, id)"
    )
    op.execute(
        "CREATE INDEX conv_email_hash ON conversations (tenant_id, visitor_email_hash) "
        "WHERE visitor_email_hash IS NOT NULL"
    )
    op.execute("CREATE INDEX messages_tenant_time ON messages (tenant_id, created_at)")

    op.execute("DROP FUNCTION IF EXISTS resolve_hosted(text, text)")
    op.execute("""
        CREATE FUNCTION resolve_hosted(p_tenant_slug text, p_assistant_slug text)
        RETURNS TABLE (tenant_id text, assistant_id text, public_key_enc bytea)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT a.tenant_id, a.id, k.public_key_enc
            FROM assistants a
            JOIN tenants t ON t.id = a.tenant_id
            JOIN api_keys k ON k.tenant_id = a.tenant_id AND k.assistant_id = a.id
                           AND k.channel = 'hosted' AND k.revoked_at IS NULL
                           AND k.public_key_enc IS NOT NULL
            WHERE t.slug = p_tenant_slug::citext AND a.slug = p_assistant_slug::citext
              AND a.hosted_enabled AND t.status = 'active'
        $$""")
    op.execute("REVOKE ALL ON FUNCTION resolve_hosted(text, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION resolve_hosted(text, text) TO codura_app")

    # last_used_at is touched before the request has a Tenant context (ADR-0025).
    op.execute("""
        CREATE FUNCTION touch_api_key(p_key_id text) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            UPDATE api_keys SET last_used_at = now()
            WHERE id = p_key_id AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')
        $$""")
    op.execute("REVOKE ALL ON FUNCTION touch_api_key(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION touch_api_key(text) TO codura_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS touch_api_key(text)")
    op.execute("DROP FUNCTION IF EXISTS resolve_hosted(text, text)")
    op.execute("""
        CREATE FUNCTION resolve_hosted(p_tenant_slug text, p_assistant_slug text)
        RETURNS TABLE (tenant_id text, assistant_id text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT a.tenant_id, a.id FROM assistants a JOIN tenants t ON t.id = a.tenant_id
            WHERE t.slug = p_tenant_slug::citext AND a.slug = p_assistant_slug::citext
              AND a.hosted_enabled AND t.status = 'active'
        $$""")
    op.execute("REVOKE ALL ON FUNCTION resolve_hosted(text, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION resolve_hosted(text, text) TO codura_app")
    op.execute("DROP INDEX IF EXISTS messages_tenant_time")
    op.execute("DROP INDEX IF EXISTS conv_email_hash")
    op.execute("DROP INDEX IF EXISTS conv_tenant_recent")
    op.execute("DROP INDEX IF EXISTS api_keys_one_hosted")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS public_key_enc")
