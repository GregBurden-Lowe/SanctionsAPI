# Sanctions Screening — Changelog (Management Brief)

High-level changes from the original app to the current version. This is a feature overview, not a technical specification.

---

## Data & screening: from Parquet to PostgreSQL

- **Previously:** Screening used local Parquet files. Each check ran in-process with no shared cache or job queue.
- **Now:** Screening is backed by **PostgreSQL**. Watchlist data is loaded into the database and queries run against it, which improves performance and scales better under load.
- **Benefits:** Screening results can be cached (e.g. 12‑month retention), so repeat checks are faster. Under high load, checks can be queued and processed by a background worker instead of blocking the API. Data refresh (e.g. loading the latest sanctions/PEP lists) updates the Postgres watchlist in one place.

---

## Login and access control

- **When the database is connected,** the website **requires sign-in**. User accounts are stored in PostgreSQL (no more anonymous access to the web app).
- **How login works:** Users sign in with email and password. The server issues a **session token** (JWT). The browser keeps this token and sends it with each request; the server validates it before allowing access to screening, dashboard, and admin features.
- **Session expiry:** Tokens expire after a set time. If a user’s token has expired, the next API call is rejected and the app **automatically logs them out and shows the login screen** — they can’t stay on protected pages with an expired session.
- **Optional sign-up:** If enabled, new users can request access by entering their work email; a temporary password is sent by email. They must set a new password on first sign-in.
- **Roles:** **Admins** can manage users, create API keys, trigger data refresh, and use admin-only screens. **Standard users** can run screenings, use the dashboard, and (if permitted) the match-review queue.

---

## Other highlights

- **API keys:** Admins can create screening API keys so external systems (e.g. Microsoft Dataverse, Power Apps, scripts) can call the screening API without using a user password. Keys are managed under Admin → API keys.
- **Dataverse / Power Apps:** A web resource is available for embedding the compliance check flow inside Dynamics/Dataverse or Power Apps, using an API key configured once by the deployer.
- **Security:** Rate limiting and login backoff help protect against abuse. Only key hashes are stored; expired or invalid tokens trigger an immediate logout and redirect to login.

---

*For technical details, see the main [README](../README.md) and the docs in this folder.*
