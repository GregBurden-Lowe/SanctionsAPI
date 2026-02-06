# Sanctions & PEP Screening API

FastAPI backend for OpenSanctions/PEP screening with an optional Vite + React frontend. The API contract is stable for integration with Dynamics, Power Apps, and other internal systems.

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
| `POWER_AUTOMATE_FLOW_URL` | Power Automate HTTP trigger URL for audit payloads. If unset, audit is skipped. |
| `POWER_AUTOMATE_SYNC_TEST` | Set to `1` to send audit synchronously (for debugging). Default is async. |

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

You can also put the URL in a `.env` file in `frontend/` (see `frontend/.env.example`):

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
- **POST /refresh_opensanctions** — Body: `{ include_peps: boolean }`. Returns `{ status: "ok", include_peps }` or 500 with `{ status: "error", message }`.

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

After first deploy, **load sanctions data** via the app: open the app → **Admin** → **Refresh OpenSanctions data** (or `POST /refresh_opensanctions` with `{"include_peps": true}`). The data is stored in the container volume.

### Deploy on DigitalOcean

**Option A: App Platform (container)**

1. Connect the repo; choose **Web Service** and **Dockerfile** as the build method.
2. Set **HTTP Port** to `8000` (or the port your Dockerfile `EXPOSE`s).
3. Add env vars if needed: `POWER_AUTOMATE_FLOW_URL`, `POWER_AUTOMATE_SYNC_TEST`.
4. Deploy. After first boot, run a data refresh (Admin page or API) so screening works.

**Option B: Droplet (or any VM)**

1. On the server: clone the repo, then `docker build -t sanctions-api .`
2. Run with a volume and optional env:
   ```bash
   docker run -d -p 8000:8000 \
     -v sanctions-data:/app/data \
     -e POWER_AUTOMATE_FLOW_URL="https://..." \
     --restart unless-stopped \
     sanctions-api
   ```
3. Put Nginx (or another reverse proxy) in front for SSL and proxy to `localhost:8000`.
4. Run a refresh (Admin or `POST /refresh_opensanctions`) once to populate data.

The container listens on `0.0.0.0` and uses the `PORT` env (default 8000) so it works with platform-assigned ports.

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
