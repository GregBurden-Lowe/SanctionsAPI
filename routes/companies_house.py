from fastapi import APIRouter, HTTPException, Query

from services.companies_house import (
    CompaniesHouseConfigError,
    CompaniesHouseConnectionError,
    CompaniesHouseNotFoundError,
    CompaniesHouseRateLimitError,
    CompaniesHouseUpstreamError,
    get_company,
    get_officers,
    resolve_best_company_match,
    search_companies,
)


router = APIRouter(prefix="/api/companies", tags=["companies-house"])


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, CompaniesHouseConfigError):
        raise HTTPException(status_code=503, detail="Companies House integration not configured")
    if isinstance(exc, CompaniesHouseNotFoundError):
        raise HTTPException(status_code=404, detail="Company not found")
    if isinstance(exc, CompaniesHouseRateLimitError):
        raise HTTPException(status_code=429, detail="Companies House rate limit exceeded")
    if isinstance(exc, CompaniesHouseConnectionError):
        raise HTTPException(status_code=502, detail="Unable to connect to Companies House")
    if isinstance(exc, CompaniesHouseUpstreamError):
        raise HTTPException(status_code=502, detail="Companies House upstream error")
    raise HTTPException(status_code=500, detail="Unexpected Companies House integration error")


@router.get("/search")
def companies_search(q: str = Query(..., min_length=1, description="Company name search query")):
    try:
        return {"items": search_companies(q)}
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/search/best-match")
def companies_best_match(q: str = Query(..., min_length=1, description="Company name search query")):
    try:
        item = resolve_best_company_match(q)
        return {"item": item}
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/{company_number}")
def companies_get(company_number: str):
    try:
        return get_company(company_number)
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/{company_number}/officers")
def companies_officers(company_number: str):
    try:
        return {"items": get_officers(company_number)}
    except Exception as exc:
        _raise_http_error(exc)


@router.get("/{company_number}/screen")
def companies_screen_bundle(company_number: str):
    try:
        company = get_company(company_number)
        officers = get_officers(company_number)
        return {"company": company, "officers": officers}
    except Exception as exc:
        _raise_http_error(exc)

