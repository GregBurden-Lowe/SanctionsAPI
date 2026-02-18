# Tests

API contract tests to guard against accidental changes to the public API.

- **[test_api_contract.py](test_api_contract.py)** — Checks `/health`, `/opcheck` validation (requestor, name), and response shape for a valid screening.

Run with the server up (e.g. `uvicorn api_server:app --port 8000`):

```bash
python tests/test_api_contract.py
```

Or from repo root: `python -m pytest tests/` (if pytest is installed).
