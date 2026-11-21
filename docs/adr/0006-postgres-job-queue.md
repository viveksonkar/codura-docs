# ADR-0006 — Postgres-backed Job queue (`SKIP LOCKED`), no Redis

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** FR-04, NFR-02, ADR-0003

## Context

Ingestion and re-embedding are slow and must run outside requests, survive restarts and retry.
Throughput is modest (documents per minute, not messages per second). Fewer containers makes the
local stack faster to start and simpler to run in production.

## Decision

A `jobs` table (`kind`, `payload jsonb`, `status`, `attempts`, `max_attempts`, `run_after`,
`locked_by`, `locked_until`, `last_error`) in Postgres. Workers claim with
`SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` ordered by `run_after`, hold a lease
(`locked_until`) renewed while working, and reclaim expired leases. `LISTEN/NOTIFY` wakes idle
workers; polling every 2 s is the fallback. Enqueue happens in the same transaction as the domain
write (upload ⇒ Document row + Job, atomically). Handlers are idempotent per Job payload.

## Alternatives considered

### Celery / RQ / arq + Redis — rejected
Extra service and broker semantics for low volume; loses the transactional enqueue.

### `procrastinate` (Postgres-based library) — considered, not chosen for M0
Viable; our needs are ~150 lines and we want lease semantics we fully control. Revisit if we need
cron schedules, priorities, or the queue code grows.

## Consequences

Simple ops and exactly-once enqueue with the domain write. We own retries, backoff and lease
handling, with integration tests for crash/reclaim. Scale by `--scale worker=N`.
