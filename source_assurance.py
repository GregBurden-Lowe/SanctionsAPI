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
    'EU': ('eu council', 'eu financial sanctions', 'european union'),
    'OFAC': ('ofac',),
}


def decode(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else dict(value or {})


def build_manifest(sanctions_path, peps_path=None):
    from utils import _dataset_allowed
    counts = {name: 0 for name in LIST_LABELS}
    counts['PEP'] = 0
    hashes = {}
    datasets = set()
    for kind, path in [('sanctions', sanctions_path), ('peps', peps_path)]:
        if not path:
            continue
        with open(path, 'rb') as stream:
            hashes[kind] = hashlib.file_digest(stream, 'sha256').hexdigest()
        with open(path, encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                label = row.get('dataset', '')
                if not row.get('name') or (kind == 'sanctions' and not _dataset_allowed(label)):
                    continue
                datasets.add(label)
                if kind == 'peps':
                    counts['PEP'] += 1
                else:
                    for name, labels in LIST_LABELS.items():
                        if any(part in label.lower() for part in labels):
                            counts[name] += 1
    version = hashlib.sha256(json.dumps({'files': hashes, 'lists': LIST_LABELS, 'format': 2}, sort_keys=True).encode()).hexdigest()
    return {'version': version, 'ingested_at': datetime.now(timezone.utc).isoformat(),
            'source_published_at': None, 'publication_time_verified': False,
            'file_hashes': hashes, 'list_counts': counts, 'datasets': sorted(datasets),
            'scope': 'Configured sanctions lists and PEP collection; not identity verification or complete ownership research.'}


def assess(manifest, include_peps=True, now=None):
    manifest = decode(manifest)
    required = ['UK', 'UN', 'EU', 'OFAC'] + (['PEP'] if include_peps else [])
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


def require_current(manifest, include_peps=True):
    from utils import ScreeningUnavailableError
    coverage = assess(manifest, include_peps)
    if coverage['status'] != 'current':
        raise ScreeningUnavailableError('Source coverage is ' + coverage['status'] +
            (': ' + ', '.join(coverage['missing_lists']) if coverage['missing_lists'] else '') +
            '. Refresh and verify the watchlist before screening. No clearance issued.')
    return coverage


async def postgres_manifest(conn):
    if not await conn.fetchval("SELECT to_regclass('public.watchlist_source_state') IS NOT NULL"):
        return {}
    return decode(await conn.fetchval('SELECT manifest FROM watchlist_source_state WHERE singleton'))


def parquet_manifest(path=None):
    import pyarrow.parquet as pq
    from utils import OSN_PARQUET
    try:
        return decode((pq.read_schema(path or OSN_PARQUET).metadata or {}).get(METADATA_KEY))
    except (FileNotFoundError, OSError, ValueError):
        return {}
