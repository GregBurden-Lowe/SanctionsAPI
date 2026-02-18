# Internal queue-ingestion API (bulk / external screening)

This document describes the **internal** HTTP API that lets other web applications submit screening requests in a safe, controlled way. The endpoint **only enqueues** work; it does **not** run sanctions/PEP matching and does **not** return screening results. It is designed to protect the system under burst traffic and to integrate with the existing Postgres-backed job queue.

---

## 1. Intent and goals

- **Accept screening requests** from other web apps (bulk or single) over HTTP.
- **Do not run** sanctions/PEP matching in the request path — all heavy work is done by background workers.
- **Do not overload** the API or CPU; the endpoint returns quickly and only touches the database.
- **Integrate** with the existing `screened_entities` and `screening_jobs` tables (no new persistence).

Other applications can integrate with minimal changes (HTTP only); they enqueue work and can later obtain results via existing mechanisms (e.g. polling `GET /opcheck/jobs/{job_id}` or their own workflow) if needed. This API exists to **protect** the system, not to return results.

---

## 2. Key rules (non-negotiable)

| Rule | Implementation |
|------|----------------|
| 1. Must NOT run sanctions/PEP matching | Endpoint only reads/writes DB; no call to `perform_opensanctions_check`. |
| 2. Must NOT bypass the job queue | New work is only inserted into `screening_jobs`; workers process it. |
| 3. Must return quickly | Only DB lookups + optional single insert per item; no heavy computation. |
| 4. Must be safe under burst traffic | Idempotent by `entity_key`: reuse or already_pending avoids duplicate jobs. |
| 5. Must reuse existing valid screenings | If `screened_entities` has a valid row (`screening_valid_until > now()`), do not enqueue. |

---

## 3. Endpoints

Two endpoints are provided; both are **protected** (see Security below).

| Method | Path | Purpose |
|--------|------|--------|
| `POST` | `/internal/screening/jobs` | Submit a **single** screening request. |
| `POST` | `/internal/screening/jobs/bulk` | Submit **multiple** screening requests (max 500 per request). |

**`/opcheck` is not reused** for this use case; the internal API is separate and does not return screening results.

---

## 4. Request and response shapes

### 4.1 Single: `POST /internal/screening/jobs`

**Request body** (JSON):

```json
{
  "name": "Full name or organization to screen",
  "dob": "2000-01-15",
  "entity_type": "Person",
  "requestor": "System or user name"
}
```

- **`name`** — Required. Full name or organization.
- **`dob`** — Optional. Date of birth (YYYY-MM-DD).
- **`entity_type`** — Optional. `"Person"` or `"Organization"` (default `"Person"`).
- **`requestor`** — Required. User or system requesting the screening.

**Response** (lightweight; **no screening results**):

- `{ "status": "reused" }` — A valid screening already exists; nothing enqueued.
- `{ "status": "already_pending" }` — A job for this entity is already pending or running; nothing enqueued.
- `{ "status": "queued", "job_id": "<uuid>" }` — A new job was enqueued.

On validation error (e.g. missing `name` or `requestor`): **400** with detail.  
If the queue is unavailable (no `DATABASE_URL`): **503**.

### 4.2 Bulk: `POST /internal/screening/jobs/bulk`

**Request body** (JSON):

```json
{
  "requests": [
    { "name": "Entity One", "requestor": "System A" },
    { "name": "Entity Two", "dob": "1985-06-01", "entity_type": "Person", "requestor": "System A" }
  ]
}
```

- **`requests`** — Required. Array of objects with the same fields as the single request. **Max length 500.**

**Response**:

```json
{
  "results": [
    { "status": "reused" },
    { "status": "queued", "job_id": "550e8400-e29b-41d4-a716-446655440000" },
    { "status": "already_pending" },
    { "status": "error", "error": "missing_requestor" }
  ]
}
```

- Each element corresponds to the same index in `requests`.
- **`status`** — One of `reused`, `already_pending`, `queued`, or `error`.
- **`job_id`** — Present only when `status` is `queued`.
- **`error`** — Present when `status` is `error` (e.g. `missing_name`, `missing_requestor`).

Bulk does not fail the whole request on a single bad item; invalid items get `status: "error"` in that slot. If the queue is unavailable: **503**.

---

## 5. Per-request behaviour (queue interaction)

For **each** request item the API:

1. **Validates** input: `name` and `requestor` required; `dob` and `entity_type` optional.
2. **Normalizes** and derives **`entity_key`** (same logic as `/opcheck`: normalized name + entity type + DOB → SHA-256).
3. **Queries `screened_entities`**:
   - If a row exists and **`screening_valid_until > now()`** → do **not** enqueue; return **`reused`**.
4. **Queries `screening_jobs`**:
   - If a row exists for this `entity_key` with **`status IN ('pending', 'running')`** → do **not** enqueue again; return **`already_pending`**.
5. **Otherwise** inserts one row into `screening_jobs` (status `pending`) and returns **`queued`** with **`job_id`**.

So:

- All heavy screening is done by **background workers**.
- This endpoint **only** reads `screened_entities` and `screening_jobs`, and **inserts** into `screening_jobs` when needed.
- Calling the endpoint many times with the same entity is **idempotent by `entity_key`**: at most one pending/running job per entity; valid screenings are reused.

---

## 6. Database support: `has_pending_or_running_job`

To avoid duplicate jobs for the same entity, the API uses a helper in **`screening_db.py`**:

```python
async def has_pending_or_running_job(conn, entity_key: str) -> bool:
    """True if there is a job for this entity_key with status pending or running."""
    row = await conn.fetchrow(
        """
        SELECT 1 FROM screening_jobs
        WHERE entity_key = $1 AND status IN ('pending', 'running')
        LIMIT 1
        """,
        entity_key,
    )
    return row is not None
```

This is used **after** checking for a valid screening and **before** calling `enqueue_job`.

---

## 7. Core logic: `_internal_screening_outcome`

The same per-item logic is used for both single and bulk. It validates, then returns one of `reused`, `already_pending`, or `queued` (plus `job_id` when queued), or `error` with a reason:

```python
async def _internal_screening_outcome(conn, item: InternalScreeningRequest) -> dict:
    """
    One entity: validate, then reused | already_pending | queued.
    Returns { status, job_id? }. Does NOT run screening; does NOT return results.
    """
    name = (item.name or "").strip()
    requestor = (item.requestor or "").strip()
    if not name:
        return {"status": "error", "error": "missing_name"}
    if not requestor:
        return {"status": "error", "error": "missing_requestor"}
    dob = (item.dob.strip() if isinstance(item.dob, str) else item.dob) or None
    entity_type = (item.entity_type or "Person")

    entity_key = derive_entity_key(display_name=name, entity_type=entity_type, dob=dob)

    valid = await screening_db.get_valid_screening(conn, entity_key)
    if valid is not None:
        return {"status": "reused"}

    if await screening_db.has_pending_or_running_job(conn, entity_key):
        return {"status": "already_pending"}

    job_id = await screening_db.enqueue_job(
        conn, entity_key=entity_key, name=name, date_of_birth=dob,
        entity_type=entity_type, requestor=requestor,
    )
    return {"status": "queued", "job_id": job_id}
```

Note: **no** call to `perform_opensanctions_check`; **no** screening results in the return value.

---

## 8. Security requirements

The internal API is **more powerful** than the public screening flow (it can drive large volumes of work) and **must** be protected. It must not be exposed without at least one of the following:

- **Static API key**
- **IP allowlisting**
- **Or both**

### 8.1 Implementation: `require_internal_screening_auth`

Both internal routes use a FastAPI dependency that enforces:

- **API key** (optional): from environment **`INTERNAL_SCREENING_API_KEY`**.
  - Client sends it via header **`X-Internal-Screening-Key`** or **`Authorization: Bearer <key>`**.
  - If the env var is set, the provided value must match; otherwise **401** (invalid or missing API key).
- **IP allowlist** (optional): from environment **`INTERNAL_SCREENING_IP_ALLOWLIST`** (comma-separated list of IPs).
  - Client IP is taken from **`X-Forwarded-For`** (first value) or, if absent, **`request.client.host`**.
  - If the env var is set, the client IP must be in the list; otherwise **403** (client IP not allowed).

If **both** API key and IP allowlist are set, **both** must pass. If **neither** is set, the internal API is treated as disabled and returns **503** with a message that no API key or IP allowlist is configured.

```python
async def require_internal_screening_auth(request: Request) -> None:
    api_key = os.environ.get("INTERNAL_SCREENING_API_KEY", "").strip()
    allowlist_raw = os.environ.get("INTERNAL_SCREENING_IP_ALLOWLIST", "").strip()
    allowlist = [s.strip() for s in allowlist_raw.split(",") if s.strip()]

    if not api_key and not allowlist:
        raise HTTPException(
            status_code=503,
            detail="Internal screening API is disabled (no API key or IP allowlist configured)",
        )

    if api_key:
        provided = request.headers.get("x-internal-screening-key", "").strip()
        if not provided and request.headers.get("authorization", "").startswith("Bearer "):
            provided = request.headers.get("authorization", "").replace("Bearer ", "", 1).strip()
        if provided != api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    if allowlist:
        client_ip = _internal_screening_client_ip(request)
        if client_ip not in allowlist:
            raise HTTPException(status_code=403, detail="Client IP not allowed")
```

Routes are registered with:

```python
@app.post("/internal/screening/jobs", dependencies=[Depends(require_internal_screening_auth)])
@app.post("/internal/screening/jobs/bulk", dependencies=[Depends(require_internal_screening_auth)])
```

### 8.2 Configuration summary

| Environment variable | Purpose |
|---------------------|--------|
| `INTERNAL_SCREENING_API_KEY` | Static secret; client sends via `X-Internal-Screening-Key` or `Authorization: Bearer <key>`. At least one of this or IP allowlist must be set. |
| `INTERNAL_SCREENING_IP_ALLOWLIST` | Comma-separated allowed client IPs (e.g. `10.0.0.1,192.168.1.100`). |

---

## 9. When the queue is unavailable

If **`DATABASE_URL`** is not set (no Postgres, no job queue):

- **`POST /internal/screening/jobs`** and **`POST /internal/screening/jobs/bulk`** both return **503** with detail `"Screening queue requires DATABASE_URL"`.

The internal API does **not** fall back to synchronous screening; it only enqueues.

---

## 10. Scope limits (what was not changed)

- **`/opcheck`** — Not refactored; behaviour unchanged.
- **Persistence** — No new tables or stores; only existing `screened_entities` and `screening_jobs`.
- **Worker concurrency** — Not increased; same 1–2 workers as before.
- **Auto re-screening** — Not added; all screening remains demand-driven.
- **Business rules** — Unchanged (e.g. 12-month validity, one row per entity, replace on re-screen).

---

## 11. Success criteria (design goals)

After this implementation:

- Other web apps can **submit large numbers** of screening requests over HTTP without changing their integration model.
- The API **stays responsive** because the endpoint only does DB work.
- **CPU usage** stays predictable; screening runs in workers at a controlled pace.
- **No duplicate work** per entity: reuse when valid, already_pending when a job exists, otherwise one new job.
- The design is **easy to explain** to auditors and other teams: enqueue-only, protected, idempotent by entity, no results exposed.
