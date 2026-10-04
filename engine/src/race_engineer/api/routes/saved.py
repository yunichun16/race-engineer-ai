"""GET /api/saved and /api/saved/{id}: the chat's saved example answers (plan section 4).

`web/scripts/saved.ts record` asks each of the chat page's example questions in a fresh
conversation and writes the answer's events to `data/saved/<id>.json`, which is never committed
(the charts in a recording carry positions derived from F1 data). `scripts/serve_data.py` bundles
the folder with the served data, and the site copies it into `public/saved/` when it builds. The
site shows a recording, marked as saved, whenever the live chat is rate-limited, at its daily cap,
paused or off.

- /api/saved: the index, `{"answers": [{"id", "question", "recorded_at", "mode"}]}`, by id.
- /api/saved/{id}: the recording, byte for byte as it was written. An id is 1-40 lowercase
  letters, digits and hyphens (422 `invalid_request` otherwise); 404 `not_found` when there is no
  such recording. Ids are looked up among the files the folder lists, never joined into a path.

The folder is RACE_ENGINEER_SAVED (default `$RACE_ENGINEER_DATA/saved`). Recordings made with the
scripted chat (any `mode` but "anthropic") are hidden unless RACE_ENGINEER_SAVED_ALLOW_FAKE=1,
which only the local profiles set, so a deployed API never offers a scripted answer as Claude's.
A file that isn't a readable recording is left out and logged. The folder's files are read once
and kept until one of them changes (`cache.memo_by_files`). Answers may be cached for an hour.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Request, Response
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from race_engineer.api.errors import error_response
from race_engineer.api.models import ErrorBody
from race_engineer.tools.cache import memo_by_files

log = logging.getLogger(__name__)

SAVED_PATH = "/api/saved"
ID_PATTERN = r"^[a-z0-9-]{1,40}$"
FORMAT_VERSION = 1  # the file's "v" (plan 4.1)
REAL_MODE = "anthropic"  # recorded through the Claude API, not the scripted chat
CACHE_CONTROL = "public, max-age=3600"

_ID = re.compile(ID_PATTERN)

router = APIRouter()


@dataclass(frozen=True)
class Recording:
    """One saved answer: what the index lists, and the file's bytes."""

    id: str
    question: str
    recorded_at: str
    mode: str
    body: bytes

    def entry(self) -> dict[str, str]:
        return {
            "id": self.id,
            "question": self.question,
            "recorded_at": self.recorded_at,
            "mode": self.mode,
        }


def _recording(path: Path) -> Recording | None:
    """The recording in `path`, or None (logged) when it isn't one: unreadable, not JSON, another
    format version, an id that doesn't match the file name, or a field missing."""
    try:
        body = path.read_bytes()
        data = json.loads(body)
    except (OSError, ValueError) as exc:
        log.warning("saved answers: %s can't be read (%s)", path.name, type(exc).__name__)
        return None
    fields = ("question", "recorded_at", "mode")
    if (
        not isinstance(data, dict)
        or data.get("v") != FORMAT_VERSION
        or data.get("id") != path.stem
        or not all(isinstance(data.get(name), str) for name in fields)
        or not isinstance(data.get("events"), list)
    ):
        log.warning("saved answers: %s isn't a v%d recording, skipped", path.name, FORMAT_VERSION)
        return None
    return Recording(path.stem, data["question"], data["recorded_at"], data["mode"], body)


@memo_by_files(lambda a: a["folder"], maxsize=4)
def _recordings(folder: Path) -> dict[str, Recording]:
    """Every readable recording in `folder` by id (none when the folder is missing)."""
    if not folder.is_dir():
        return {}
    found = (_recording(p) for p in sorted(folder.glob("*.json")) if _ID.fullmatch(p.stem))
    return {r.id: r for r in found if r is not None}


def _shown(folder: Path, allow_fake: bool) -> dict[str, Recording]:
    return {id_: r for id_, r in _recordings(folder).items() if allow_fake or r.mode == REAL_MODE}


def saved_index(folder: Path, allow_fake: bool) -> list[dict[str, str]]:
    """The recordings in `folder` the API offers, by id: each one's `id`, `question`,
    `recorded_at` and `mode`. Scripted recordings only when `allow_fake`. The chat's status
    route (`GET /api/chat/status`) lists the same answers."""
    return [r.entry() for r in _shown(folder, allow_fake).values()]


def _settings(request: Request) -> tuple[Path, bool]:
    settings = request.app.state.settings
    return settings.saved_dir, settings.saved_allow_fake


class SavedEntry(BaseModel):
    id: str = Field(examples=["monaco-2023"])
    question: str = Field(examples=["Summarise the 2023 Monaco Grand Prix."])
    recorded_at: str = Field(examples=["2026-10-04T13:05:12Z"])
    mode: str = Field(examples=[REAL_MODE])


class SavedIndex(BaseModel):
    """The saved answers on offer, by id."""

    answers: list[SavedEntry]


@router.get(SAVED_PATH, summary="List the saved example answers", response_model=SavedIndex)
async def index(request: Request, response: Response) -> Any:
    """The chat's saved example answers: each one's id, question, when it was recorded and
    the chat mode that recorded it."""
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {"answers": await anyio.to_thread.run_sync(saved_index, *_settings(request))}


@router.get(
    f"{SAVED_PATH}/{{answer_id}}",
    summary="Get a saved example answer",
    responses={
        200: {"description": "The recording (plan 4.1): the question and the chat's events"},
        404: {"model": ErrorBody, "description": "No saved answer with that id"},
        422: {"model": ErrorBody, "description": "Not an id (1-40 of a-z, 0-9 and -)"},
    },
)
async def answer(
    request: Request, answer_id: Annotated[str, PathParam(pattern=ID_PATTERN)]
) -> Response:
    """One saved answer as it was recorded: its question, the chat's events (the text, the tool
    calls and their results, with the chart data) and the model that answered."""
    found = (await anyio.to_thread.run_sync(_shown, *_settings(request))).get(answer_id)
    if found is None:
        return error_response(404, "not_found", f"No saved answer {answer_id!r}.")
    return Response(
        found.body, media_type="application/json", headers={"Cache-Control": CACHE_CONTROL}
    )
