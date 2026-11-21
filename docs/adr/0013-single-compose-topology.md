# ADR-0013 — One docker compose with profiles and a prod override

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** NFR-02, NFR-03, deployment.md

## Context

The brief: "full docker compose so that I can spin the environment locally very quickly to test
and then take it to production". Some components are optional (GPU, WordPress test site, OCR).

## Decision

- Root `docker-compose.yml` is the complete local stack and the production base.
- Compose **profiles** for optional parts: `gpu` (Ollama with NVIDIA reservation), `wp` (WordPress
  + MariaDB for plugin testing), `ocr` (docling, optional M4; MVP OCR is Tesseract inside the worker, ADR-0016).
- Overrides: `compose.dev.yml` (hot reload, bind mounts) and `compose.prod.yml` (TLS via Caddy, no
  published internal ports, restart policies, limits, registry images).
- One backend image for `api`, `worker` and `migrate` (different commands). One-shot `migrate`
  and `ollama-init` services gate startup via `depends_on: condition: service_completed_successfully`.
- Caddy is the single ingress (`/api` → api, everything else → web).

## Alternatives considered

### Kubernetes / Helm from day one — rejected
Overkill for a single-host product; Compose on a VPS meets the target. The service split maps
cleanly to k8s later.

### Separate compose files per environment — rejected
Drift between them is exactly what NFR-03 forbids; overrides keep one base.

## Consequences

`docker compose up` is the whole onboarding. Production is a single host unless the database,
Blob Store or LLM are moved out via config (all supported by the Ports and `DATABASE_URL`).
