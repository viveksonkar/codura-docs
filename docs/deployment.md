# Deployment

**Status:** Draft v0.3 · **Last updated:** 2026-11-18 · **Implements:** NFR-01…03, NFR-11 · ADR-0013

## 1. Local (default)

```bash
cp .env.example .env              # local-first defaults, no secrets needed
docker compose up -d              # GPU by default (COMPOSE_FILE in .env adds compose.gpu.yml); first run pulls ~4 GB of models
#   CPU-only machine: remove compose.gpu.yml from COMPOSE_FILE in .env
docker compose --profile observability up -d   # + Prometheus/Grafana (ADR-0022)
docker compose --profile wp up -d    # M3: + local WordPress at :8081 with the plugin mounted
docker compose logs -f ollama-init   # watch model pull
```

| URL | What |
|---|---|
| http://localhost:8080 | Admin Console (Platform Admin from `BOOTSTRAP_PLATFORM_ADMIN_*`; demo Tenant Admin `admin@demo.test`) |
| http://localhost:8080/c/demo/help | Hosted Chat Page of the demo Tenant (M2) |
| http://localhost:8080/api/docs | OpenAPI UI (non-production only) |
| http://localhost:8080/widget/v1/widget.js | Widget bundle |
| http://localhost:8080/demo | Static demo page with the Widget (dev only) |
| http://localhost:11434 | Ollama (bound to 127.0.0.1) |
| http://localhost:8025 | Mailpit: signup/verify/reset/invite emails |
| http://localhost:8080/signup | Self-service organisation signup |
| http://localhost:3000 | Grafana (`observability` profile) |

Dev loop: `docker compose -f docker-compose.yml -f compose.dev.yml up` bind-mounts `backend/src`
with `uvicorn --reload` and runs the Vite dev server for `web/`.

**Windows notes:** use Docker Desktop with the WSL2 backend; keep the repo inside WSL or accept
slower bind mounts; GPU needs a recent NVIDIA driver with WSL support.

## 2. Compose services, ordering and health

`postgres (healthy) → migrate (completed) → api, worker`
`ollama (healthy) → ollama-init (completed) → api, worker`
`api (healthy) → caddy`

Every long-running service has a healthcheck; `api` uses `/readyz`.

## 3. Environment variables (`.env.example`)

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `local` | `local` / `production`; production enforces SEC-12 |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Used in snippets and CORS |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `codura` / `codura` / `codura_docs` | Change in prod |
| `DATABASE_OWNER_URL` | derived in compose | Owner role: migrations + bootstrap only |
| `DATABASE_APP_URL` / `DATABASE_WORKER_URL` / `DATABASE_PLATFORM_URL` | derived in compose | Non-owner roles `codura_app` / `codura_worker` / `codura_platform`; RLS always applies (ADR-0014) |
| `DB_APP_PASSWORD` / `DB_WORKER_PASSWORD` / `DB_PLATFORM_PASSWORD` | dev values | Set on the roles by `migrate`; refused defaults in production |
| `SESSION_SECRET` | `dev-only-change-me` | Refused in production |
| `BOOTSTRAP_PLATFORM_ADMIN_EMAIL` / `_PASSWORD` | `platform@local.test` / `admin` | First-boot Platform Admin; refused default in production |
| `SEED_DEMO_TENANT` | `true` locally, `false` in prod | Creates Tenant `demo` (Tenant Admin `admin@demo.test`, Tenant User `user@demo.test`), Assistant `help` |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | |
| `COMPOSE_FILE` | `docker-compose.yml:compose.gpu.yml` | GPU default (ADR-0018); on Windows set `COMPOSE_PATH_SEPARATOR=:` or use `;` |
| `OLLAMA_PULL_MODELS` | `bge-m3,qwen3:4b` | Read by `ollama-init`; optional `qwen2.5:7b` |
| `OLLAMA_MAX_LOADED_MODELS` / `OLLAMA_KEEP_ALIVE` | `2` / `30m` | Keep chat + embedding models resident in 6 GB VRAM |
| `SECRETS_MASTER_KEY` | dev value | AES-256 key (base64) for credentials and Visitor emails; refused default in production (SEC-17) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` | `mailpit` / `1025` / – / – / `no-reply@codura.local` | `EmailSender` (ADR-0020) |
| `SIGNUP_ENABLED` | `true` | Self-service signup |
| `PAYMENT_PROVIDER` | `manual` | `manual` (no network) / `stripe`. Plans, quotas and the Trial live in the database (specs/billing.md); `TRIAL_*` variables were removed in M3 |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | empty | Required for `stripe`. Webhook URL `${PUBLIC_BASE_URL}/api/webhooks/payments/stripe` with `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed` |
| `BILLING_SCHEDULER_ENABLED` | `true` | Worker enqueues rollups, snapshots and the billing cycle (specs/billing.md §7) |
| `MODEL_CONFIG_FILE` | `/app/config/models.yaml` | Model Profiles (switchable), fixed embedding, OCR ([architecture.md](./architecture.md) §4) |
| `OCR_LANG_PACKS` (build arg) | `eng hin spa fra deu ara chi_sim` | Tesseract packs baked into the image (ADR-0016); adjust per Q3 |
| `OCR_DEFAULT_LANGUAGES` | `eng` | Default `ocr_languages` for new Tenants |
| `MAX_PAGES` | `500` | Per Document (NFR-07) |
| `CHAT_MAX_CONCURRENT_PER_TENANT` | `2` | SEC-06: one Tenant can't starve the shared LLM |
| `DEFAULT_CHAT_PROFILE` | `local-default` | For new Assistants |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | empty | Seed only; afterwards credentials are stored encrypted per Model Profile (SEC-17) |
| `BLOB_STORE` | `local` | `local` / `s3` |
| `BLOB_LOCAL_PATH` | `/data/blobs` | Volume `blobs` |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | empty | For `s3` |
| `MAX_UPLOAD_MB` | `50` | NFR-07 |
| `WORKER_CONCURRENCY` | `2` | Jobs per worker process |
| `LOG_LEVEL` | `info` | |

## 4. Production

Same images, different overrides (NFR-03):

```bash
docker compose -f docker-compose.yml -f compose.prod.yml up -d
```

`compose.prod.yml`:
- `caddy` binds 80/443 with automatic TLS for `PUBLIC_BASE_URL`'s host; `api`, `postgres`,
  `ollama` publish **no** host ports.
- `restart: unless-stopped`, resource limits, log rotation.
- Images pulled by tag from a registry (GHCR) built in CI, not built on the server.
- Secrets via Docker secrets or a root-only `.env`.
- Optional: point `DATABASE_URL` at managed Postgres with pgvector, `BLOB_STORE=s3`, and a chat
  profile at a remote Provider or a separate GPU host running Ollama/vLLM.

Sizing guide (single host): 4 vCPU / 16 GB RAM runs `qwen2.5:3b` + `bge-m3` + OCR on CPU for
light traffic. For real traffic or many Tenants, use a GPU with ≥ 12 GB VRAM for 7B models, or
point the chat Model Profile at a remote Provider. OCR stays on CPU; scale it with
`--scale worker=N`.

## 5. Backup & restore (M5, NFR-11)

- `scripts/backup.sh`: `pg_dump -Fc` + tar/rsync of the `blobs` volume (or S3 versioning), with
  retention. `scripts/restore.sh` documented and exercised in CI on a throwaway stack.
- Embeddings are reproducible from Blobs, but are backed up anyway (OCR + re-embedding is slow).

## 6. Upgrades

`migrate` runs `alembic upgrade head` on every deploy before `api`/`worker` start. Migrations
must be backward-compatible with the previous app version (expand → migrate → contract) so a
rolling restart is safe.
