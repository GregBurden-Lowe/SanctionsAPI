"""Name coverage rules validated against the offline shadow comparison.

Scores are uncalibrated heuristics; neither a score nor a removed candidate
is an identity determination.
"""
from collections import Counter
from rapidfuzz.distance import DamerauLevenshtein, Indel, JaroWinkler, Levenshtein
from name_matching import (ORG_GENERIC_TOKENS, ORG_LEGAL_TOKENS, PERSON_PARTICLES,
                           tokens)

HONORIFICS = frozenset('mr mrs ms miss dr prof sir dame sheikh sheik'.split())


def adjacent_transposition(left, right):
    if len(left) != len(right) or len(left) < 5:
        return False
    changed = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
    return (len(changed) == 2 and changed[1] == changed[0] + 1
            and left[changed[0]] == right[changed[1]]
            and left[changed[1]] == right[changed[0]])


def words(name, organisation=False):
    parts = tokens(name)
    if organisation:
        # A one-character typo in a long legal-form word should not become a
        # distinctive name. Short forms stay exact to avoid over-stripping.
        core = [part for part in parts if part not in ORG_LEGAL_TOKENS and not (
            len(part) >= 5 and any(len(legal) >= 5 and abs(len(legal) - len(part)) <= 1
                                   and DamerauLevenshtein.distance(part, legal) == 1
                                   for legal in ORG_LEGAL_TOKENS))]
        parts = core or parts
    ignored = ORG_GENERIC_TOKENS if organisation else PERSON_PARTICLES | HONORIFICS
    informative = [part for part in parts if part not in ignored]
    return informative or ([] if not organisation else parts)


def align(query, candidate, *, organisation=False, allow_short_org_typo=False):
    """Greedy one-to-one matches; query initials may match complete person words."""
    edges = []
    for i, a in enumerate(query):
        for j, b in enumerate(candidate):
            if a == b:
                sim = 1.0
            elif not organisation and len(a) == 1 and len(b) > 1 and a == b[0]:
                sim = 0.85
            elif (organisation and allow_short_org_typo and len(a) == len(b) == 3
                  and a.isalpha() and b.isalpha() and Levenshtein.distance(a, b) == 1):
                sim = Indel.normalized_similarity(a, b)
            elif min(len(a), len(b)) >= 4:
                jw = JaroWinkler.similarity(a, b)
                indel = Indel.normalized_similarity(a, b)
                # A single substitution is a plausible typo, including when JW
                # drops just below its cutoff. Longer differences still require
                # the existing thresholds; this does not establish identity.
                substitution = (len(a) == len(b) and len(a) >= 5 and a.isalpha() and b.isalpha()
                                and Levenshtein.distance(a, b) == 1)
                if not substitution and (jw < (0.90 if organisation else 0.86) or
                        (indel < (0.90 if organisation else 0.80)
                         and not (organisation and adjacent_transposition(a, b)))):
                    continue
                sim = min(jw, indel)
            else:
                continue
            edges.append((sim, i, j))
    used_i, used_j, matches = set(), set(), []
    for sim, i, j in sorted(edges, reverse=True):
        if i not in used_i and j not in used_j:
            used_i.add(i); used_j.add(j); matches.append((i, j, sim))
    return matches


def paired_generic_typos(query, candidate, q_words, c_words):
    """Ignore a generic-word typo only when the opposite name contains it.

    Keep the typo distinctive unless another exact substantive word also
    agrees. This is still ambiguous (e.g. shopping/shipping) and only an
    experimental heuristic, not an identity conclusion.
    """
    q_generic = set(tokens(query)) & ORG_GENERIC_TOKENS
    c_generic = set(tokens(candidate)) & ORG_GENERIC_TOKENS

    def typo(word, generic):
        return word.isalpha() and len(word) >= 5 and any(
            len(other) >= 5 and abs(len(word)-len(other)) <= 1
            and DamerauLevenshtein.distance(word, other) == 1 for other in generic)

    q_new = [word for word in q_words if not typo(word, c_generic)]
    c_new = [word for word in c_words if not typo(word, q_generic)]
    if q_new and c_new and set(q_new) & set(c_new):
        return q_new, c_new
    return q_words, c_words


def plausible_person_partial_score(q, c, matched):
    """Score two narrow ambiguities as potential matches for review.

    A listed initial may explain a query word if another complete name part is
    exactly equal. A shorter listed name may omit one query part if its two or
    more substantive words all match exactly. Neither proves identity.
    """
    exact = [(i, j) for i, j, _ in matched if q[i] == c[j] and len(q[i]) >= 3]
    if (len(q) >= 3 and len(q) - len(matched) == 1 and len(matched) == len(c)
            and len(exact) >= 2):
        return min(79, round(100 * sum(sim for _, _, sim in matched) / len(q)))
    if not any(len(q[i]) >= 4 for i, _ in exact):
        return None
    used_q={i for i, _, _ in matched}; used_c={j for _, j, _ in matched}
    initial_count=0
    for j, word in enumerate(c):
        if j in used_c or len(word) != 1:
            continue
        for i, query_word in enumerate(q):
            if i not in used_q and len(query_word) >= 4 and query_word[0] == word:
                used_q.add(i);used_c.add(j);initial_count+=1;break
    if len(used_q)==len(q) and len(q)>=2 and initial_count:
        return min(79, round(100 * (sum(sim for _, _, sim in matched) + 0.5 * initial_count) / len(q)))
    return None


def relaxed_pairs(q, c):
    """One-to-one weak pairs, usable only for a low-scored review candidate."""
    edges=[]
    for i,a in enumerate(q):
        for j,b in enumerate(c):
            if a == b and len(a) >= 3:
                sim=1.0
            elif min(len(a),len(b)) == 1 and a[0] == b[0] and max(len(a),len(b)) >= 4:
                sim=0.5
            elif a.isalpha() and b.isalpha() and min(len(a),len(b)) >= 4:
                jw=JaroWinkler.similarity(a,b); indel=Indel.normalized_similarity(a,b)
                if jw < 0.85 or indel < 0.65:
                    continue
                sim=min(jw,indel)
            else:
                continue
            edges.append((sim,i,j))
    seen_q=set();seen_c=set();pairs=[]
    for sim,i,j in sorted(edges,reverse=True):
        if i not in seen_q and j not in seen_c:
            seen_q.add(i);seen_c.add(j);pairs.append((i,j,sim))
    return pairs


def ambiguous_person_score(q, c):
    pairs=relaxed_pairs(q,c)
    full=[(i,j) for i,j,_ in pairs if min(len(q[i]),len(c[j])) >= 3]
    # The fallback is intentionally bounded to two or three query words and
    # needs two separate word pairings, at least one of them non-initial.
    if not (2 <= len(q) <= 3 and len(pairs) >= 2 and full):
        return None
    return min(69,round(100*sum(sim for _,_,sim in pairs)/len(q)))


def ambiguous_organisation_score(query, candidate, q, c):
    from utils import _org_token_profile, get_effective_org_generic_tokens
    # The current organisation policy can mark business words as generic.
    # A shared short core and shared generic word are weak, not an exact ID.
    qp=_org_token_profile(query);cp=_org_token_profile(candidate)
    qi=qp['informative_set'];ci=cp['informative_set']
    if qi and qi==ci and qp['generic_set'] & cp['generic_set']:
        return 60
    pairs=relaxed_pairs(q,c)
    required=min(2,max(len(q),len(c)))
    full=[(i,j) for i,j,_ in pairs if min(len(q[i]),len(c[j])) >= 5
          and JaroWinkler.similarity(q[i],c[j]) >= 0.90
          and Indel.normalized_similarity(q[i],c[j]) >= 0.80]
    if not q or not c or len(q)>3 or len(pairs)<required or not full:
        return None
    if len(q)==len(c)==1 and q[0]!=c[0]:
        generic=ORG_GENERIC_TOKENS | get_effective_org_generic_tokens()
        query_words=set(tokens(query));candidate_words=set(tokens(candidate))
        shared=(query_words & candidate_words & generic)
        logistics={'shipping','logistics'}
        related_sector=bool(query_words & logistics) and bool(candidate_words & logistics)
        if not shared and not related_sector:
            return None
    return min(69,round(100*sum(sim for _,_,sim in pairs)/max(len(q),len(c))))


def evaluate(query, candidate, match_type, submitted_type):
    """Return a shadow name score and reason; None means removed for review.

    Current retrieval and exact DOB conflicts remain upstream. Evaluate each
    candidate under its own name rules even when the submitted type differs:
    a misclassified input must not remove a plausible match. Missing identity
    evidence never itself clears a candidate. Cross-script source names with
    lossy normalization are ineligible as standalone matches, but other aliases
    are evaluated.
    """
    from utils import has_unsupported_name_characters
    if has_unsupported_name_characters(candidate):
        return None, 'source_name_loses_characters'
    org = (match_type or '').lower() == 'organization'
    q, c = words(query, organisation=org), words(candidate, organisation=org)
    if org:
        q, c = paired_generic_typos(query, candidate, q, c)
    if not q or not c:
        return None, 'no_comparable_name_parts'
    common_generic = set(tokens(query)) & set(tokens(candidate)) & ORG_GENERIC_TOKENS
    short_org_typo = org and len(q) == len(c) == 1 and bool(common_generic)
    matched = align(q, c, organisation=org, allow_short_org_typo=short_org_typo)
    if len(matched) < len(q):
        partial_score = None if org else plausible_person_partial_score(q, c, matched)
        if partial_score is not None:
            return partial_score, 'ambiguous_partial_name_review'
        weak_score = ambiguous_organisation_score(query,candidate,q,c) if org else ambiguous_person_score(q,c)
        if weak_score is not None:
            return weak_score, 'ambiguous_name_review'
        return None, 'unmatched_query_parts'
    if org and len(matched) < len(c):
        weak_score=ambiguous_organisation_score(query,candidate,q,c)
        if weak_score is not None:
            return weak_score, 'ambiguous_name_review'
        return None, 'unmatched_organisation_parts'
    if org:
        # Score the actual aligned words, with a modest penalty for unmatched
        # non-legal words. This is an explainable heuristic, not a probability.
        q_generic = Counter(x for x in tokens(query) if x in ORG_GENERIC_TOKENS)
        c_generic = Counter(x for x in tokens(candidate) if x in ORG_GENERIC_TOKENS)
        extras = sum((q_generic - c_generic).values()) + sum((c_generic - q_generic).values())
        score = max(0, round(100 * sum(s for _, _, s in matched) / max(len(q), len(c))) - min(12, extras * 3))
        # A legal-form omission or typo must not be displayed as identical.
        if sorted(tokens(query)) != sorted(tokens(candidate)):
            score = min(score, 99)
    else:
        score = -1  # sentinel: preserve current person's score for accepted candidates
    return score, 'name_parts_agree'


def apply_rules(query, names, hits, added, entity_type, submitted_type):
    """Apply the tested name rules to an already retrieved candidate set."""
    from name_matching import normalize_text
    original_indexes = {hit[2] for hit in hits}
    kept = []
    kept_added = set()
    for normalized, score, index in hits:
        proposed, _ = evaluate(query, names[index], entity_type, submitted_type)
        if proposed is None:
            continue
        kept.append((normalized, score if proposed == -1 else proposed, index))
        if index in added:
            kept_added.add(index)
    if (entity_type or '').lower() == 'organization':
        for index, candidate in enumerate(names):
            if index in original_indexes:
                continue
            proposed, reason = evaluate(query, candidate, entity_type, submitted_type)
            if proposed is not None and proposed >= 75 and reason == 'name_parts_agree':
                kept.append((normalize_text(candidate), proposed, index))
                kept_added.add(index)
    return sorted(kept, key=lambda hit: hit[1], reverse=True), kept_added
