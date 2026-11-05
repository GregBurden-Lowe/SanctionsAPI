"""Token-based name assessment for the v2 matcher (PostgreSQL path only).

Names are compared word by word rather than as whole strings, so spelling
variation in several words and an omitted patronymic can still align, and words
that are common on the watchlists count for less than distinctive ones. Values
here are operational heuristics chosen on constructed cases, not calibrated
probabilities of identity. See docs/MATCHING_V2.md.
"""
from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Set, Tuple

from rapidfuzz.distance import JaroWinkler

MATCHER_RULES_VERSION = "evidence-v9-matching-v2"

PERSON_TOKEN_SIMILARITY = 0.86     # two words of 4+ letters align at or above this
ORG_TOKEN_SIMILARITY = 0.90        # stricter: company words are less variable
PERSON_ACCEPT_COVERAGE = 0.80      # weighted share of the query explained by the candidate
UNEXPLAINED_SHARE_LIMIT = 0.40     # reject when this much of the query has no near match
UNEXPLAINED_TOKEN_SIMILARITY = 0.75
EXTRA_TOKEN_PENALTY = 0.03         # per listed word absent from the query
MAX_EXTRA_TOKEN_PENALTY = 0.08     # keeps omitted patronymics from sinking a match
RARE_TOKEN_DOC_FREQUENCY = 30
RETRIEVAL_TOKEN_SIMILARITY = 0.80  # looser than scoring: retrieval must favour recall
RETRIEVAL_MIN_FUZZY_LENGTH = 4
RETRIEVAL_LENGTH_TOLERANCE = 2
MIN_TOKEN_WEIGHT = 0.2
ALIGNED_ORG_SCORE = 90
MAX_ALIGNED_PERSON_SCORE = 89      # an aligned variation is never shown as a near-exact name

_SEPARATORS = r"[-‐‑‒–—;/]"
PERSON_PARTICLES = frozenset(
    "al el bin ibn binti da de del della der di la le st van von abu abd".split()
)
ORG_LEGAL_TOKENS = frozenset(
    "limited liability company joint stock open closed public private ltd llc llp plc inc "
    "incorporated corp corporation gmbh ooo oao zao pao pjsc jsc ojsc cjsc sa ag bv co sarl "
    "pte pty kg spa srl fze fzco the of and".split()
)
ORG_GENERIC_TOKENS = frozenset(
    "group holding holdings international global services service trading trade industries "
    "industrial technologies technology logistics investments investment shipping energy "
    "consulting national general united first royal northern eastern premier enterprise "
    "enterprises management partners".split()
)


def normalize_text(value) -> str:
    """Same rules as utils._normalize_text; duplicated to avoid an import cycle."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("utf-8")
    value = re.sub(r"[^\w\s]", "", value)
    return re.sub(r"\s+", " ", value).lower().strip()


def tokens(name) -> List[str]:
    return normalize_text(re.sub(_SEPARATORS, " ", str(name))).split()


def organisation_core(name) -> List[str]:
    """Words left once legal forms are removed; all words if nothing else remains."""
    words = tokens(name)
    return [word for word in words if word not in ORG_LEGAL_TOKENS] or words


def name_tokens(names: Iterable[str]) -> Set[str]:
    found: Set[str] = set()
    for name in names:
        found.update(tokens(name))
    return found


@dataclass(frozen=True)
class TokenStats:
    """How often query words appear across listed entities."""
    doc_frequency: Dict[str, int]
    total_entities: int

    def frequency(self, token: str) -> int:
        return self.doc_frequency.get(token, 0)

    def weight(self, token: str) -> float:
        total = max(2, self.total_entities)
        return max(MIN_TOKEN_WEIGHT, math.log(total / (1 + self.frequency(token))) / math.log(total))


def _align(query: List[str], candidate: List[str], cutoff: float) -> Dict[int, Tuple[int, float]]:
    """One-to-one word alignment, strongest pairs first: {query index: (candidate index, similarity)}."""
    pairs = sorted(
        ((JaroWinkler.similarity(left, right), i, j)
         for i, left in enumerate(query) for j, right in enumerate(candidate)),
        reverse=True,
    )
    used_query, used_candidate, aligned = set(), set(), {}
    for similarity, i, j in pairs:
        if i in used_query or j in used_candidate:
            continue
        left, right = query[i], candidate[j]
        initial = min(len(left), len(right)) == 1 and left[0] == right[0]
        if left == right or initial or (min(len(left), len(right)) >= 4 and similarity >= cutoff):
            used_query.add(i)
            used_candidate.add(j)
            aligned[i] = (j, 1.0 if left == right else max(similarity, 0.85) if initial else similarity)
    return aligned


@dataclass(frozen=True)
class PersonAssessment:
    coverage: float
    aligned_informative: int
    required_informative: int
    unexplained_share: float


def assess_person(query_name, candidate_name, stats: TokenStats) -> Optional[PersonAssessment]:
    query, candidate = tokens(query_name), tokens(candidate_name)
    if not query or not candidate:
        return None
    aligned = _align(query, candidate, PERSON_TOKEN_SIMILARITY)
    total_weight = sum(stats.weight(token) for token in query)
    coverage = sum(stats.weight(query[i]) * similarity for i, (_, similarity) in aligned.items()) / total_weight
    coverage -= min(MAX_EXTRA_TOKEN_PENALTY, EXTRA_TOKEN_PENALTY * max(0, len(candidate) - len(aligned)))
    unexplained = sum(
        stats.weight(token) for i, token in enumerate(query)
        if i not in aligned and len(token) > 1
        and max(JaroWinkler.similarity(token, other) for other in candidate) < UNEXPLAINED_TOKEN_SIMILARITY
    ) / total_weight
    informative = [token for token in query if token not in PERSON_PARTICLES]
    return PersonAssessment(
        coverage=coverage,
        aligned_informative=sum(query[i] not in PERSON_PARTICLES for i in aligned),
        required_informative=min(2, len(informative)),
        unexplained_share=unexplained,
    )


@dataclass(frozen=True)
class OrganisationAssessment:
    distinctive_words_agree: bool
    rare_word_aligned: bool


def assess_organisation(query_name, candidate_name, stats: TokenStats) -> Optional[OrganisationAssessment]:
    query, candidate = organisation_core(query_name), organisation_core(candidate_name)
    if not query or not candidate:
        return None
    aligned = _align(query, candidate, ORG_TOKEN_SIMILARITY)
    used = {j for j, _ in aligned.values()}
    query_distinctive = [i for i, token in enumerate(query) if token not in ORG_GENERIC_TOKENS] or range(len(query))
    candidate_distinctive = [j for j, token in enumerate(candidate) if token not in ORG_GENERIC_TOKENS] or range(len(candidate))
    return OrganisationAssessment(
        distinctive_words_agree=all(i in aligned for i in query_distinctive) and all(j in used for j in candidate_distinctive),
        rare_word_aligned=any(stats.frequency(query[i]) <= RARE_TOKEN_DOC_FREQUENCY for i in aligned),
    )


def refine_hits(query_name, names: List[str], standard_hits, *, has_dob: bool, entity_type: str,
                stats: TokenStats, remove_disagreeing: bool = False) -> Tuple[list, Set[int]]:
    """Add names the standard comparison rejected but token alignment accepts.

    Standard hits are always kept. remove_disagreeing also drops hits whose
    distinctive words disagree, but it stays off: on real subjects it removed
    alerts sharing rare listed words, and no labelled evidence supports that yet.
    Returns the hits and the indexes added by alignment.
    """
    organisation = (entity_type or "Person").strip().lower() == "organization"
    assess = assess_organisation if organisation else assess_person
    kept = []
    for hit in standard_hits:
        assessment = assess(query_name, names[hit[2]], stats)
        if remove_disagreeing and not has_dob and assessment and (
            (not assessment.distinctive_words_agree and not assessment.rare_word_aligned) if organisation
            else assessment.unexplained_share >= UNEXPLAINED_SHARE_LIMIT
        ):
            continue
        kept.append(hit)
    known = {hit[2] for hit in standard_hits}
    added: Set[int] = set()
    for index, candidate in enumerate(names):
        if index in known:
            continue
        assessment = assess(query_name, candidate, stats)
        if not assessment:
            continue
        if organisation and assessment.distinctive_words_agree:
            kept.append((normalize_text(candidate), ALIGNED_ORG_SCORE, index))
            added.add(index)
        elif (not organisation and assessment.required_informative >= 2
              and assessment.aligned_informative >= assessment.required_informative
              and assessment.coverage >= PERSON_ACCEPT_COVERAGE):
            kept.append((normalize_text(candidate), min(MAX_ALIGNED_PERSON_SCORE, int(assessment.coverage * 100)), index))
            added.add(index)
    return sorted(kept, key=lambda hit: hit[1], reverse=True), added


@dataclass(frozen=True)
class RetrievalPlan:
    query_tokens: List[str]
    informative: List[int]
    required_matches: int


def retrieval_plan(name, entity_type) -> RetrievalPlan:
    organisation = (entity_type or "Person").strip().lower() == "organization"
    query = list(dict.fromkeys(organisation_core(name) if organisation else tokens(name)))
    informative = [
        i for i, token in enumerate(query)
        if token not in PERSON_PARTICLES and len(token) > 1 and (not organisation or token not in ORG_GENERIC_TOKENS)
    ] or list(range(len(query)))
    return RetrievalPlan(query, informative, min(2, len(informative)))


def similar_tokens(query_token: str, known_tokens: Iterable[str]) -> List[str]:
    """Filter index tokens down to those close enough to count for retrieval."""
    if len(query_token) < RETRIEVAL_MIN_FUZZY_LENGTH:
        return [token for token in known_tokens if token == query_token]
    return [token for token in known_tokens
            if token == query_token or JaroWinkler.similarity(query_token, token) >= RETRIEVAL_TOKEN_SIMILARITY]
