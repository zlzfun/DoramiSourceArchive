"""Issue #143: admin receipt capacity and archival HTTP contract."""
from fastapi.testclient import TestClient
from config_bailian import BailianSpeechConfig
from tests.test_podcast_processing_api import api_env, _login

def test_tts_receipt_capacity_and_safe_reclaim_admin_routes(api_env, monkeypatch, tmp_path):
    app, _sink, _store, _snapshot, _config = api_env
    config = BailianSpeechConfig(
        api_key="test-only", account_scope="test", tts_monthly_budget_minor=100,
        tts_per_run_budget_minor=50, tts_receipt_root=str(tmp_path / "receipts"),
    )
    monkeypatch.setattr(app.podcast_speech_config_service, "resolve_config", lambda _s: config)
    client = TestClient(app.app, raise_server_exceptions=False)
    _login(client)
    response = client.get("/api/admin/podcast-tts-receipts")
    assert response.status_code == 200
    assert response.json()["status"] == "available"
    assert response.json()["used_bytes"] == 0
    assert response.json()["estimated_max_narration_reservation_bytes"] <= app.podcast_artifact_store.max_bytes
    reclaimed = client.post("/api/admin/podcast-tts-receipts/reclaim")
    assert reclaimed.status_code == 200
    assert reclaimed.json()["compressed_wavs"] == 0


