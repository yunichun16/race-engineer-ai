"""GET /api/saved and /api/saved/{id}: the chat's saved example answers (api/routes/saved.py),
on synthetic recordings in a temporary folder."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from race_engineer.api.app import create_app
from race_engineer.api.routes.saved import CACHE_CONTROL, saved_index
from race_engineer.api.settings import Settings


def recording(answer_id: str, mode: str = "anthropic", **changes: object) -> dict:
    """A recording as web/scripts/saved.ts writes it (plan 4.1), with made-up events."""
    return {
        "v": 1,
        "id": answer_id,
        "question": f"Question {answer_id}?",
        "recorded_at": "2026-10-04T13:05:12Z",
        "mode": mode,
        "model": "claude-sonnet-5-5" if mode == "anthropic" else "scripted",
        "prompt_version": "test",
        "data": {"latest": None, "results_run": None},
        "events": [{"type": "text", "delta": "An answer."}, {"type": "done", "history": []}],
    } | changes


@pytest.fixture
def saved(tmp_path: Path) -> Path:
    folder = tmp_path / "saved"
    folder.mkdir()
    for answer in (recording("monaco-2023"), recording("baku-sc"), recording("latest", "fake")):
        (folder / f"{answer['id']}.json").write_text(json.dumps(answer, indent=2))
    (folder / "broken.json").write_text("{not json")
    (folder / "renamed.json").write_text(json.dumps(recording("another-id")))
    (folder / "old-format.json").write_text(json.dumps(recording("old-format", v=0)))
    (folder / "no-events.json").write_text(json.dumps(recording("no-events", events=None)))
    (folder / "Not_An_Id.json").write_text(json.dumps(recording("Not_An_Id")))
    (folder / "notes.txt").write_text("not a recording")
    (tmp_path / "secret.json").write_text(json.dumps(recording("secret")))  # outside the folder
    return folder


def client_for(tmp_path: Path, saved: Path, allow_fake: bool) -> TestClient:
    settings = Settings(
        processed=tmp_path / "processed",
        results=tmp_path / "results",
        chat="off",
        mcp_host_check=False,
        saved_dir=saved,
        saved_allow_fake=allow_fake,
    )
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path, saved: Path) -> Iterator[TestClient]:
    with client_for(tmp_path, saved, allow_fake=False) as client:
        yield client


def test_the_index_lists_claudes_recordings_by_id(client: TestClient) -> None:
    response = client.get("/api/saved")
    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE_CONTROL
    assert response.json() == {
        "answers": [
            {
                "id": answer_id,
                "question": f"Question {answer_id}?",
                "recorded_at": "2026-10-04T13:05:12Z",
                "mode": "anthropic",
            }
            for answer_id in ("baku-sc", "monaco-2023")
        ]
    }


def test_a_recording_is_served_byte_for_byte(client: TestClient, saved: Path) -> None:
    response = client.get("/api/saved/monaco-2023")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == CACHE_CONTROL
    assert response.content == (saved / "monaco-2023.json").read_bytes()


@pytest.mark.parametrize("answer_id", ["Monaco", "a_b", "x" * 41, "monaco.json", "%2E%2E"])
def test_a_bad_id_is_422(client: TestClient, answer_id: str) -> None:
    response = client.get(f"/api/saved/{answer_id}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "answer_id",
    ["missing", "broken", "renamed", "another-id", "old-format", "no-events", "notes", "secret"],
)
def test_a_missing_or_unreadable_recording_is_404(client: TestClient, answer_id: str) -> None:
    response = client.get(f"/api/saved/{answer_id}")
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not_found",
        "message": f"No saved answer {answer_id!r}.",
        "tool": None,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/saved/..%2Fsecret",
        "/api/saved/%2E%2E%2Fsecret",
        "/api/saved/..%2F..%2Fsaved%2Fmonaco-2023",
        "/api/saved/%2Fetc%2Fpasswd",
    ],
)
def test_no_path_leaves_the_folder(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code in (404, 422)
    assert b"Question" not in response.content


def test_scripted_recordings_are_hidden_unless_allowed(
    client: TestClient, tmp_path: Path, saved: Path
) -> None:
    assert client.get("/api/saved/latest").status_code == 404
    with client_for(tmp_path, saved, allow_fake=True) as allowed:
        ids = [a["id"] for a in allowed.get("/api/saved").json()["answers"]]
        assert ids == ["baku-sc", "latest", "monaco-2023"]
        found = allowed.get("/api/saved/latest")
        assert found.status_code == 200 and found.json()["mode"] == "fake"


def test_saved_index_reads_changes_and_a_missing_folder(saved: Path, tmp_path: Path) -> None:
    assert saved_index(tmp_path / "nowhere", allow_fake=True) == []
    assert [a["id"] for a in saved_index(saved, allow_fake=False)] == ["baku-sc", "monaco-2023"]
    (saved / "baku-sc.json").unlink()
    (saved / "new-one.json").write_text(json.dumps(recording("new-one")))
    assert [a["id"] for a in saved_index(saved, allow_fake=False)] == ["monaco-2023", "new-one"]
    assert set(saved_index(saved, allow_fake=False)[0]) == {"id", "question", "recorded_at", "mode"}
