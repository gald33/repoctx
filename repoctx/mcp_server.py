import argparse
import logging
from pathlib import Path

from repoctx.retriever import get_task_context as repo_get_task_context

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - exercised in runtime environments without MCP installed
    FastMCP = None


def create_server(repo_root: str | Path | None = None):
    if FastMCP is None:
        raise RuntimeError("The 'mcp' package is required to run the MCP server.")

    resolved_root = Path(repo_root).resolve() if repo_root is not None else Path.cwd().resolve()
    server = FastMCP("repoctx")

    @server.tool()
    def get_task_context(task: str) -> dict[str, object]:
        logger.info("Building context for task '%s' in %s", task, resolved_root)
        return repo_get_task_context(task=task, repo_root=resolved_root).to_dict()

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RepoCtx MCP server")
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root to inspect (defaults to current working directory)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    create_server(repo_root=args.repo).run()


if __name__ == "__main__":
    main()
