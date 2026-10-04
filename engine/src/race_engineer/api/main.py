"""`race-engineer-api`: run the API with uvicorn.

    uv run race-engineer-api                 # http://127.0.0.1:8000, docs at /docs
    uv run race-engineer-api --reload        # restart on code changes (make api)
    RACE_ENGINEER_CHAT=fake uv run race-engineer-api   # the scripted chat (make api-fake)
    race-engineer-api --host 0.0.0.0 --no-access-log   # in a container (engine/Dockerfile)

The port defaults to $PORT when it is set (container hosts such as Cloud Run set it), else 8000.
--no-access-log turns off uvicorn's line per request, which prints each client's address.
uvicorn's own X-Forwarded-For handling is off: by default it would take the client address from
that header on any request from 127.0.0.1, so a local client could pick its own. The address the
chat's limits count comes from the connection, or from the header only as far as
RACE_ENGINEER_TRUSTED_PROXY_HOPS says (api/limits/identity.py).
The other settings come from environment variables (api/settings.py).
"""

from __future__ import annotations

import argparse
import copy
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

APP = "race_engineer.api.app:create_app"
PACKAGE_DIR = Path(__file__).resolve().parents[1]  # src/race_engineer
DEFAULT_PORT = 8000


def logging_config() -> dict[str, Any]:
    """uvicorn's logging, plus the app's own INFO lines (startup times, the chat's mode) in the
    same format. Passed to uvicorn so the reloader's worker process gets it too."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["loggers"]["race_engineer"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    return config


def default_port(environ: Mapping[str, str] | None = None) -> int:
    """$PORT when it is set, else 8000. ValueError names PORT when it isn't a port number."""
    value = (os.environ if environ is None else environ).get("PORT", "").strip()
    if not value:
        return DEFAULT_PORT
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise ValueError(f"PORT must be a port number (1-65535), not {value!r}.")
    return int(value)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Race Engineer AI API")
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="default $PORT, else 8000")
    parser.add_argument("--reload", action="store_true", help="restart when the code changes")
    parser.add_argument(
        "--no-access-log", action="store_true", help="no line per request (it holds the client IP)"
    )
    args = parser.parse_args(argv)
    if args.port is None:
        try:
            args.port = default_port()
        except ValueError as exc:
            parser.error(str(exc))

    # The app reads its settings from the environment, in the reloader's worker process too;
    # the host decides whether /mcp accepts only localhost Host headers.
    os.environ["RACE_ENGINEER_HOST"] = args.host
    uvicorn.run(
        APP,
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(PACKAGE_DIR)] if args.reload else None,
        log_config=logging_config(),
        access_log=not args.no_access_log,
        proxy_headers=False,  # the limits read X-Forwarded-For themselves, if trusted
    )


if __name__ == "__main__":
    main()
