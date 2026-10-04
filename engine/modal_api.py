"""Deploy the API (the tools, the chat and /mcp) to Modal (plan M7 section 6).

These are the user's steps, run with your own Modal account from your own terminal, with
ANTHROPIC_BASE_URL unset (docs/DEPLOY.md has the whole sequence):

    make serve-data                                # data/serve: the served files, checked
    make serve-upload                              # copy data/serve to the Modal volume
    make deploy-api [SITE_ORIGIN=https://<project>.vercel.app] [PREVIEW_REGEX=<regex>]

Behind the targets (run from engine/):

    uv run --group cloud python modal_api.py upload [../data/serve] [--dry-run]
    uv run --group cloud modal deploy modal_api.py       # after `make mcp-app`

The app `race-engineer-api` serves `create_app()` as one ASGI web endpoint, labelled
`race-engineer-api`, so its URL is https://<workspace>--race-engineer-api.modal.run. It reads:
- the served-data bundle on the volume `race-engineer-serve`, mounted read-only at /data
  (`upload` writes /processed, /results, /saved and /SERVE_MANIFEST.json);
- the secret `race-engineer-api`: ANTHROPIC_API_KEY, RACE_ENGINEER_CHAT_SECRET,
  UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN, which you create in Modal's dashboard;
- an environment baked into the image (`serve_env`): the data at /data, a public host, stateless
  MCP, the limits on with the Upstash store, and the browser origins allowed by CORS:
  SITE_ORIGIN from the deploying shell, or none at all before the site exists.

One container at most (`max_containers=1`: the in-process rate limits and the MCP server stay in
one place), scaled to zero after 5 idle minutes. The resources are requests, not limits: 0.5 CPU
core, which it may exceed while a heavy tool runs (a CPU limit applies only when a (request,
limit) pair is given), and 2 GiB of memory, which it may exceed up to 4 GiB before Modal restarts
it. Modal is expected to bill the higher of the request and the use (check modal.com/pricing).

Deploy-time checks run only when the modal CLI deploys, serves or runs this file from your
machine: they refuse while ANTHROPIC_BASE_URL is set (a terminal whose chat traffic goes through
a gateway), while a chart bundle is missing (`make mcp-app`), or with a SITE_ORIGIN or
PREVIEW_REGEX that can't be used. Containers import this file too, without the deploying shell's
variables, so the baked values are read back from their own environment, the image comes out the
same, and only the standard library and modal are imported at the top.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import modal

ENGINE = Path(__file__).resolve().parent
APP_NAME = LABEL = SECRET_NAME = "race-engineer-api"
VOLUME_NAME = "race-engineer-serve"
MOUNT = "/data"  # RACE_ENGINEER_DATA inside the container
# Nested like the repo, because config.repo_root() walks up three levels from config.py.
REMOTE_SRC = "/app/engine/src"
UI_DIR = ENGINE / "src" / "race_engineer" / "mcp_server" / "ui"
PLACEHOLDER_TEXT = "Chart bundle not built"  # mcp_server/server.py's stand-in page
LOCAL_BUNDLE = ENGINE.parent / "data" / "serve"
MANIFEST = "SERVE_MANIFEST.json"  # written last by scripts/serve_data.py
BUNDLE_DIRS = ("processed", "results", "saved")  # each uploaded to /<name> on the volume
REAL_MODE = "anthropic"  # a saved answer recorded through the Claude API
DEPLOY_COMMANDS = {"deploy", "serve", "run", "shell"}  # modal CLI commands that start the app


def serve_env(cors_origins: str, origin_regex: str = "") -> dict[str, str]:
    """The container's environment: an explicit list, never a copy of the deploying shell's
    (which holds your key). `cors_origins` is RACE_ENGINEER_CORS_ORIGINS ("none": no browser
    origin); `origin_regex`, when set, also allows the origins it matches (Vercel previews)."""
    env = {
        "RACE_ENGINEER_DATA": MOUNT,
        # Not loopback: the MCP SDK then accepts *.modal.run Host headers, and the limits apply.
        "RACE_ENGINEER_HOST": "0.0.0.0",
        "RACE_ENGINEER_MCP_STATELESS": "1",
        "RACE_ENGINEER_LIMITS": "on",
        # Never the memory store, which a cold start would reset: no Upstash, no chat.
        "RACE_ENGINEER_LIMITS_STORE": "upstash",
        "RACE_ENGINEER_CORS_ORIGINS": cors_origins,
        "PYTHONUNBUFFERED": "1",
    }
    if origin_regex:
        env["RACE_ENGINEER_CORS_ORIGIN_REGEX"] = origin_regex
    return env


def cors_settings(environ: Mapping[str, str], local: bool) -> tuple[str, str]:
    """(RACE_ENGINEER_CORS_ORIGINS, RACE_ENGINEER_CORS_ORIGIN_REGEX) for the image: on your
    machine from SITE_ORIGIN and PREVIEW_REGEX; in a container, the values the image was built
    with, read back from its environment, so the image definition comes out the same."""
    if local:
        sites = [o.strip().rstrip("/") for o in environ.get("SITE_ORIGIN", "").split(",")]
        return ",".join(s for s in sites if s) or "none", environ.get("PREVIEW_REGEX", "").strip()
    return (
        environ.get("RACE_ENGINEER_CORS_ORIGINS", "none"),
        environ.get("RACE_ENGINEER_CORS_ORIGIN_REGEX", ""),
    )


def modal_command(argv: Sequence[str]) -> str | None:
    """The modal CLI command loading this file when it starts the app (`modal deploy`, `serve`,
    `run` or `shell`), else None: imported by Python, tests, `python modal_api.py upload`."""
    if len(argv) < 2 or Path(argv[0]).name not in {"modal", "__main__.py"}:
        return None
    return argv[1] if argv[1] in DEPLOY_COMMANDS else None


def chart_bundles() -> tuple[str, ...]:
    """The chart bundles the MCP server serves (`mcp_server.server.UI_BUNDLES`)."""
    from race_engineer.mcp_server.server import UI_BUNDLES

    return tuple(UI_BUNDLES)


def deploy_problems(environ: Mapping[str, str], ui_dir: Path = UI_DIR) -> list[str]:
    """Why this machine shouldn't deploy the API now (empty: go ahead)."""
    problems = []
    if environ.get("ANTHROPIC_BASE_URL", "").strip():
        problems.append(
            "ANTHROPIC_BASE_URL is set: deploy from your own terminal with it unset "
            "(`unset ANTHROPIC_BASE_URL`; docs/DEPLOY.md)"
        )
    origins, regex = cors_settings(environ, local=True)
    if origins != "none":
        for origin in origins.split(","):
            if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?", origin):
                problems.append(
                    f"SITE_ORIGIN must be the site's origin, https://<host> with no path, "
                    f"not {origin!r}"
                )
    if regex:
        try:
            re.compile(regex)
        except re.error as exc:
            problems.append(f"PREVIEW_REGEX isn't a regular expression ({exc})")
    for name in chart_bundles():
        page = ui_dir / f"{name}.html"
        if not page.is_file() or page.stat().st_size == 0:
            problems.append(f"chart bundle {page.name} not built: run `make mcp-app`")
        elif PLACEHOLDER_TEXT in page.read_text(encoding="utf-8", errors="replace"):
            problems.append(f"chart bundle {page.name} is the placeholder: run `make mcp-app`")
    return problems


LOCAL = modal.is_local()
CORS_ORIGINS, ORIGIN_REGEX = cors_settings(os.environ, LOCAL)
if LOCAL and modal_command(sys.argv) is not None:
    _problems = deploy_problems(os.environ)
    if _problems:
        sys.exit("modal_api.py: not deploying:\n" + "\n".join(f"- {p}" for p in _problems))
    if CORS_ORIGINS == "none":
        print(
            "modal_api.py: no SITE_ORIGIN, so no browser origin may call the API yet. Once the "
            "site is up: make deploy-api SITE_ORIGIN=https://<project>.vercel.app"
        )

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME)  # created by `upload`; a deploy needs it
image = (
    modal.Image.debian_slim(python_version="3.13")
    # The serving dependencies only: no training group (torch, MLflow, FastF1), no dev tools;
    # the uv version that wrote uv.lock. uv_sync installs no project: the source comes next.
    .uv_sync(str(ENGINE), extra_options="--no-default-groups", uv_version="0.12.19")
    .env(serve_env(CORS_ORIGINS, ORIGIN_REGEX))
    # With the chart bundles in mcp_server/ui/ (the training image leaves them out).
    .add_local_dir(
        ENGINE / "src" / "race_engineer",
        f"{REMOTE_SRC}/race_engineer",
        ignore=["**/__pycache__", "**/*.pyc"],
    )
)


@app.function(
    image=image,
    volumes={MOUNT: volume.read_only()},
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    cpu=0.5,  # a request: a heavy tool may use more
    memory=(2048, 4096),  # (request, limit) in MiB: billed past 2 GiB, restarted past 4 GiB
    min_containers=0,
    max_containers=1,
    scaledown_window=300,
    timeout=150,  # Modal's ceiling for a web request; a question stops at 120 s
)
@modal.concurrent(max_inputs=32)
@modal.asgi_app(label=LABEL)
def api() -> Any:
    """The API as `race-engineer-api` serves it locally; Modal runs its lifespan (the warm-up,
    the chat client, the MCP session manager)."""
    if REMOTE_SRC not in sys.path:
        sys.path.insert(0, REMOTE_SRC)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # no request URLs in the log (plan 13.1)
    from race_engineer.api.app import create_app

    return create_app()


# --- upload: the served-data bundle to the volume -----------------------------------------------


def bundle_problems(bundle: Path) -> list[str]:
    """Why `bundle` (made by scripts/serve_data.py) shouldn't be uploaded (empty: go ahead)."""
    if not (bundle / MANIFEST).is_file():
        return [f"{bundle} has no {MANIFEST} (an unfinished or older bundle?): `make serve-data`"]
    problems = []
    try:
        manifest = json.loads((bundle / MANIFEST).read_text())
    except (OSError, ValueError) as exc:
        return [f"{bundle / MANIFEST} can't be read ({type(exc).__name__}): `make serve-data`"]
    for name in ("processed", "results"):
        if not (bundle / name).is_dir():
            problems.append(f"{bundle} has no {name}/: `make serve-data`")
    scripted = []
    for path in sorted((bundle / "saved").glob("*.json")):
        try:
            mode = json.loads(path.read_text()).get("mode")
        except (OSError, ValueError, AttributeError):
            mode = None
        if mode != REAL_MODE:
            scripted.append(path.stem)
    if scripted:
        problems.append(
            f"{bundle / 'saved'} holds scripted answers ({', '.join(scripted)}): record real "
            "ones (`make saved-record`), then `make serve-data` without ALLOW_FAKE"
        )
    elif manifest.get("allow_fake"):
        problems.append(f"{bundle} was built with --allow-fake: `make serve-data` without it")
    return problems


def upload_plan(bundle: Path) -> list[tuple[Path, str, int, int]]:
    """What `upload` sends: (local path, path on the volume, files, bytes) for each part, a
    missing folder (saved/ before any answer is recorded) as 0 files."""
    plan = []
    for name in BUNDLE_DIRS:
        folder = bundle / name
        files = [p for p in folder.rglob("*") if p.is_file()] if folder.is_dir() else []
        plan.append((folder, f"/{name}", len(files), sum(p.stat().st_size for p in files)))
    manifest = bundle / MANIFEST
    plan.append((manifest, f"/{MANIFEST}", 1, manifest.stat().st_size))
    return plan


def upload(bundle: Path, dry_run: bool) -> None:
    problems = bundle_problems(bundle)
    if problems:
        sys.exit("modal_api.py: not uploading:\n" + "\n".join(f"- {p}" for p in problems))
    plan = upload_plan(bundle)
    action = "would upload" if dry_run else "uploading"
    total = sum(size for *_, size in plan)
    print(f"{action} {bundle} ({total / 1e9:.2f} GB) to the Modal volume {VOLUME_NAME}:")
    for local, remote, files, size in plan:
        print(f"  {remote:<22} {files:>6,} file(s) {size / 1e6:>10,.1f} MB  from {local}")
    saved = dict((remote, files) for _, remote, files, _ in plan)["/saved"]
    if not saved:
        print(
            "no saved answers: the site will have none to show while the chat is limited "
            "(`make saved-record`, then `make serve-data`, to add them)"
        )
    if dry_run:
        print("dry run: nothing uploaded, no connection made")
        return
    target = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
    with target.batch_upload(force=True) as batch:
        for local, remote, _, _ in plan:
            if local.is_dir():
                batch.put_directory(local, remote)
            elif local.is_file():
                batch.put_file(local, remote)
    print(
        f"uploaded {sum(files for _, _, files, _ in plan):,} files. Files deleted locally stay "
        f"on the volume (docs/DEPLOY.md); `make deploy-api` restarts the API on the new data."
    )


def cli(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python modal_api.py", description="The served data on the Modal volume."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    where = ", ".join(f"<bundle>/{d} to /{d}" for d in BUNDLE_DIRS)
    up = commands.add_parser(
        "upload",
        help="copy a served-data bundle (make serve-data) to the volume",
        description=f"Copy a served-data bundle (make serve-data) to the Modal volume "
        f"{VOLUME_NAME}, created if missing: {where}, and <bundle>/{MANIFEST} to /{MANIFEST}. "
        "Refuses a bundle without its manifest or with scripted saved answers.",
    )
    up.add_argument(
        "bundle", type=Path, nargs="?", default=LOCAL_BUNDLE, help="default: ../data/serve"
    )
    up.add_argument(
        "--dry-run", action="store_true", help="check the bundle and list what would be sent"
    )
    args = parser.parse_args(argv)
    if args.command == "upload":
        upload(args.bundle, args.dry_run)


if __name__ == "__main__":
    cli()
