import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.auth import HTTPBasicAuth
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException, Timeout


BASE_URL = "https://api.company-information.service.gov.uk"
TIMEOUT_SECONDS = 12


class CompaniesHouseError(Exception):
    pass


class CompaniesHouseConfigError(CompaniesHouseError):
    pass


class CompaniesHouseNotFoundError(CompaniesHouseError):
    pass


class CompaniesHouseRateLimitError(CompaniesHouseError):
    pass


class CompaniesHouseConnectionError(CompaniesHouseError):
    pass


class CompaniesHouseUpstreamError(CompaniesHouseError):
    pass


def _get_api_key() -> str:
    """Return the Companies House API key from CH_API_KEY."""
    api_key = (os.environ.get("CH_API_KEY") or "").strip()
    if not api_key:
        raise CompaniesHouseConfigError("CH_API_KEY is not configured")
    return api_key


def _request(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    allow_404: bool = False,
) -> Optional[Dict[str, Any]]:
    """Perform an authenticated GET request to Companies House and return JSON payload.

    When allow_404=True, a 404 response returns None instead of raising.
    """
    url = f"{BASE_URL}{path}"
    try:
        response = requests.get(
            url,
            params=params or None,
            auth=HTTPBasicAuth(_get_api_key(), ""),
            timeout=TIMEOUT_SECONDS,
        )
    except (Timeout, RequestsConnectionError) as exc:
        raise CompaniesHouseConnectionError("Could not connect to Companies House API") from exc
    except RequestException as exc:
        raise CompaniesHouseConnectionError("Request to Companies House API failed") from exc

    if response.status_code == 404 and allow_404:
        return None
    if response.status_code == 404:
        raise CompaniesHouseNotFoundError("Company not found")
    if response.status_code == 429:
        raise CompaniesHouseRateLimitError("Companies House rate limit exceeded")
    if response.status_code >= 500:
        raise CompaniesHouseUpstreamError("Companies House service unavailable")
    if response.status_code >= 400:
        raise CompaniesHouseUpstreamError(f"Companies House error: HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise CompaniesHouseUpstreamError("Invalid JSON returned by Companies House API") from exc
    if not isinstance(payload, dict):
        raise CompaniesHouseUpstreamError("Unexpected response payload from Companies House API")
    return payload


def search_companies(query: str) -> List[Dict[str, Any]]:
    """Search Companies House by query and return a simplified company list.

    Results are capped to the first 100 items for predictable performance.
    """
    q = (query or "").strip()
    if not q:
        return []
    data = _request("/search/companies", params={"q": q, "items_per_page": 100})
    items = (data.get("items") or [])[:100]
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "company_name": item.get("title"),
                "company_number": item.get("company_number"),
                "company_status": item.get("company_status"),
                "date_of_creation": item.get("date_of_creation"),
                "address_snippet": item.get("address_snippet"),
            }
        )
    return out


def get_company(company_number: str) -> Dict[str, Any]:
    """Return simplified company profile details for a company number."""
    number = (company_number or "").strip()
    if not number:
        raise CompaniesHouseNotFoundError("Invalid company number")
    data = _request(f"/company/{number}")
    return {
        "company_name": data.get("company_name"),
        "company_number": data.get("company_number"),
        "company_status": data.get("company_status"),
        "date_of_creation": data.get("date_of_creation"),
        "sic_codes": data.get("sic_codes"),
        "accounts": data.get("accounts"),
        "has_charges": data.get("has_charges"),
        "registered_office_address": data.get("registered_office_address"),
    }


def get_officers(company_number: str, active_only: bool = False) -> List[Dict[str, Any]]:
    """Return simplified officers/directors for a company number.

    Each officer includes a computed status:
    - "resigned" when resigned_on is present
    - "active" when resigned_on is absent

    Args:
        company_number: Companies House company number.
        active_only: When True, returns only officers with status="active".
    """
    number = (company_number or "").strip()
    if not number:
        raise CompaniesHouseNotFoundError("Invalid company number")
    items = _all_items(f"/company/{number}/officers")
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dob = item.get("date_of_birth") if isinstance(item.get("date_of_birth"), dict) else None
        resigned_on = item.get("resigned_on") or None
        status = "resigned" if resigned_on else "active"
        if active_only and status != "active":
            continue
        officer_id: Optional[str] = None
        links = item.get("links") if isinstance(item.get("links"), dict) else {}
        appointments_link = links.get("officer", {}).get("appointments") if isinstance(links.get("officer"), dict) else None
        self_link = links.get("self")
        for raw_link in (appointments_link, self_link):
            if not isinstance(raw_link, str):
                continue
            m = re.search(r"/officers/([^/]+)", raw_link)
            if m:
                officer_id = m.group(1)
                break
        out.append(
            {
                "name": item.get("name"),
                "role": item.get("officer_role"),
                "status": status,
                "appointed_on": item.get("appointed_on"),
                "resigned_on": resigned_on,
                "officer_role": item.get("officer_role"),  # compatibility for existing callers
                "nationality": item.get("nationality"),
                "date_of_birth": dob,
                "officer_id": officer_id,
                "country_of_residence": item.get("country_of_residence"),
                "source_path": (item.get("links") or {}).get("self"),
            }
        )
    return out


def resolve_best_company_match(query: str) -> Optional[Dict[str, Any]]:
    """Return the first active company match for a query, else first result."""
    results = search_companies(query)
    for row in results:
        if str(row.get("company_status") or "").strip().lower() == "active":
            return row
    return results[0] if results else None


def get_insolvency(company_number: str) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Return simplified insolvency case data for a company, or None when not found.

    Companies House returns 404 when there is no insolvency history; this is treated
    as a non-error and maps to None.
    """
    number = (company_number or "").strip()
    if not number:
        raise CompaniesHouseNotFoundError("Invalid company number")
    data = _request(f"/company/{number}/insolvency", allow_404=True)
    if data is None:
        return None

    cases_raw = data.get("cases") or []
    simplified_cases: List[Dict[str, Any]] = []
    for case in cases_raw:
        if not isinstance(case, dict):
            continue
        practitioners_raw = case.get("practitioners") or []
        practitioners: List[str] = []
        for practitioner in practitioners_raw:
            if not isinstance(practitioner, dict):
                continue
            name_parts = practitioner.get("name")
            if isinstance(name_parts, dict):
                full_name = " ".join(
                    p.strip()
                    for p in [
                        str(name_parts.get("forename") or "").strip(),
                        str(name_parts.get("surname") or "").strip(),
                    ]
                    if p.strip()
                )
                if full_name:
                    practitioners.append(full_name)
            elif practitioner.get("name"):
                practitioners.append(str(practitioner.get("name")).strip())

        simplified_cases.append(
            {
                "type": case.get("type"),
                "case_start_date": case.get("date"),
                "practitioners": practitioners,
            }
        )
    return {"cases": simplified_cases}


def get_officer_appointments(officer_id: str) -> List[Dict[str, Any]]:
    """Return simplified appointment history for an officer ID.

    Calls GET /officers/{officer_id}/appointments and maps the response to:
    company_number, company_name, company_status, appointed_on, resigned_on.
    """
    oid = (officer_id or "").strip()
    if not oid:
        raise CompaniesHouseNotFoundError("Missing officer ID")
    data = _request(f"/officers/{oid}/appointments")
    items = data.get("items") or []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "company_number": item.get("appointed_to", {}).get("company_number")
                if isinstance(item.get("appointed_to"), dict)
                else None,
                "company_name": item.get("appointed_to", {}).get("company_name")
                if isinstance(item.get("appointed_to"), dict)
                else None,
                "company_status": item.get("appointed_to", {}).get("company_status")
                if isinstance(item.get("appointed_to"), dict)
                else None,
                "appointed_on": item.get("appointed_on"),
                "resigned_on": item.get("resigned_on"),
            }
        )
    return out


def analyse_director_risk(officer_id: str) -> Dict[str, Any]:
    """Analyse a director's risk profile from their company appointments.

    Rules:
    - HIGH: dissolved companies > 5
    - MEDIUM: dissolved companies between 2 and 5
    - LOW: dissolved companies < 2

    Only the first 20 appointments are analysed for performance.
    If appointment data cannot be retrieved, safe fallback values are returned.
    """
    oid = (officer_id or "").strip()
    fallback = {
        "total_appointments": 0,
        "total_companies": 0,
        "active_companies": 0,
        "dissolved_companies": 0,
        "liquidated_companies": 0,
        "flags": [],
        "risk_level": "UNKNOWN",
    }
    if not oid:
        return fallback
    try:
        appointments = get_officer_appointments(oid)[:20]
    except (
        CompaniesHouseRateLimitError,
        CompaniesHouseConnectionError,
        CompaniesHouseUpstreamError,
        CompaniesHouseNotFoundError,
    ):
        return fallback

    total = len(appointments)
    flags: List[str] = []
    active = 0
    dissolved = 0
    liquidated = 0
    for appointment in appointments:
        status = str(appointment.get("company_status") or "").strip().lower()
        if status == "active":
            active += 1
        if status == "dissolved":
            dissolved += 1
        if "liquidation" in status or status == "liquidated":
            liquidated += 1

    if dissolved > 5:
        risk = "HIGH"
    elif 2 <= dissolved <= 5:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    if total > 50:
        flags.append("nominee_director")
    return {
        "total_appointments": total,
        "total_companies": total,
        "active_companies": active,
        "dissolved_companies": dissolved,
        "liquidated_companies": liquidated,
        "flags": flags,
        "risk_level": risk,
    }


def detect_shell_company_risk(company_data: Dict[str, Any], directors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply a lightweight shell-company risk model using existing Companies House data.

    Scoring model:
    - NEW COMPANY (<12 months): +2
    - NON-ACTIVE STATUS: +3
    - MISSING SIC CODES: +1
    - OVERDUE ACCOUNTS: +2
    - HIGH-RISK DIRECTOR: +3

    Risk mapping:
    - 0-2: LOW
    - 3-5: MEDIUM
    - 6+: HIGH
    """
    score = 0
    flags: List[str] = []

    # Company age in months from date_of_creation (YYYY-MM-DD)
    age_months: Optional[int] = None
    raw_creation = str(company_data.get("date_of_creation") or "").strip()
    if raw_creation:
        try:
            created = datetime.strptime(raw_creation, "%Y-%m-%d")
            now = datetime.utcnow()
            age_months = max(0, (now.year - created.year) * 12 + (now.month - created.month))
        except ValueError:
            age_months = None
    if age_months is not None and age_months < 12:
        score += 2
        flags.append("new_company")

    status = str(company_data.get("company_status") or "").strip().lower()
    if status and status != "active":
        score += 3
        flags.append("non_active_status")

    sic_codes = company_data.get("sic_codes")
    if not isinstance(sic_codes, list) or len(sic_codes) == 0:
        score += 1
        flags.append("missing_sic_codes")

    accounts = company_data.get("accounts") if isinstance(company_data.get("accounts"), dict) else {}
    if bool(accounts.get("overdue")):
        score += 2
        flags.append("accounts_overdue")

    has_high_risk_director = any(
        str((d.get("risk") or {}).get("risk_level") or "").strip().upper() == "HIGH"
        for d in directors
        if isinstance(d, dict)
    )
    if has_high_risk_director:
        score += 3
        flags.append("director_high_risk")

    if score >= 6:
        risk_level = "HIGH"
    elif score >= 3:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {"score": score, "risk_level": risk_level, "flags": flags}


def detect_address_risk(company_data: Dict[str, Any]) -> Dict[str, Any]:
    """Assess address concentration risk using registered office postcode search volume.

    Rules:
    - >50 companies at postcode: HIGH
    - 20-50 companies: MEDIUM
    - <20 companies: LOW
    """
    addr = company_data.get("registered_office_address")
    postcode = ""
    if isinstance(addr, dict):
        postcode = str(addr.get("postal_code") or "").strip()
    if not postcode:
        return {"postcode": None, "company_count": 0, "risk_level": "LOW"}
    matches = search_companies(postcode)
    normalized_postcode = re.sub(r"[^A-Z0-9]", "", postcode.upper())
    count = sum(
        1
        for match in matches
        if normalized_postcode
        and normalized_postcode
        in re.sub(r"[^A-Z0-9]", "", str(match.get("address_snippet") or "").upper())
    )
    if count > 50:
        level = "HIGH"
    elif count >= 20:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {"postcode": postcode, "company_count": count, "risk_level": level}


def detect_director_turnover(officers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect rapid director turnover based on appointments and resignations in the last 12 months."""
    now = datetime.utcnow()
    appointed_last_12_months = 0
    resigned_last_12_months = 0

    def _is_within_last_12_months(raw_date: Any) -> bool:
        raw = str(raw_date or "").strip()
        if not raw:
            return False
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return False
        months = max(0, (now.year - parsed.year) * 12 + (now.month - parsed.month))
        return months <= 12

    for officer in officers:
        if not isinstance(officer, dict):
            continue
        if str(officer.get("officer_role") or "").strip().lower() != "director":
            continue
        if _is_within_last_12_months(officer.get("appointed_on")):
            appointed_last_12_months += 1
        if _is_within_last_12_months(officer.get("resigned_on")):
            resigned_last_12_months += 1
    director_changes_last_12_months = appointed_last_12_months + resigned_last_12_months
    return {
        "rapid_turnover": director_changes_last_12_months >= 3,
        "appointed_last_12_months": appointed_last_12_months,
        "resigned_last_12_months": resigned_last_12_months,
        "director_changes_last_12_months": director_changes_last_12_months,
    }


def detect_company_age_risk(company_data: Dict[str, Any]) -> Dict[str, Any]:
    """Assess age-based risk from date_of_creation.

    Rules:
    - <6 months: HIGH
    - 6-12 months: MEDIUM
    - >12 months: LOW
    """
    raw_creation = str(company_data.get("date_of_creation") or "").strip()
    if not raw_creation:
        return {"age_months": None, "risk_level": "LOW"}
    try:
        created = datetime.strptime(raw_creation, "%Y-%m-%d")
        now = datetime.utcnow()
        age_months = max(0, (now.year - created.year) * 12 + (now.month - created.month))
    except ValueError:
        return {"age_months": None, "risk_level": "LOW"}
    if age_months < 6:
        level = "HIGH"
    elif age_months <= 12:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {"age_months": age_months, "risk_level": level}


def get_company_screening_data(company_number: str) -> Dict[str, Any]:
    """Return bundled company profile, officers, insolvency, and risk indicators."""
    company = get_company(company_number)
    officers = get_officers(company_number)
    officers_with_risk: List[Dict[str, Any]] = []
    for officer in officers:
        officer_enriched = dict(officer)
        role = str(officer.get("officer_role") or "").strip().lower()
        if role == "director":
            officer_enriched["risk"] = analyse_director_risk(str(officer.get("officer_id") or ""))
        officers_with_risk.append(officer_enriched)
    shell_risk = detect_shell_company_risk(company, officers_with_risk)
    address_risk = detect_address_risk(company)
    age_risk = detect_company_age_risk(company)
    director_turnover = detect_director_turnover(officers_with_risk)
    return {
        "company": company,
        "ownership": get_ownership(company_number),
        "officers": officers_with_risk,
        "insolvency": get_insolvency(company_number),
        "shell_risk": shell_risk,
        "address_risk": address_risk,
        "age_risk": age_risk,
        "director_turnover": director_turnover,
    }


def _all_items(path):
    """Read every page; a broken/incomplete page is an error, never an empty register."""
    result = []
    while True:
        page = _request(path, params={'items_per_page': 100, 'start_index': len(result)})
        items = page.get('items') or []
        total = page.get('total_results', page.get('total_count'))
        if total is None:
            raise CompaniesHouseUpstreamError('Register pagination total is unavailable')
        if not items and len(result) < int(total):
            raise CompaniesHouseUpstreamError('Register pagination is incomplete')
        result.extend(items)
        if len(result) >= int(total):
            return result
        if len(result) > 10000:
            raise CompaniesHouseUpstreamError('Register is too large for an interactive review')


def get_ownership(company_number):
    from datetime import timezone
    observed = datetime.now(timezone.utc).isoformat()
    try:
        items = _all_items(f'/company/{company_number}/persons-with-significant-control')
        statements = _all_items(f'/company/{company_number}/persons-with-significant-control-statements')
    except CompaniesHouseError as exc:
        return {'status': 'unavailable', 'observed_at': observed, 'persons': [], 'statements': [],
                'limitations': [str(exc), 'Ownership information could not be retrieved; do not interpret this as no owners.']}
    persons = []
    hidden = False
    for item in items:
        if item.get('ceased_on'):
            continue
        kind = item.get('kind', '')
        if not item.get('name') or 'super-secure' in kind:
            hidden = True
            continue
        persons.append({'name': item['name'], 'entity_type': 'Person' if 'individual' in kind else 'Organization',
            'date_of_birth': item.get('date_of_birth'), 'country': item.get('country_of_residence'),
            'natures_of_control': item.get('natures_of_control', []),
            'source_path': item.get('links', {}).get('self'), 'kind': kind,
            'identification': item.get('identification')})
    current_statements = [item for item in statements if not item.get('ceased_on')]
    return {'status': 'requires_review' if hidden or current_statements else 'retrieved', 'observed_at': observed,
            'persons': persons, 'statements': current_statements,
            'limitations': ['Registry-reported direct relationships only. No complete ownership chain or sanctions control determination.',
                           *(['Protected ownership details are not available for screening.'] if hidden else [])]}


def connected_subjects(bundle):
    """Keep each relationship; never equate two people solely because their names agree."""
    company = bundle['company']
    subjects = [{'name': company['company_name'], 'entity_type': 'Organization', 'dob': None,
                 'country': (company.get('registered_office_address') or {}).get('country'),
                 'relationship': 'company', 'source_path': '/company/'+company['company_number']}]
    for officer in bundle['officers']:
        if officer.get('status') != 'active' or not officer.get('name'):
            continue
        subjects.append({'name': officer['name'], 'entity_type': 'Organization' if 'corporate' in (officer.get('role') or '') else 'Person',
            'dob': str(officer['date_of_birth']['year']) if (officer.get('date_of_birth') or {}).get('year') else None,
            'country': officer.get('country_of_residence'), 'relationship': officer.get('role') or 'officer',
            'source_path': officer.get('source_path')})
    for person in bundle.get('ownership', {}).get('persons', []):
        subjects.append({'name': person['name'], 'entity_type': person['entity_type'],
            'dob': str(person['date_of_birth']['year']) if (person.get('date_of_birth') or {}).get('year') else None,
            'country': person.get('country'), 'relationship': 'person with significant control',
            'source_path': person.get('source_path'), 'natures_of_control': person['natures_of_control']})
    return subjects
