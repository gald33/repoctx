"""autoprovision subcommand — the cloud-session provisioning, run synchronously.

Same sequence the MCP server runs in the background in remote sessions
(install the [embeddings] extra into this interpreter's env, record consent,
build/refresh the index), exposed for environment setup scripts and manual
runs. Running it from the CLI is an explicit request, so the remote-only env
gate does not apply; a recorded "declined" consent is still respected (use
`repoctx index` to change that answer).
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace


def _register(subparsers) -> None:
    ap = subparsers.add_parser(
        "autoprovision",
        help=(
            "Install embedding deps into this Python and build the index — "
            "what cloud sessions do automatically in the background"
        ),
    )
    ap.add_argument("--repo", default=".", help="Repository root")
    ap.add_argument("--verbose", action="store_true")


def _run(args: argparse.Namespace) -> None:
    from repoctx.autoprovision import _provision, provisioning_state

    repo = Path(args.repo).resolve()
    status = _provision(repo)
    print(json.dumps({"status": status, "state": provisioning_state(repo)}, indent=2))
    if status not in ("ready", "declined"):
        raise SystemExit(1)


autoprovision_cmd = SimpleNamespace(NAME="autoprovision", register=_register, run=_run)
