"""Compare name-only matchers on a labelled JSON corpus; never used for screening.

The comparison uses a fixed Jaro-Winkler full-string baseline, not OFAC's
complete algorithm. Labels must be independently established for acceptance.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rapidfuzz.distance import JaroWinkler
from utils import get_best_name_matches, _normalize_text


def compare(cases, threshold):
    counts = {'current': Counter(), 'jaro_winkler': Counter()}
    differences = []
    evaluated = 0
    for case in cases:
        if case.get('type', 'Person') != 'Person':
            continue
        if not isinstance(case.get('expected'), bool):
            raise ValueError('Each case must include a boolean expected value')
        expected = case['expected']
        query, candidate = case['query'], case['candidate']
        current = bool(get_best_name_matches(query, [candidate], threshold=75))
        jw_score = JaroWinkler.normalized_similarity(_normalize_text(query), _normalize_text(candidate))
        jw = jw_score >= threshold
        evaluated += 1
        for key, prediction in [('current', current), ('jaro_winkler', jw)]:
            result = ('true_positive' if expected else 'false_positive') if prediction else ('false_negative' if expected else 'true_negative')
            counts[key][result] += 1
        if current != expected or jw != expected or current != jw:
            differences.append({'label': case['label'], 'expected': expected, 'current': current,
                                'jaro_winkler': jw, 'jaro_winkler_score': round(jw_score, 4)})
    return {'cases': evaluated, 'threshold_jaro_winkler': threshold, 'counts': {k: dict(v) for k, v in counts.items()},
            'differences': differences,
            'limitations': ['Name-only synthetic benchmark unless independently reviewed labels are supplied.',
                            'Full-string Jaro-Winkler is not OFAC or OpenSanctions scoring.',
                            'Does not test retrieval, aliases, identifiers, source coverage or production accuracy.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('corpus', type=Path)
    parser.add_argument('--jaro-threshold', type=float, default=0.85)
    args = parser.parse_args()
    if not 0 <= args.jaro_threshold <= 1:
        parser.error('threshold must be between 0 and 1')
    data = args.corpus.read_bytes()
    result = compare(json.loads(data), args.jaro_threshold)
    result['corpus_sha256'] = hashlib.sha256(data).hexdigest()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
