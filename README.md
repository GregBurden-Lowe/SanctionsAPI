# Sanctions & PEP Screening API

FastAPI backend for OpenSanctions/PEP screening with an optional Vite + React frontend. The API contract is stable for integration with Dynamics, Power Apps, and other internal systems.

## Repository structure

| Path | Contents |
|------|----------|
| **/** | Backend entrypoints (`api_server.py`, `screening_worker.py`), core modules (`utils.py`, `screening_db.py`), schema (`schema.sql`), Docker & config |
| **[docs/](docs/)** | Architecture and API docs (persistence, internal screening API, Nginx hardening) |
| **[frontend/](frontend/)** | Vite + React UI (screening, admin, design tokens) |
| **[tests/](tests/)** | API contract tests |

Quick links: [Connecting the database](#connecting-the-database) · [API contract](#api-contract-frozen) · [Docker](#docker-recommended-for-production--digitalocean)

## Local development

### Backend

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

- API: http://localhost:8000  
- Health: http://localhost:8000/health  

### Frontend (Vite dev server with proxy)

```bash
cd frontend
npm install
npm run dev
```

- App: http://localhost:5173  
- API calls are proxied to `http://127.0.0.1:8000` (no CORS issues).

### Environment variables (backend)

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection URL for screening persistence, job queue, and GUI user accounts. If unset, screening runs synchronously with no cache or queue; the website does not require login. See [Connecting the database](#connecting-the-database) below. |
| `GUI_JWT_SECRET` | Secret used to sign JWT tokens (min 32 characters when using the database). Required in production; app will not start if missing or weak. |
| `ALLOW_WEAK_JWT_SECRET` | Set to `true` for local dev/test only to allow a short or default secret. Ignored/rejected outside dev/test environment modes. |
| `REFRESH_OPENSANCTIONS_API_KEY` | Optional. When set, **POST /refresh_opensanctions** can be called with this key via header `X-Refresh-Opensanctions-Key` or `Authorization: Bearer <key>` (for scripts/cron). When unset, only admin JWT can be used. |
| `TRUSTED_PROXY_IPS` | Comma-separated IPs of trusted reverse proxies (e.g. `127.0.0.1,::1`). Only when the direct client is in this set is `X-Forwarded-For` used for client IP (rate limiting and internal screening). Default: `127.0.0.1,::1`. |
| `RATE_LIMIT_STORAGE_URL` | Optional shared backend for rate limiting (recommended for multi-instance deploys), e.g. `redis://:password@redis-host:6379/0`. If unset, in-memory per-process limiting is used. |

**Rate limiting** (per client IP): `/auth/login` 5/min, `/auth/signup` 3/min, `POST /opcheck` 60/min, `GET /opcheck/jobs/{job_id}` 60/min, `POST /refresh_opensanctions` 2/min, `POST /internal/screening/jobs` 120/min, `POST /internal/screening/jobs/bulk` 20/min. Exceeding returns 429. The client IP is taken from the direct connection unless behind a trusted proxy (see below).

**Login backoff** (per account): after repeated failed logins in a 15-minute window, `/auth/login` applies a soft delay (5 fails: 30s, 8 fails: 2m, 10+ fails: 10m) and returns `429` with `Retry-After`.

**Trusted proxy:** When the app is behind a reverse proxy (e.g. Nginx), set `TRUSTED_PROXY_IPS` to the proxy’s IP(s), e.g. `127.0.0.1,::1` or your Nginx host. Only then is `X-Forwarded-For` used for client IP (rate limiting and internal screening allowlist). Otherwise the direct connection IP is used to avoid spoofing. Prefer **INTERNAL_SCREENING_API_KEY** for `/internal/screening/*`; IP allowlist is secondary and only safe when traffic comes via a trusted proxy.

### Environment variables (frontend)

| Variable | Description |
|----------|-------------|
| `VITE_API_BASE_URL` | API base URL when not using the Vite proxy (e.g. production or custom backend). Empty = same origin. |

### Run frontend against the live API

If you only have the frontend locally and want to test against the deployed API (e.g. https://sanctions-check.co.uk):

```bash
cd frontend
npm install
VITE_API_BASE_URL=https://sanctions-check.co.uk npm run dev
```

Then open http://localhost:5173. All API calls go to the live server. The backend CORS config already allows `localhost` and `127.0.0.1`, so this works without changes on the server.

You can also put the URL in a `.env` file in `frontend/` (see `frontend/.env.example`).

## GUI authentication

Authentication for the website uses the same JWT. **POST /opcheck** remains unauthenticated for Dynamics, Power Apps, and scripts. **POST /refresh_opensanctions** requires either admin JWT (e.g. from the website **Admin → Refresh**) or an API key (see `REFRESH_OPENSANCTIONS_API_KEY` below).

When **DATABASE_URL** is set, the website requires sign-in. Users are stored in the database.

- **Production:** Leave **SEED_DEFAULT_ADMIN** unset so no default user is created. Create the first admin via the database (insert into `users`) or a one-off script.
- **Development / first-time setup:** Set `SEED_DEFAULT_ADMIN=true` (or `1`/`yes`) to seed a default admin user:
  - **Email:** `Greg.Burden-Lowe@Legalprotectiongroup.co.uk`
  - **Password:** `Admin`
  - **First logon:** the user must change their password before using the app.

Admins can create more users from **Admin → Users**: set email, initial password, and optionally “Require password change at first logon”. The **Users** link in the sidebar is visible only to admin users.

**Sign up (request access):** Users with an approved company email domain can use **Sign up** on the login page. They enter only their email; a **temporary password is sent by email** (via [Resend](https://resend.com)). They sign in with that password and are then required to set a new password. Set **RESEND_API_KEY** and **RESEND_FROM** (see optional env vars) to enable signup.

If **DATABASE_URL** is not set, the website does not require login (anyone can use the app in the browser).

## Connecting the database

To enable screening persistence (12‑month cache) and the job queue:

1. **Create a PostgreSQL database** (local, managed, or Docker).

2. **Set `DATABASE_URL`** to a valid connection string, for example:
   ```bash
   export DATABASE_URL="postgresql://user:password@localhost:5432/sanctions"
   ```
   Or in a `.env` file in the project root (do not commit secrets):
   ```
   DATABASE_URL=postgresql://user:password@localhost:5432/sanctions
   ```
   The API uses **asyncpg**; the URL format is the same as for `psycopg2` (e.g. `postgresql://...` or `postgres://...`).

3. **Start the API** as usual. On first request (or startup), the API creates the tables (`screened_entities`, `screening_jobs`) if they do not exist. You can also run the schema once by hand:
   ```bash
   psql "$DATABASE_URL" -f schema.sql
   ```

4. **Run the background worker** (separate process) so queued jobs are processed:
   ```bash
   python screening_worker.py
   ```
   The worker needs the same codebase and `DATABASE_URL`; it uses **psycopg2**. Run 1–2 worker instances (e.g. in systemd or Docker).

**Without `DATABASE_URL`:** the API still works: every `/opcheck` runs screening synchronously and returns the result; there is no cache or queue.

Optional env vars (when using the DB):

| Variable | Description |
|----------|-------------|
| `OPCHECK_QUEUE_THRESHOLD` | When pending+running jobs ≥ this, `/opcheck` enqueues instead of running sync (default `5`). |
| `SCREENING_JOBS_RETENTION_DAYS` | Worker deletes completed/failed jobs older than this (default `7`). |
| `SCREENED_ENTITIES_RETENTION_MONTHS` | When set (e.g. `12`), worker deletes `screened_entities` rows older than N months. Unset/`0` = no automatic purge. See `docs/data-retention.md`. |
| `SCREENING_CLEANUP_EVERY_N_LOOPS` | Worker runs cleanup every N loops (default `50`). |
| `SCREENING_WORKER_POLL_SECONDS` | Worker sleep when no job is available (default `5`). |
| `RESEND_API_KEY` | API key for [Resend](https://resend.com) to email temporary passwords on signup. If unset, the signup form returns 503. |
| `RESEND_FROM` | Sender for signup emails, e.g. `Sanctions Screening <noreply@yourdomain.com>`. Defaults to Resend onboarding address. |

For the internal bulk API, see `docs/INTERNAL_SCREENING_API.md` (API key and/or IP allowlist required).

```
VITE_API_BASE_URL=https://sanctions-check.co.uk
```

## Production build

1. Build the frontend:

   ```bash
   cd frontend
   npm ci
   npm run build
   ```

   Output is in `frontend/dist`.

2. Run the backend; it will serve the built frontend from `frontend/dist` if present:

   ```bash
   uvicorn api_server:app --host 0.0.0.0 --port 8000
   ```

   - API routes (`/health`, `/opcheck`, `/refresh_opensanctions`) take precedence.  
   - All other requests are served from `frontend/dist` (e.g. `/` → `index.html`).  
   - If `frontend/dist` does not exist, the backend still runs as API-only.

## API contract (frozen)

- **GET /health** — Returns plain text `ok`.
- **POST /opcheck** — Body: `{ name, dob?, entity_type?, requestor? }`. Validates `name` and `requestor`; returns result with keys: `Match Found`, `Risk Level`, `Confidence`, `Score`, `Top Matches`, `Check Summary`, `Is Sanctioned`, `Is PEP`, etc. Do not rename or restructure these fields.
- **POST /refresh_opensanctions** — Body: `{ include_peps?: boolean, sync_postgres?: boolean }` (`sync_postgres` defaults to `true`). Returns `{ status: "ok", include_peps, postgres_synced, postgres_rows }` or 500 with `{ status: "error", message }`.

## Docker (recommended for production / DigitalOcean)

The repo is fully containerized: one image runs the FastAPI backend and serves the built frontend.

### Build and run locally

```bash
# Build the image (builds frontend then backend)
docker build -t sanctions-api .

# Run with persistent data (parquet + search log survive restarts)
docker run -p 8000:8000 -v sanctions-data:/app/data sanctions-api
```

Or with Docker Compose (same thing, with a named volume):

```bash
docker compose up -d
# App: http://localhost:8000
```

After first deploy, load sanctions/PEP data with:

```bash
curl -X POST https://your-domain/refresh_opensanctions \
  -H "Content-Type: application/json" \
  -H "X-Refresh-Opensanctions-Key: $REFRESH_OPENSANCTIONS_API_KEY" \
  -d '{"include_peps":true}'
```

This refresh now also rebuilds `watchlist_entities` in PostgreSQL by default.

### Deploy on DigitalOcean

You need a **PostgreSQL database** (for screening cache, job queue, and GUI users). Use a [DigitalOcean Managed Database](https://docs.digitalocean.com/products/databases/postgresql/) or any Postgres host, then set `DATABASE_URL` when deploying.

**Option A: App Platform (easiest)**

1. In [DigitalOcean Control Panel](https://cloud.digitalocean.com/) go to **Apps** → **Create App** → connect your GitHub repo.
2. Choose **Web Service** and set the **Source** to use the **Dockerfile** (not a buildpack).
3. Set **HTTP Port** to `8000`.
4. **Environment variables** (App → Settings → App-Level Environment Variables):
   - **DATABASE_URL** — PostgreSQL connection string (e.g. from a DO Managed Database; use the “Connection string” or `postgresql://user:password@host:port/database?sslmode=require`).
   - **GUI_JWT_SECRET** — A long random secret for signing login tokens (e.g. `openssl rand -hex 32`). Required in production.
5. Deploy. After the first deploy:
   - Open the app URL → sign in with the default user (see [GUI authentication](#gui-authentication)) and change the password if prompted.
   - Trigger `POST /refresh_opensanctions` once so screening tables are populated (this can take a few minutes).
6. (Optional) Add your domain under **Settings** → **Domains** and point DNS to the app.

**Option B: Droplet (or any VM)**

1. Create a Droplet (e.g. Ubuntu) and ensure you have a PostgreSQL database (managed DB or install Postgres on the Droplet / another server).
2. On the Droplet, install Docker: `curl -fsSL https://get.docker.com | sh`
3. Clone the repo and build:
   ```bash
   git clone https://github.com/GregBurden-Lowe/SanctionsAPI.git
   cd SanctionsAPI
   docker build -t sanctions-api .
   ```
4. Run the container with a volume and env vars:
   ```bash
   docker run -d -p 8000:8000 \
     -v sanctions-data:/app/data \
     -e DATABASE_URL="postgresql://user:password@host:5432/dbname?sslmode=require" \
     -e GUI_JWT_SECRET="your-long-random-secret" \
     --restart unless-stopped \
     sanctions-api
   ```
5. Put Nginx (or Caddy) in front for SSL and proxy to `http://127.0.0.1:8000`. Example Nginx server block:
   ```nginx
   server {
     listen 80;
     server_name your-domain.com;
     location / {
       proxy_pass http://127.0.0.1:8000;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
     }
   }
   ```
   Then enable SSL (e.g. `certbot --nginx`) and reload Nginx.
   For internet-facing hardening (bot path blocking, edge rate limits, real IP headers), see `/docs/NGINX_HARDENING.md`.
6. After first boot: open the app, sign in (default user), change password if required, then call `POST /refresh_opensanctions` once.

The container listens on `0.0.0.0` and uses the `PORT` env (default 8000), so it works with platform-assigned ports on App Platform.

### Replacing an existing DigitalOcean deployment

If you already have the API running (e.g. NSSM on a Droplet, or an older App Platform app) and want to switch to the Dockerised stack:

**1. Back up before changing anything**

- Note **environment variables** (e.g. `DATABASE_URL`) from the current app or server.
- Note how your **domain** is pointed (e.g. sanctions-check.co.uk → App Platform or Droplet IP).
- If you have **parquet data** on the server you care about, copy the `data/` folder (or at least `opensanctions.parquet`) off the server; the new container can start fresh and you’ll run a data refresh, or you can mount that data into the container later.

**2. Tear down or repurpose the old setup**

- **App Platform:** In the DO control panel, delete the old app (or the service that runs the API). Your repo and any new app you create are separate.
- **Droplet (current API + Nginx):** Either:
  - **Option A — Replace in place:** Stop the current API (e.g. stop the NSSM service or the process on port 4512). You’ll reuse the same Droplet and Nginx; Nginx will keep pointing at a port you’ll use for Docker (e.g. 8000).
  - **Option B — Fresh Droplet:** Create a new Droplet, set up Docker and Nginx there, then point the domain at the new Droplet and decommission the old one when ready.

**3. Deploy the Dockerised app**

- **App Platform:** Create a new **Web Service**, connect this repo, set **Source** to the Dockerfile. Set **HTTP Port** to `8000`. Add the env vars you backed up. Deploy. Then point your domain (e.g. sanctions-check.co.uk) at the new app’s URL in DO, or use DO’s “Add Domain” for the app.
- **Droplet (same or new):**  
  - Install Docker (and Docker Compose if you prefer): `curl -fsSL https://get.docker.com | sh`.  
  - Clone this repo: `git clone https://github.com/GregBurden-Lowe/SanctionsAPI.git && cd SanctionsAPI`.  
  - Build and run:
    ```bash
    docker build -t sanctions-api .
    docker run -d -p 8000:8000 \
      -v sanctions-data:/app/data \
      -e DATABASE_URL="postgresql://..." \
      --restart unless-stopped \
      sanctions-api
    ```
  - Configure Nginx to proxy to `http://127.0.0.1:8000` (same as before, but port 8000 instead of 4512). Reload Nginx.  
  - If the domain was already pointing at this Droplet, SSL and DNS stay the same.

**4. After first boot**

- Open the app (via your domain or the app URL). Sign in with the default user (see [GUI authentication](#gui-authentication)); change the password when prompted.
- Call `POST /refresh_opensanctions` (with `{"include_peps": true}`) so screening has data. This can take a few minutes.

### Nightly refresh cron (Droplet)

Run this on the droplet host to refresh at 22:00 daily:

```bash
crontab -e
```

Add:

```cron
0 22 * * * /usr/bin/curl -sS -X POST https://sanctions-check.co.uk/refresh_opensanctions -H 'Content-Type: application/json' -H 'X-Refresh-Opensanctions-Key: YOUR_REFRESH_KEY' -d '{"include_peps":true}' >> /var/log/sanctions-refresh.log 2>&1
```

Tip: keep `REFRESH_OPENSANCTIONS_API_KEY` in your app environment and use that same value in the cron header.

**5. Sanity check**

- Visit `https://your-domain/health` → should return `ok`.
- Run a screening from the UI to confirm the API responds correctly.

## API contract safety tests

Lightweight checks to prevent API drift:

```bash
python3 tests/test_api_contract.py
```
(Use `python` if your environment provides it.)

Requires the server to be running (e.g. `uvicorn api_server:app --port 8000`). Tests verify:

- `/health` returns `ok`
- `/opcheck` rejects missing `requestor` with the expected message
- `/opcheck` rejects missing `name` with the expected message
- `/opcheck` returns the expected response keys for valid input

## Design system

UI tokens and component recipes are defined in `design.json`. The frontend (Tailwind + React) must use only those tokens and classes; do not add new colors, spacing, or variants without updating the design file.
