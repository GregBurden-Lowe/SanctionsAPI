from services import companies_house


def test_address_risk_counts_exact_postcode_matches(monkeypatch):
    def fake_search_companies(query):
        return [
            {"address_snippet": "1 Example Road, Bristol, BS35 3QH"},
            {"address_snippet": "2 Example Road, Bristol, BS35 3QH"},
            {"address_snippet": "3 Example Road, Bristol, BS35 3QA"},
            {"address_snippet": "BS35 Trading Estate, Thornbury"},
            {"address_snippet": None},
        ]

    monkeypatch.setattr(companies_house, "search_companies", fake_search_companies)

    result = companies_house.detect_address_risk(
        {"registered_office_address": {"postal_code": "BS35 3QH"}}
    )

    assert result == {
        "postcode": "BS35 3QH",
        "company_count": 2,
        "risk_level": "LOW",
    }
