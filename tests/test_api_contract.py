"""
Lightweight API contract tests to prevent drift. Run against a live server:
  uvicorn api_server:app --port 8000
  python tests/test_api_contract.py
"""
import os
import sys
import urllib.request
import urllib.error
import json

BASE = os.environ.get("SANCTIONS_API_BASE", "http://127.0.0.1:8000")

# Expected response keys from POST /opcheck (frozen contract)
OPCHECK_KEYS = frozenset({
    "Match Found", "Risk Level", "Confidence", "Score", "Top Matches",
    "Check Summary", "Is Sanctioned", "Is PEP", "Sanctions Name", "Birth Date",
    "Regime", "Position", "Topics",
})


def get_health():
    req = urllib.request.Request(f"{BASE}/health", method="GET")
    with urllib.request.urlopen(req) as r:
        return r.read().decode().strip()


def post_json(path, data):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"message": body}


def main():
    errors = []

    # GET /health returns "ok"
    try:
        text = get_health()
        if text != "ok":
            errors.append(f"GET /health: expected 'ok', got {text!r}")
    except Exception as e:
        errors.append(f"GET /health: {e}")
        # If server is down, skip the rest
        if errors:
            print("API contract checks failed (is the server running on {}?).".format(BASE))
            for e in errors:
                print("  -", e)
            sys.exit(1)

    # POST /opcheck rejects missing requestor
    code, body = post_json("/opcheck", {"name": "Test", "entity_type": "Person"})
    if code != 400:
        errors.append(f"POST /opcheck (no requestor): expected 400, got {code}")
    else:
        msg = (body.get("message") or "").lower()
        if "requestor" not in msg:
            errors.append(f"POST /opcheck (no requestor): expected message mentioning requestor, got {body!r}")

    # POST /opcheck rejects missing name (400 from handler or 422 from Pydantic)
    code, body = post_json("/opcheck", {"requestor": "Tester", "entity_type": "Person"})
    if code not in (400, 422):
        errors.append(f"POST /opcheck (no name): expected 400 or 422, got {code}")
    else:
        msg = (body.get("message") or str(body.get("detail", [])).lower() or "").lower()
        if "name" not in msg:
            errors.append(f"POST /opcheck (no name): expected response mentioning name, got {body!r}")

    # POST /opcheck with valid input returns expected keys
    code, body = post_json("/opcheck", {
        "name": "NonExistentPersonXYZ123",
        "requestor": "ContractTest",
        "entity_type": "Person",
    })
    if code != 200:
        errors.append(f"POST /opcheck (valid): expected 200, got {code} body={body}")
    else:
        missing = OPCHECK_KEYS - set(body.keys())
        if missing:
            errors.append(f"POST /opcheck (valid): missing response keys: {sorted(missing)}")
        if "Check Summary" in body:
            cs = body["Check Summary"]
            if not isinstance(cs, dict) or "Status" not in cs or "Source" not in cs or "Date" not in cs:
                errors.append(f"POST /opcheck (valid): Check Summary must have Status, Source, Date; got {cs!r}")

    if errors:
        print("API contract checks failed:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("API contract checks passed.")


if __name__ == "__main__":
    main()
