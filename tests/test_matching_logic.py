import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

import api_server
from api_server import ReviewRerunRequest
from utils import build_input_classification, detect_company_likeness, get_best_name_matches


class MatchingLogicTests(unittest.TestCase):
    def test_org_generic_words_do_not_drive_false_positive(self):
        hits = get_best_name_matches(
            "BUZZ PROPERTY MANAGEMENT LTD",
            ["PRINCE PROPERTY CAMBODIA MANAGEMENT CO. LTD."],
            threshold=75,
            entity_type="Organization",
        )
        self.assertEqual(hits, [])

    def test_org_distinctive_anchor_is_required(self):
        hits = get_best_name_matches(
            "Ezytrac Property Management",
            ["Pacific Real Estate Property Management Company"],
            threshold=75,
            entity_type="Organization",
        )
        self.assertEqual(hits, [])

    def test_org_close_variants_still_match(self):
        hits = get_best_name_matches(
            "Hyde and Partners",
            ["Hyde & Partners Ltd"],
            threshold=75,
            entity_type="Organization",
        )
        self.assertTrue(hits)
        self.assertGreaterEqual(hits[0][1], 75)

    def test_org_generic_overlap_only_is_rejected(self):
        hits = get_best_name_matches(
            "Hyde and Partners",
            ["Siavash Nurian and Partners"],
            threshold=75,
            entity_type="Organization",
        )
        self.assertEqual(hits, [])

    def test_org_borderline_name_can_surface_as_suggestion_only(self):
        decision_hits = get_best_name_matches(
            "Digital Move Limited (Smart Move)",
            ["Smart Digital Ideas DOO"],
            threshold=75,
            entity_type="Organization",
        )
        suggestion_hits = get_best_name_matches(
            "Digital Move Limited (Smart Move)",
            ["Smart Digital Ideas DOO"],
            threshold=60,
            strict_short_queries=False,
            entity_type="Organization",
        )
        self.assertEqual(decision_hits, [])
        self.assertTrue(suggestion_hits)

    def test_person_name_is_not_flagged_as_company(self):
        classification = detect_company_likeness("John Edward Hyde")
        self.assertFalse(classification["looks_like_company"])

    def test_company_like_person_submission_is_flagged(self):
        classification = build_input_classification(
            name="Hyde and Partners",
            submitted_as="Person",
            person_result={"Match Found": False, "Score": 0},
            organization_result={"Match Found": True, "Score": 84},
            pep_checked=False,
        )
        self.assertTrue(classification["likely_misclassified"])
        self.assertEqual(classification["inferred_as"], "Organization")


class _FakeAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


class ReviewRerunTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_rerun_keeps_same_entity_key_and_corrects_type(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/review/entity-123/rerun",
                "headers": [],
                "client": ("127.0.0.1", 12345),
            }
        )
        review_row = {
            "entity_key": "entity-123",
            "display_name": "Hyde and Partners",
            "entity_type": "Person",
            "date_of_birth": None,
            "business_reference": "BR-1",
            "reason_for_check": "Ad-Hoc Compliance Review",
            "last_requestor": "analyst@example.com",
            "review_status": "IN_REVIEW",
            "review_claimed_by": "analyst@example.com",
        }

        with patch.object(
            api_server.screening_db,
            "get_pool",
            AsyncMock(return_value=_FakePool()),
        ), patch.object(
            api_server.screening_db,
            "search_screened_entities",
            AsyncMock(return_value=[review_row]),
        ), patch.object(
            api_server,
            "_check_opensanctions_impl",
            AsyncMock(return_value={
                "Check Summary": {"Status": "Fail Sanction", "Source": "Test", "Date": "2026-04-15 10:00:00"},
                "Is Sanctioned": True,
                "Is PEP": False,
                "Score": 81,
            }),
        ) as check_mock:
            response = await api_server.rerun_review(
                request,
                "entity-123",
                ReviewRerunRequest(entity_type="Organization", country="United Kingdom"),
                payload={"sub": "analyst@example.com"},
            )

        self.assertEqual(response["entity_key"], "entity-123")
        self.assertTrue(response["type_corrected"])
        self.assertEqual(response["original_entity_type"], "Person")
        self.assertEqual(response["corrected_entity_type"], "Organization")
        called_req = check_mock.await_args.args[0]
        self.assertEqual(called_req.rerun_entity_key, "entity-123")
        self.assertEqual(called_req.entity_type, "Organization")
        self.assertEqual(called_req.country, "United Kingdom")


if __name__ == "__main__":
    unittest.main()
