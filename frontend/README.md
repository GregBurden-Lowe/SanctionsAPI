# Frontend

Vite + React app for sanctions/PEP screening and admin (data refresh). Design tokens live in the repo root as `design.json`.

**Commands** (see project root [README](../README.md) for full instructions):

- `npm install` then `npm run dev` — dev server with API proxy (http://localhost:5173)
- `npm run build` — production build to `dist/` (served by the backend when present)

Environment: optional `VITE_API_BASE_URL` when not using the Vite proxy (e.g. to point at a deployed API).
