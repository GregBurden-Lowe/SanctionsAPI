"""One entity-type and PEP policy for interactive and background screening."""
from typing import Optional
from utils import detect_company_likeness, build_input_classification

def _result_priority(result: dict) -> int:
    if bool(result.get("Is Sanctioned")):
        return 3
    if bool(result.get("Is PEP")):
        return 2
    return 1


def _status_for_type_check(result: dict) -> str:
    summary = result.get("Check Summary") if isinstance(result.get("Check Summary"), dict) else {}
    status = str(summary.get("Status") or "").strip()
    if status:
        return status
    if bool(result.get("Is Sanctioned")):
        return "Fail Sanction"
    if bool(result.get("Is PEP")):
        return "Fail PEP"
    return "Cleared"


def _looks_like_company_name(name: str) -> bool:
    return bool(detect_company_likeness(name).get("looks_like_company"))


def _merge_dual_type_results(
    person_result: dict,
    organization_result: dict,
    *,
    name: str,
    submitted_entity_type: str,
    pep_checked: bool,
    pep_skip_reason: Optional[str] = None,
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
    merged["Is PEP"] = bool(person_result.get("Is PEP") or organization_result.get("Is PEP"))
    if "Candidate Evidence" in person_result or "Candidate Evidence" in organization_result:
        evidence = person_result.get("Candidate Evidence", []) + organization_result.get("Candidate Evidence", [])
        evidence.sort(key=lambda item: (item["disposition"] != "potential_match", item["source_type"] != "sanctions", -item["name_score"]))
        merged["Candidate Evidence"] = evidence[:50]
        summaries = [person_result.get("Candidate Evidence Summary", {}), organization_result.get("Candidate Evidence Summary", {})]
        merged["Candidate Evidence Summary"] = {
            key: sum(int(item.get(key, 0)) for item in summaries)
            for key in ("evaluated_name_matches", "potential_matches", "excluded")
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
    merged["PEP Check"] = {
        "checked": bool(pep_checked),
        "status": "checked" if pep_checked else "skipped",
        "reason": pep_skip_reason,
        "message": (
            (
                "PEP screening skipped for Organization checks."
                if pep_skip_reason == "entity_type_organization"
                else "PEP screening skipped because the query appears to be a company name."
            )
            if not pep_checked
            else "PEP screening executed."
        ),
    }
    merged["Input Classification"] = build_input_classification(
        name=name,
        submitted_as=submitted_entity_type,
        person_result=person_result,
        organization_result=organization_result,
        pep_checked=pep_checked,
    )
    return merged


def check_plan(name, dob, country, entity_type):
    kind = (entity_type or 'Person').strip().lower()
    pep = kind == 'person' and not _looks_like_company_name(name)
    reason = None if pep else ('entity_type_organization' if kind == 'organization' else 'company_name_detected')
    return pep, reason, [
        dict(name=name, dob=dob, country=country, entity_type='Person', include_peps=pep),
        dict(name=name, dob=None, country=country, entity_type='Organization', include_peps=False),
    ]


def run_parquet_check(*, name, dob, country, entity_type, requestor=None, matcher=None, log_search=False, require_assurance=False):
    from utils import perform_opensanctions_check, get_opensanctions_df
    from source_assurance import require_current, assess
    actual = matcher is None or matcher is perform_opensanctions_check
    matcher = matcher or perform_opensanctions_check
    pep, reason, plan = check_plan(name, dob, country, entity_type)
    coverage = None
    if actual:
        # Pin a single dataframe for both entity-type passes and its embedded manifest.
        frame = get_opensanctions_df()
        manifest = frame.attrs.get('source_manifest', {})
        coverage = require_current(manifest, pep) if require_assurance else assess(manifest, pep)
        results = [matcher(**item, requestor=requestor, log_search=False, dataframe=frame) for item in plan]
    else:
        results = [matcher(**item, requestor=requestor, log_search=False) for item in plan]
    merged = _merge_dual_type_results(*results, name=name, submitted_entity_type=entity_type,
                                     pep_checked=pep, pep_skip_reason=reason)
    if coverage is not None:
        merged['Source Coverage'] = coverage
    if log_search:
        from utils import _append_search_to_csv
        _append_search_to_csv(name, merged['Check Summary'])
    return merged


async def run_postgres_check(conn, *, name, dob, country, entity_type, requestor=None, matcher=None, log_search=False, require_assurance=False):
    from utils import perform_postgres_watchlist_check
    from source_assurance import require_current, assess, postgres_manifest
    actual = matcher is None or matcher is perform_postgres_watchlist_check
    matcher = matcher or perform_postgres_watchlist_check
    pep, reason, plan = check_plan(name, dob, country, entity_type)
    coverage = None
    if actual:
        async with conn.transaction():
            # Prevent a refresh between manifest read and either retrieval pass.
            await conn.execute('LOCK TABLE watchlist_entities IN ACCESS SHARE MODE')
            manifest = await postgres_manifest(conn)
            coverage = require_current(manifest, pep) if require_assurance else assess(manifest, pep)
            results = [await matcher(conn, **item, requestor=requestor, log_search=False) for item in plan]
    else:
        results = [await matcher(conn, **item, requestor=requestor, log_search=False) for item in plan]
    merged = _merge_dual_type_results(*results, name=name, submitted_entity_type=entity_type,
                                     pep_checked=pep, pep_skip_reason=reason)
    if coverage is not None:
        merged['Source Coverage'] = coverage
    if log_search:
        from utils import _append_search_to_csv
        _append_search_to_csv(name, merged['Check Summary'])
    return merged
