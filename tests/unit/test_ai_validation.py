import json

from apps.api.app.ai_gateway import mock_candidate, review_candidate
from apps.api.app.ai_validation import extract_json_payload, repair_json_payload, validate_candidate_payload


def test_extract_json_from_fenced_text():
    payload = {"status": "reject", "candidate_id": "x", "reject_reason": "bad"}
    parsed, error = extract_json_payload(f"```json\n{json.dumps(payload)}\n```")
    assert error is None
    assert parsed == payload


def test_valid_mock_candidate_passes_schema():
    candidate = mock_candidate("structural_elegance", {"difficulty_star": 3}, [])
    validated, errors = validate_candidate_payload(candidate)
    assert errors == []
    assert validated is not None
    assert review_candidate(validated)["decision"] == "accept"


def test_invalid_candidate_is_rejected_before_review():
    validated, errors = validate_candidate_payload({"status": "ok", "candidate_id": "x", "question": {"stem_md": ""}})
    assert validated is None
    assert errors


def test_reject_candidate_is_valid_but_review_rejects():
    validated, errors = validate_candidate_payload({"status": "reject", "candidate_id": "x", "reject_reason": "cannot solve"})
    assert errors == []
    assert validated is not None
    assert review_candidate(validated)["decision"] == "reject"


def test_json_repair_retry_handles_trailing_commas_and_single_quotes():
    trailing, trailing_error = repair_json_payload('{"status":"reject","candidate_id":"x","reject_reason":"bad",}')
    single_quote, single_quote_error = repair_json_payload("{'status':'reject','candidate_id':'y','reject_reason':'bad'}")

    assert trailing_error is None
    assert trailing["candidate_id"] == "x"
    assert single_quote_error is None
    assert single_quote["candidate_id"] == "y"


def test_validate_candidate_uses_one_repair_retry():
    validated, errors = validate_candidate_payload("{'status':'reject','candidate_id':'x','reject_reason':'bad',}")
    broken, broken_errors = validate_candidate_payload("{'status':")

    assert errors == []
    assert validated["status"] == "reject"
    assert broken is None
    assert "json_repair_failed" in broken_errors
