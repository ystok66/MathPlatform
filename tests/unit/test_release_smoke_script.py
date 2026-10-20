from pathlib import Path
import re


def test_release_smoke_phase_script_is_split_phase_not_start_stop_or_pause():
    script = Path("scripts/smoke/release_smoke_phase.ps1").read_text(encoding="utf-8")

    assert 'ValidateSet("preflight", "after-start", "doctor", "after-stop")' in script
    assert "[string]$DataDir" in script
    assert "[string]$SmokeProfile" in script
    assert "--smoke-profile" in script
    assert "--data-dir" in script
    assert '"status", "--json"' in script
    assert '"doctor", "--json"' in script
    assert '"start"' not in script
    assert '"stop"' not in script
    assert re.search(r"(?im)^\s*pause\s*$", script) is None


def test_release_business_smoke_is_bounded_api_only_flow():
    script = Path("scripts/smoke/release_business_smoke.ps1").read_text(encoding="utf-8")

    assert "/api/import/json-files?max_questions=1" in script
    assert "/api/practice/sessions" in script
    assert "/api/reports/summary" in script
    assert "/api/history/groups" in script
    assert "/api/generation/batches" in script
    assert "use_mock = $true" in script
    assert "candidate_count = 1" in script
    assert "/api/admin/history/export" in script
    assert "/api/admin/history/import?dry_run=true" in script
    assert "/api/admin/question-bank/export" in script
    assert "/api/admin/question-bank/import?dry_run=true" in script
    assert "Get-StableTransferHash" in script
    assert "history_transfer_dry_run_hash_mismatch" in script
    assert "question_bank_transfer_dry_run_hash_mismatch" in script
    assert "Invoke-RestMethod" in script
    assert '"start"' not in script
    assert '"stop"' not in script
    assert re.search(r"(?im)^\s*pause\s*$", script) is None
