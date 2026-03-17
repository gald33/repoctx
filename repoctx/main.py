import argparse
import json
import logging
from pathlib import Path

from repoctx.retriever import get_task_context


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local repository intelligence for coding agents")
    parser.add_argument("task", help="Task description to retrieve context for")
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root to inspect (defaults to current directory)",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        response = get_task_context(task=args.task, repo_root=Path(args.repo))
    except Exception as exc:  # pragma: no cover - exercised in CLI runtime
        logging.getLogger(__name__).error("repoctx failed: %s", exc)
        raise SystemExit(1) from exc

    if args.format == "json":
        print(json.dumps(response.to_dict(), indent=2))
        return

    print(response.context_markdown)


if __name__ == "__main__":
    main()
