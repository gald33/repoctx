import argparse
import logging
import sys

from repoctx.commands import COMMAND_HANDLERS, COMMAND_MODULES, SUBCOMMAND_NAMES
from repoctx.commands.experiment import EXPERIMENT_SUBCOMMANDS
from repoctx.experiment_mcp import refresh_after_cli_invocation

logger = logging.getLogger(__name__)

SUBCOMMANDS = SUBCOMMAND_NAMES
HELP_USAGE = """repoctx [-h] TASK
       repoctx [-h] COMMAND ..."""
HELP_EPILOG = """Default behavior:
  If the first argument is not a subcommand, RepoCtx treats it as `query`.

Examples:
  repoctx "refactor the auth middleware to support OAuth"
  repoctx query "show me tests related to the billing webhook flow" --repo /path/to/repo --format json
  repoctx index --repo /path/to/repo
  repoctx experiment
  repoctx experiment "refactor the auth middleware to support OAuth"

Common workflows:
  Use `repoctx TASK` for the default query shorthand.
  Use `repoctx query TASK [flags]` when you need query-specific options.
  Use `repoctx experiment` to launch or resume the guided experiment flow.
  Run `repoctx COMMAND --help` for command-specific flags and examples.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local repository intelligence for coding agents",
        usage=HELP_USAGE,
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="command",
        title="Common subcommands",
        metavar="COMMAND",
        description="Use `repoctx COMMAND --help` for command-specific usage.",
    )
    for module in COMMAND_MODULES:
        module.register(sub)
    return parser


def main() -> None:
    _ensure_default_subcommand()
    parser = build_parser()
    args = parser.parse_args()
    refresh_after_cli_invocation()
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING)

    # No-op on stable; prints a one-time stderr disclosure on canary builds
    # (and only on the very first invocation per install).
    try:
        from repoctx import reporting

        reporting.maybe_show_canary_notice()
    except Exception:  # noqa: BLE001 — disclosure must never break the CLI
        pass

    cmd = args.command or "query"
    handler = COMMAND_HANDLERS.get(cmd)
    if handler is None:
        parser.print_help()
        raise SystemExit(1)
    handler(args)


def _ensure_default_subcommand() -> None:
    """If the first non-flag arg is not a known subcommand, insert 'query'."""
    if len(sys.argv) < 2:
        return
    first = sys.argv[1]
    if first == "experiment":
        idx = 2
        while idx < len(sys.argv):
            token = sys.argv[idx]
            if token in EXPERIMENT_SUBCOMMANDS or token in {"-h", "--help"}:
                return
            if token == "--repo":
                idx += 2
                continue
            if token.startswith("-"):
                idx += 1
                continue
            sys.argv.insert(idx, "start")
            return
        return
    if first not in SUBCOMMANDS and not first.startswith("-"):
        sys.argv.insert(1, "query")


if __name__ == "__main__":
    main()
