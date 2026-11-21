# ADR-0009 — Blob Store Port: local volume default, S3-compatible for prod

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** FR-05, FR-06, FR-07, NFR-03, SEC-03

## Context

Original uploads must be kept for re-ingest (FR-07) and re-embedding. Locally we want zero setup;
in production, durable object storage.

## Decision

`BlobStore` Port (`put`, `open`, `delete`, `exists`), content-addressed by SHA-256
(`blobs/<ws>/<aa>/<hash>`). Adapters: `local` (Docker volume, default) and `s3` (any
S3-compatible endpoint: AWS S3, Cloudflare R2, Backblaze B2, self-hosted Garage/SeaweedFS).

## Alternatives considered

### MinIO in the default compose — rejected
Another container for local dev, and the community edition's distribution and licensing (AGPL)
have become less predictable. The `s3` Adapter can still target MinIO if someone runs it.

### Store bytes in Postgres (`bytea` / large objects) — rejected
Bloats backups and WAL; 50 MB files are a poor fit.

## Consequences

Local dev needs nothing extra; prod switches with `BLOB_STORE=s3`. Content addressing gives
dedupe (FR-05) and safe keys (no user-controlled paths).
