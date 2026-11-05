"""Versioned source manifests and explicit coverage checks (no inferred publication dates)."""
import csv
import hashlib
import json
import os
from datetime import datetime, timezone

METADATA_KEY = b'screening.source_manifest.v1'
LIST_LABELS = {
    'UK': ('hm treasury', 'hmt', 'ofsi', 'uk financial', 'uk fcdo', 'uk sanctions list'),
    'UN': ('united nations', 'un security council'),
    'EU': ('eu council', 'eu financial sanctions', 'european union', 'eu consolidated'),
    'OFAC': ('ofac',),
}


def decode(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else dict(value or {})


def build_manifest(sanctions_path):
    from utils import _dataset_allowed
    counts = {name: 0 for name in LIST_LABELS}
    hashes = {}
    datasets = set()
    with open(sanctions_path, 'rb') as stream:
        hashes['sanctions'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    with open(sanctions_path, encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            label = row.get('dataset', '')
            if not row.get('name') or not _dataset_allowed(label):
                continue
            datasets.add(label)
            for name, labels in LIST_LABELS.items():
                if any(part in label.lower() for part in labels):
                    counts[name] += 1
    version = hashlib.sha256(json.dumps({'files': hashes, 'lists': LIST_LABELS, 'format': 2}, sort_keys=True).encode()).hexdigest()
    return {'version': version, 'ingested_at': datetime.now(timezone.utc).isoformat(),
            'source_published_at': None, 'publication_time_verified': False,
            'file_hashes': hashes, 'list_counts': counts, 'datasets': sorted(datasets),
            'scope': 'Configured sanctions lists; not identity verification or complete ownership research.'}


def assess(manifest, now=None):
    manifest = decode(manifest)
    required = ['UK', 'UN', 'EU', 'OFAC']
    missing = [name for name in required if not manifest.get('list_counts', {}).get(name)]
    now = now or datetime.now(timezone.utc)
    try:
        ingested = datetime.fromisoformat(manifest['ingested_at'])
        age = (now - ingested).total_seconds() / 3600
    except (KeyError, ValueError, TypeError):
        age = None
    max_age = max(1, float(os.getenv('SCREENING_MAX_SOURCE_AGE_HOURS', '48')))
    status = 'unverified' if not manifest.get('version') or age is None else 'missing_sources' if missing else 'stale' if age < -0.1 or age > max_age else 'current'
    return {**manifest, 'status': status, 'required_lists': required, 'missing_lists': missing,
            'age_hours': round(age, 2) if age is not None else None, 'maximum_age_hours': max_age}


def require_current(manifest):
    from utils import ScreeningUnavailableError
    coverage = assess(manifest)
    if coverage['status'] != 'current':
        raise ScreeningUnavailableError('Source coverage is ' + coverage['status'] +
            (': ' + ', '.join(coverage['missing_lists']) if coverage['missing_lists'] else '') +
            '. Refresh and verify the watchlist before screening. No clearance issued.')
    return coverage


async def loaded_list_mismatches(conn, manifest):
    """Lists whose PostgreSQL row count differs from the published manifest."""
    expected = decode(manifest).get('list_counts', {})
    loaded = {name: 0 for name in LIST_LABELS}
    for row in await conn.fetch("""SELECT dataset, count(*) AS rows FROM watchlist_entities
                                   WHERE source_type='sanctions' GROUP BY dataset"""):
        label = str(row['dataset'] or '').lower()
        for name, patterns in LIST_LABELS.items():
            if any(part in label for part in patterns):
                loaded[name] += row['rows']
                break
    return {name: {'expected': expected[name], 'loaded': loaded[name]}
            for name in LIST_LABELS if name in expected and expected[name] != loaded[name]}


async def postgres_manifest(conn):
    if not await conn.fetchval("SELECT to_regclass('public.watchlist_source_state') IS NOT NULL"):
        return {}
    return decode(await conn.fetchval('SELECT manifest FROM watchlist_source_state WHERE singleton'))


def parquet_manifest(path=None):
    import pyarrow.parquet as pq
    from utils import OWN_PARQUET
    try:
        return decode((pq.read_schema(path or OWN_PARQUET).metadata or {}).get(METADATA_KEY))
    except (FileNotFoundError, OSError, ValueError):
        return {}


def build_own_manifest(watchlists_parquet_path: str) -> dict:
    """Build a source manifest from our self-scraped watchlists parquet file."""
    import hashlib as _hl
    import pandas as pd

    try:
        df = pd.read_parquet(watchlists_parquet_path, columns=["dataset", "source_type", "name"])
    except Exception:
        df = pd.DataFrame(columns=["dataset", "source_type", "name"])

    df = df.fillna("")
    df = df[df["name"].str.strip() != ""]
    df = df[df["source_type"] != "peps"]

    counts = {name: 0 for name in LIST_LABELS}
    datasets: set = set()

    for label, grp in df.groupby("dataset", sort=False):
        label = str(label)
        datasets.add(label)
        for list_name, label_patterns in LIST_LABELS.items():
            if any(part in label.lower() for part in label_patterns):
                counts[list_name] += len(grp)
                break

    file_hash = ""
    try:
        hasher = _hl.sha256()
        with open(watchlists_parquet_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()
    except OSError:
        pass

    hashes = {"all": file_hash}
    # Counts alone do not identify a snapshot: an alias can change without
    # changing any source's row count. Cache reuse and archived snapshots must
    # follow the actual staged file content.
    version = _hl.sha256(
        json.dumps({"list_counts": counts, "datasets": sorted(datasets),
                    "file_hashes": hashes, "format": 4}, sort_keys=True).encode()
    ).hexdigest()

    return {
        "version": version,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "source_published_at": None,
        "publication_time_verified": False,
        "file_hashes": hashes,
        "list_counts": counts,
        "datasets": sorted(datasets),
        "scope": "Self-scraped sanctions lists (UN, OFAC, UK, EU).",
    }
