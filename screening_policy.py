"""Entity-type policy for interactive and background screening (sanctions only)."""
from utils import (detect_company_likeness, build_input_classification, effective_screening_country,
                   is_dob_required_result, _DISPOSITION_ORDER)


def _result_priority(result: dict) -> int:
    # A potential match outranks a search that only needs a date of birth.
    if is_dob_required_result(result):
        return 2
    if bool(result.get("Is Sanctioned")):
        return 3
    return 1


def _status_for_type_check(result: dict) -> str:
    summary = result.get("Check Summary") if isinstance(result.get("Check Summary"), dict) else {}
    status = str(summary.get("Status") or "").strip()
    if status:
        return status
    if bool(result.get("Is Sanctioned")):
        return "Fail Sanction"
    return "Cleared"


def _looks_like_company_name(name: str) -> bool:
    return bool(detect_company_likeness(name).get("looks_like_company"))


def _merge_dual_type_results(
    person_result: dict,
    organization_result: dict,
    *,
    name: str,
    submitted_entity_type: str,
) -> dict:
    primary = person_result
    if _result_priority(organization_result) > _result_priority(person_result):
        primary = organization_result
    elif _result_priority(organization_result) == _result_priority(person_result):
        if float(organization_result.get("Score") or 0) > float(person_result.get("Score") or 0):
            primary = organization_result

    person_status = _status_for_type_check(person_result)
    org_status = _status_for_type_check(organization_result)
    merged = dict(primary)
    if "Candidate Evidence" in person_result or "Candidate Evidence" in organization_result:
        evidence = person_result.get("Candidate Evidence", []) + organization_result.get("Candidate Evidence", [])
        evidence.sort(key=lambda item: (_DISPOSITION_ORDER.get(item["disposition"], 3), -item["name_score"]))
        merged["Candidate Evidence"] = evidence[:50]
        summaries = [person_result.get("Candidate Evidence Summary", {}), organization_result.get("Candidate Evidence Summary", {})]
        merged["Candidate Evidence Summary"] = {
            key: sum(int(item.get(key, 0)) for item in summaries)
            for key in ("evaluated_name_matches", "potential_matches", "excluded", "dob_required", "below_alert_threshold")
            if key in ("evaluated_name_matches", "potential_matches", "excluded") or any(key in item for item in summaries)
        }
        merged["Candidate Evidence Summary"]["display_truncated"] = len(evidence) > 50 or any(item.get("display_truncated") for item in summaries)
    merged["Entity Type Checks"] = {
        "Person": {
            "status": person_status,
            "is_match": not person_status.lower().startswith("cleared"),
            "score": float(person_result.get("Score") or 0),
        },
        "Organization": {
            "status": org_status,
            "is_match": not org_status.lower().startswith("cleared"),
            "score": float(organization_result.get("Score") or 0),
        },
    }
    merged["Input Classification"] = build_input_classification(
        name=name,
        submitted_as=submitted_entity_type,
        person_result=person_result,
        organization_result=organization_result,
    )
    return merged


def check_plan(name, dob, country, entity_type):
    country = effective_screening_country(country)
    return [
        dict(name=name, dob=dob, country=country, entity_type='Person'),
        dict(name=name, dob=None, country=country, entity_type='Organization'),
    ]


async def run_postgres_check(conn, *, name, dob, country, entity_type, requestor=None, matcher=None, log_search=False, require_assurance=False):
    from utils import perform_postgres_watchlist_check
    from source_assurance import require_current, assess, postgres_manifest
    actual = matcher is None or matcher is perform_postgres_watchlist_check
    matcher = matcher or perform_postgres_watchlist_check
    plan = check_plan(name, dob, country, entity_type)
    coverage = None
    if actual:
        async with conn.transaction():
            await conn.execute('LOCK TABLE watchlist_entities IN ACCESS SHARE MODE')
            manifest = await postgres_manifest(conn)
            coverage = require_current(manifest) if require_assurance else assess(manifest)
            results = [await matcher(conn, **item, requestor=requestor, log_search=False,
                                     submitted_entity_type=entity_type) for item in plan]
    else:
        results = [await matcher(conn, **item, requestor=requestor, log_search=False) for item in plan]
    merged = _merge_dual_type_results(*results, name=name, submitted_entity_type=entity_type)
    if coverage is not None:
        merged['Source Coverage'] = coverage
    if log_search:
        from utils import _append_search_to_csv
        _append_search_to_csv(name, merged['Check Summary'])
    return merged
