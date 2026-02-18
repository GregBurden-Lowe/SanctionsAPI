# Screening persistence and job queue

This document describes the PostgreSQL-backed persistence layer for annual sanctions/PEP screening: two tables (current state + job queue), request flow, API behaviour, and the background worker.

---

## 1. Business goals and rules

The system is designed to:

- **Protect limited CPU** (e.g. 2 vCPU / 8 GB RAM) by controlling how many screenings run at once.
- **Stay compliant**: screen each entity at most once per 12 months unless re-screening is required.
- **Be audit-friendly**: store full screening results and metadata; no automatic re-screening without demand.
- **Be deterministic**: same logical entity always maps to the same key; re-screen replaces the existing row.

**Business rules:**

| Rule | Behaviour |
|------|-----------|
| 1 | An entity is screened at most once every 12 months (unless re-screened on demand). |
| 2 | If a valid screening exists (`screening_valid_until > now()`), the API reuses it and does **not** enqueue a job. |
| 3 | If no row exists or the screening is older than 12 months, a new screening is run on demand (via the queue). |
| 4 | When re-screened, the existing `screened_entities` row is **replaced** (upsert), not duplicated. |
| 5 | Screening work is processed by 1–2 background workers so the API and server are not overwhelmed. |
| 6 | Only **search results** are stored; the sanctions dataset itself is not stored in Postgres. |

**Validity rule:** `screening_valid_until` is extended **only** when a new screening is actually performed. Returning a cached result to the client does **not** extend validity and does **not** trigger an audit event.

---

## 2. High-level architecture

- **PostgreSQL** is the single system of record (no Redis, no external message broker).
- **Two tables:**
  1. **`screened_entities`** — current screening state per logical entity (one row per `entity_key`).
  2. **`screening_jobs`** — queue of screening jobs (pending → running → completed/failed).
- **Request flow:**
  1. Client sends `POST /opcheck` with name, DOB, entity type, requestor.
  2. API normalizes input and derives `entity_key`.
  3. API looks up `screened_entities`: if a row exists and `screening_valid_until > now()`, return that result (200, no job).
  4. Otherwise, insert a row into `screening_jobs` (status `pending`) and return **202 Accepted** with `job_id` and `Location: /opcheck/jobs/{job_id}`.
  5. A background worker claims one job at a time (`SELECT ... FOR UPDATE SKIP LOCKED`), runs the screening, upserts `screened_entities`, and marks the job `completed` (or `failed`).
  6. Client can poll `GET /opcheck/jobs/{job_id}` until `status` is `completed` or `failed`; when `completed`, the response includes `result` (same shape as a direct screening response).

---

## 3. Schema

The schema is defined in **`schema.sql`** and is also applied programmatically by **`screening_db.ensure_schema()`** at API startup when `DATABASE_URL` is set.

### 3.1 Table: `screened_entities`

Stores the **latest** screening result per logical entity. One row per `entity_key`; overwritten on re-screen.

```sql
CREATE TABLE IF NOT EXISTS screened_entities (
    entity_key       TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    normalized_name  TEXT NOT NULL,
    date_of_birth    DATE,
    entity_type      TEXT NOT NULL DEFAULT 'Person',

    last_screened_at     TIMESTAMPTZ NOT NULL,
    screening_valid_until TIMESTAMPTZ NOT NULL,

    status          TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    score           NUMERIC(5,2) NOT NULL,
    uk_sanctions_flag BOOLEAN NOT NULL DEFAULT FALSE,
    pep_flag        BOOLEAN NOT NULL DEFAULT FALSE,

    result_json     JSONB NOT NULL,
    last_requestor  TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_screened_entities_valid_until
    ON screened_entities (screening_valid_until);
CREATE INDEX IF NOT EXISTS idx_screened_entities_last_screened
    ON screened_entities (last_screened_at);
```

- **`entity_key`** — Unique identifier for the logical entity (derived from normalized name + entity type + DOB; see below).
- **`screening_valid_until`** — Set to `last_screened_at + 12 months` when a screening is run; used to decide “reuse vs enqueue.”
- **`result_json`** — Full screening output (JSONB) for audit and for returning to the client / PDF export.

There is **no** foreign key from `screening_jobs` to `screened_entities`, because jobs are created before the entity row may exist.

### 3.2 Table: `screening_jobs`

Lightweight job queue: one row per “please screen this entity if required.”

```sql
CREATE TABLE IF NOT EXISTS screening_jobs (
    job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_key      TEXT NOT NULL,

    name            TEXT NOT NULL,
    date_of_birth   TEXT,
    entity_type     TEXT NOT NULL DEFAULT 'Person',
    requestor       TEXT NOT NULL,

    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_screening_jobs_pending
    ON screening_jobs (created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_screening_jobs_status
    ON screening_jobs (status);
```

- Workers claim with `SELECT ... WHERE status = 'pending' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`.
- Completed/failed rows can be pruned periodically; the queue is not a long-term audit store.

---

## 4. Entity key derivation

The same logical entity must always map to the same key so that:

- Cached results are reused correctly.
- Re-screening updates the same row (no duplicates).

Implementation in **`utils.py`**:

```python
def _normalize_dob(dob: Optional[str]) -> Optional[str]:
    if not dob:
        return None
    try:
        dt = pd.to_datetime(str(dob), errors="coerce")
        if pd.isna(dt):
            return None
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def derive_entity_key(display_name: str, entity_type: str, dob: Optional[str]) -> str:
    """
    Stable key for one logical entity: normalized name + entity type + DOB.
    Used for screening cache and job queue. Same inputs => same key.
    """
    import hashlib
    norm_name = _normalize_text(display_name or "")
    et = (entity_type or "Person").strip().lower()
    dob_str = _normalize_dob(dob) if dob else ""
    payload = f"{norm_name}|{et}|{dob_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

`_normalize_text` (same module) normalizes Unicode (NFKD), strips punctuation, lowercases, and collapses spaces. The key is a SHA-256 hex digest of `normalized_name|entity_type|normalized_dob`, so it is deterministic and fixed length.

---

## 5. Database module: `screening_db.py`

The API uses **asyncpg**; the worker uses **psycopg2** (sync). The module provides the async API used by the FastAPI app.

### 5.1 Pool and schema

- **`get_pool()`** — Creates an asyncpg connection pool if `DATABASE_URL` is set; otherwise returns `None`. Cached in a module-level variable.
- **`close_pool()`** — Closes the pool (used at app shutdown).
- **`ensure_schema(conn)`** — Runs `CREATE TABLE IF NOT EXISTS` (and indexes) for both tables so they exist without requiring a separate migration step.

```python
async def get_pool():
    """Return asyncpg pool if DATABASE_URL is set; else None."""
    global _pool
    if _pool is not None:
        return _pool
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    try:
        import asyncpg
        _pool = await asyncpg.create_pool(url, min_size=1, max_size=4, command_timeout=30)
        return _pool
    except Exception as e:
        logger.warning("screening_db: pool create failed: %s", e)
        return None
```

### 5.2 Lookup: `get_valid_screening(conn, entity_key)`

Returns the screening result **only** if a row exists and `screening_valid_until > NOW()`. Does **not** update any timestamps (read-only for validity).

```python
async def get_valid_screening(conn, entity_key: str) -> Optional[Dict[str, Any]]:
    """
    If a row exists and screening_valid_until > now(), return result_json.
    Does NOT extend validity; read-only.
    """
    row = await conn.fetchrow(
        """
        SELECT result_json, screening_valid_until
        FROM screened_entities
        WHERE entity_key = $1 AND screening_valid_until > NOW()
        """,
        entity_key,
    )
    if row is None:
        return None
    return dict(row["result_json"])
```

### 5.3 Upsert: `upsert_screening(...)`

Called by the **worker** after a successful screening (the worker uses its own sync SQL; this is the async equivalent). Inserts or updates the single row for `entity_key`, sets `last_screened_at` and `screening_valid_until = last_screened_at + 365 days`, and fills status/risk/score/flags from the result plus `result_json`.

```python
# Excerpt: validity is set only on write
now = datetime.now(timezone.utc)
valid_until = now + timedelta(days=365)
# ... then INSERT ... ON CONFLICT (entity_key) DO UPDATE SET ...
```

### 5.4 Queue: `enqueue_job(...)` and `get_job_status(conn, job_id)`

- **`enqueue_job(conn, entity_key, name, date_of_birth, entity_type, requestor)`** — Inserts a row with `status = 'pending'`, returns `job_id` (UUID string).
- **`get_job_status(conn, job_id)`** — Returns `{ "status", "job_id" }` and, when `status == "completed"`, loads `result` from `screened_entities` by the job’s `entity_key`. On failure, includes `error_message`. Returns `None` if the job does not exist.

```python
async def get_job_status(conn, job_id: str) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        "SELECT status, entity_key, error_message FROM screening_jobs WHERE job_id = $1",
        job_id,
    )
    if row is None:
        return None
    out = {"status": row["status"], "job_id": job_id}
    if row["error_message"]:
        out["error_message"] = row["error_message"]
    if row["status"] == "completed":
        entity_row = await conn.fetchrow(
            "SELECT result_json FROM screened_entities WHERE entity_key = $1",
            row["entity_key"],
        )
        if entity_row:
            out["result"] = dict(entity_row["result_json"])
    return out
```

---

## 6. API behaviour (`api_server.py`)

### 6.1 Lifespan

When the app starts, if `DATABASE_URL` is set, a pool is created and `ensure_schema` is run so tables exist. On shutdown, `close_pool()` is called.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB pool and ensure schema when DATABASE_URL is set."""
    pool = await screening_db.get_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await screening_db.ensure_schema(conn)
            logger.info("screening_db: schema ensured")
        except Exception as e:
            logger.warning("screening_db: ensure_schema failed: %s", e)
    yield
    await screening_db.close_pool()


app = FastAPI(..., lifespan=lifespan)
```

### 6.2 POST /opcheck

1. Validate `requestor` and `name` (400 if missing).
2. Normalize `name`, `dob`, `entity_type`, `requestor`.
3. If **no pool** (`DATABASE_URL` unset): run the check synchronously and return the result (unchanged legacy behaviour).
4. If **pool exists**:
   - Compute `entity_key = derive_entity_key(display_name=name, entity_type=entity_type, dob=dob)`.
   - Call `get_valid_screening(conn, entity_key)`.
   - If a valid result is returned: return it with **200** (no job, no audit, no validity extension).
   - Otherwise: `enqueue_job(...)`, then return **202 Accepted** with body and header:

```python
return JSONResponse(
    status_code=202,
    content={"job_id": job_id, "message": "Screening queued"},
    headers={"Location": f"/opcheck/jobs/{job_id}"},
)
```

### 6.3 GET /opcheck/jobs/{job_id}

- If DB is disabled: **404** with `{"error": "not_found", "message": "Job not found"}`.
- Otherwise: `get_job_status(conn, job_id)`; if `None`, return **404**; else return the dict (includes `status`, `job_id`, and when completed, `result`; when failed, `error_message`).

---

## 7. Background worker: `screening_worker.py`

The worker runs as a **separate process** (e.g. 1–2 instances). It uses **psycopg2** (sync) and the same codebase as the API (e.g. `utils.perform_opensanctions_check`).

### 7.1 Loop

1. Connect to Postgres with `DATABASE_URL`.
2. In a transaction, **claim** one job:
   - `SELECT job_id, entity_key, name, date_of_birth, entity_type, requestor FROM screening_jobs WHERE status = 'pending' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`.
   - If no row: rollback, close, sleep (`SCREENING_WORKER_POLL_SECONDS`, default 5), then repeat.
3. Update the row: `status = 'running'`, `started_at = NOW()`; commit.
4. **Idempotency:** Query `screened_entities` for that `entity_key` with `screening_valid_until > NOW()`. If a row exists, mark the job `completed`, `finished_at = NOW()`, commit, and continue the loop (no screening run).
5. Otherwise run **`perform_opensanctions_check(name, dob, entity_type, requestor)`** (sync).
6. On success: upsert `screened_entities` (same fields as in `screening_db.upsert_screening`), set `screening_valid_until = last_screened_at + 12 months`, then set job to `completed`, `finished_at = NOW()`, commit.
7. On exception: set job to `failed`, `error_message = str(e)` (truncated), commit; log and continue.
8. Connection is closed in a `finally`; then a short sleep and the loop repeats.

Relevant claim and idempotency snippet:

```python
# Claim one pending job
cur.execute(
    """
    SELECT job_id, entity_key, name, date_of_birth, entity_type, requestor
    FROM screening_jobs
    WHERE status = 'pending'
    ORDER BY created_at
    LIMIT 1
    FOR UPDATE SKIP LOCKED
    """
)
row = cur.fetchone()
# ...
# Idempotent: if a valid screening already exists, reuse it and mark job completed
cur.execute(
    "SELECT result_json FROM screened_entities WHERE entity_key = %s AND screening_valid_until > NOW()",
    (entity_key,),
)
existing = cur.fetchone()
if existing:
    # UPDATE screening_jobs SET status = 'completed', finished_at = NOW() WHERE job_id = %s
    # then continue
```

### 7.2 Configuration

- **`DATABASE_URL`** — Required; same as the API.
- **`SCREENING_WORKER_POLL_SECONDS`** — Seconds to sleep when no job is available (default 5, minimum 2).

Run from the project root so that `utils` and (if needed) parquet data are available:

```bash
python screening_worker.py
```

---

## 8. Dependencies and deployment

- **requirements.txt** includes:
  - `asyncpg` — for the FastAPI app.
  - `psycopg2-binary` — for the sync worker.

**Without `DATABASE_URL`:** the API behaves as before: every `POST /opcheck` runs the check synchronously and returns the result; no queue, no persistence.

**With `DATABASE_URL` set:**

1. Start the API (e.g. `uvicorn api_server:app`). Schema is ensured on startup.
2. Start one or two worker processes (e.g. `python screening_worker.py`).
3. Clients that receive **202** from `POST /opcheck` can poll `GET /opcheck/jobs/{job_id}` until `status` is `completed` or `failed`; when `completed`, use `result` as the screening response.

This design keeps concurrency low, avoids duplicate screenings per entity per validity window, extends validity only when a new screening is run, and keeps the queue and state in PostgreSQL only—no Redis or external broker.
