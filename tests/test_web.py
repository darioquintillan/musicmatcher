import asyncio
import json
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from musicmatcher.config_manager import Settings, load_settings, save_settings
from musicmatcher.mapping_manager import load_state, save_state
from musicmatcher.service import Workspace
from musicmatcher.web import create_app


@pytest.fixture
def client(tmp_path):
    workspace = Workspace(tmp_path / "workspace", Settings(output_dir=str(tmp_path / "audio")))
    with TestClient(create_app(workspace=workspace)) as browser:
        browser.headers["X-MM-Token"] = browser.get("/api/bootstrap").json()["csrf_token"]
        yield browser, workspace


def wait(client, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/api/status").json()
        if response["job"]["status"] != "running":
            return response
        time.sleep(0.01)
    pytest.fail("Worker did not complete")


def import_example(client):
    response = client.post("/api/jobs", json={"action": "import_file", "filename": "example.csv",
                                            "content": "title,artists,duration_seconds\nSong,Artist,180\nSong,Artist,180\n"})
    assert response.status_code == 202
    result = wait(client)
    assert result["job"]["status"] == "completed", result
    return result


def test_local_ui_config_import_review_exports(client):
    browser, workspace = client
    assert browser.get("/").status_code == 200
    assert browser.get("/assets/app.js").status_code == 200
    assert browser.get("/").headers["x-frame-options"] == "DENY"
    bootstrap = browser.get("/api/bootstrap").json()
    assert bootstrap["config"]["candidate_limit"] == 8
    assert bootstrap["config"]["matching_mode"] == "relaxed"
    assert "api_key" not in bootstrap["config"]
    assert browser.post("/api/config", json={"candidate_limit": 3}).status_code == 200
    assert load_settings(workspace.data_dir).candidate_limit == 3
    assert browser.post("/api/config", json={"candidate_limit": 0}).status_code == 422
    assert browser.post("/api/config", json={"api_key": "not-a-supported-field"}).status_code == 422
    assert import_example(browser)["catalog"]["total"] == 2
    listing = browser.get("/api/tracks?limit=1").json()
    assert len(listing["tracks"]) == 1 and listing["total"] == 2
    key = listing["tracks"][0]["key"]
    url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert browser.post("/api/review", json={"key": key, "url": "https://evil.test/video"}).status_code == 400
    assert browser.post("/api/review", json={"key": key, "url": url}).status_code == 200
    assert all(t["match"]["manual"] for t in workspace.state()["tracks"])
    assert all(not t["authorization"] for t in workspace.state()["tracks"])
    assert browser.post("/api/authorize", json={"key": key, "note": "Own test audio"}).status_code == 200
    exported = browser.get("/api/export/mapping").json()
    assert len(exported["tracks"]) == 2
    assert len({t["id"] for t in exported["tracks"]}) == 2
    assert all(t["youtube_id"] == "AAAAAAAAAAA" for t in exported["tracks"])
    assert len(browser.get("/api/export/links").json()) == 2
    assert browser.delete("/api/catalog").status_code == 200
    assert browser.get("/api/status").json()["catalog"]["total"] == 0


def test_cross_origin_and_missing_token_are_rejected(client):
    browser, _workspace = client
    token = browser.headers.pop("X-MM-Token")
    assert browser.post("/api/config", json={"candidate_limit": 1}).status_code == 403
    browser.headers["X-MM-Token"] = token
    assert browser.post("/api/config", json={"candidate_limit": 1},
                        headers={"Origin": "https://evil.test"}).status_code == 403
    assert browser.get("/api/bootstrap", headers={"Host": "evil.test"}).status_code == 400


def test_pause_resume_and_busy_controls_receive_real_progress(client):
    browser, workspace = client
    import_example(browser)
    entered = threading.Event()

    def search(path, *, settings, progress, stop_event, **kwargs):
        assert settings.candidate_limit == 8
        progress({"stage": "search", "done": 0, "total": 2, "current_title": "Song",
                  "candidate_done": 1, "candidate_total": 8, "message": "Inspecting real test candidate"})
        entered.set()
        assert stop_event.wait(timeout=2)
        return load_state(path)

    with patch("musicmatcher.youtube_search_agent.search_tracks", side_effect=search):
        assert browser.post("/api/jobs", json={"action": "search"}).status_code == 202
        assert entered.wait(timeout=1)
        status = browser.get("/api/status").json()
        assert status["job"]["candidate_done"] == 1
        assert status["job"]["candidate_total"] == 8
        assert browser.post("/api/config", json={"candidate_limit": 10}).status_code == 409
        assert browser.post("/api/jobs", json={"action": "search"}).status_code == 409
        assert browser.delete("/api/catalog").status_code == 409
        assert browser.post("/api/jobs/stop", json={}).status_code == 200
        assert wait(browser)["job"]["status"] == "paused"
    assert workspace.state_path.is_file()


def test_sse_first_event_is_snapshot_not_fake_progress(client):
    browser, _workspace = client
    route = next(route for route in browser.app.routes if getattr(route, "path", None) == "/api/events")

    class Connected:
        async def is_disconnected(self):
            return False

    async def first_event():
        response = await route.endpoint(Connected())
        iterator = response.body_iterator
        try:
            event = await anext(iterator)
            payload = json.loads(event.split("data: ", 1)[1])
            assert payload["job"]["status"] == "idle"
            assert payload["catalog"]["total"] == 0
        finally:
            await iterator.aclose()

    asyncio.run(first_event())


def test_cannot_start_processing_without_catalog(client):
    browser, _workspace = client
    assert browser.post("/api/jobs", json={"action": "search"}).status_code == 400


def test_first_track_limit_is_validated_and_forwarded_to_worker(client):
    browser, workspace = client
    import_example(browser)
    assert browser.post("/api/jobs", json={"action": "search", "track_limit": 0}).status_code == 422
    with patch("musicmatcher.youtube_search_agent.search_tracks", return_value=workspace.state()) as search:
        assert browser.post("/api/jobs", json={"action": "search", "track_limit": 1}).status_code == 202
        assert wait(browser)["job"]["status"] == "completed"
    assert search.call_args.kwargs["track_limit"] == 1
    assert len(workspace.state()["tracks"]) == 2


def test_configuration_persists_without_any_model_secrets(tmp_path):
    settings = Settings(candidate_limit=25, output_dir=str(tmp_path / "audio"), spotify_client_id="public-client")
    save_settings(tmp_path, settings)
    assert load_settings(tmp_path) == settings
    assert "api_key" not in (tmp_path / "settings.json").read_text()
    with pytest.raises(ValueError):
        Settings(candidate_limit=26)


def test_spotify_disconnect_preserves_other_sources_and_removes_tokens(client):
    browser, workspace = client
    import_example(browser)
    state = workspace.state()
    state["tracks"][0].update(source="spotify", spotify_id="A" * 22)
    state["tracks"][1]["match"] = {"status": "accepted", "manual": True,
                                    "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"}
    save_state(workspace.state_path, state)
    cache = workspace.data_dir / ".spotify-cache-test"
    cache.write_text('{"refresh_token":"test-token"}', encoding="utf-8")
    assert browser.post("/api/spotify/disconnect").status_code == 200
    remaining = workspace.state()["tracks"]
    assert len(remaining) == 1 and remaining[0]["match"]["manual"]
    assert not cache.exists()
