"""Install / authority-scaffold subcommands."""

import argparse
import json
from types import SimpleNamespace


# -- install ------------------------------------------------------------------

def _register_install_all(subparsers) -> None:
    ia_all = subparsers.add_parser(
        "install",
        help="One-shot setup: install all harness adapters + scaffold authority layout",
    )
    ia_all.add_argument("--repo", default=".", help="Repository root")
    ia_all.add_argument(
        "--no-scaffold",
        action="store_true",
        help="Skip the contracts/docs/examples scaffold (just register MCP entries)",
    )
    ia_all.add_argument(
        "--no-claude-md-nudge",
        dest="claude_md_nudge",
        action="store_false",
        default=True,
        help=(
            "Skip inserting the anchored repoctx-nudge block into CLAUDE.md. "
            "Also disabled by setting REPOCTX_NO_CLAUDE_MD_NUDGE=1."
        ),
    )
    ia_all_index = ia_all.add_mutually_exclusive_group()
    ia_all_index.add_argument(
        "--no-index",
        dest="build_index",
        action="store_false",
        default=None,
        help="Skip building the embedding index (default: build iff [embeddings] extras are installed)",
    )
    ia_all_index.add_argument(
        "--with-index",
        dest="build_index",
        action="store_true",
        help="Force-build the embedding index (errors if [embeddings] extras are missing)",
    )


def _run_install_all(args: argparse.Namespace) -> None:
    from repoctx.harness import install_all

    result = install_all(
        repo_root=args.repo,
        scaffold_authority=not args.no_scaffold,
        build_index=args.build_index,
        claude_md_nudge=getattr(args, "claude_md_nudge", True),
    )
    print(json.dumps(result, indent=2))


install_all_cmd = SimpleNamespace(NAME="install", register=_register_install_all, run=_run_install_all)


# -- install-claude-code ------------------------------------------------------

def _register_install_claude_code(subparsers) -> None:
    ic = subparsers.add_parser(
        "install-claude-code",
        help="Install AGENTS.md section + .mcp.json entry for repoctx (v2)",
    )
    ic.add_argument("--repo", default=".", help="Repository root")
    ic.add_argument(
        "--no-claude-md-nudge",
        dest="claude_md_nudge",
        action="store_false",
        default=True,
        help=(
            "Skip inserting the anchored repoctx-nudge block into CLAUDE.md. "
            "Also disabled by setting REPOCTX_NO_CLAUDE_MD_NUDGE=1."
        ),
    )


def _run_install_claude_code(args: argparse.Namespace) -> None:
    from repoctx.harness import install_claude_code

    result = install_claude_code(
        repo_root=args.repo,
        claude_md_nudge=getattr(args, "claude_md_nudge", True),
    )
    print(json.dumps(result.to_dict(), indent=2))


install_claude_code_cmd = SimpleNamespace(
    NAME="install-claude-code", register=_register_install_claude_code, run=_run_install_claude_code,
)


# -- install-cursor -----------------------------------------------------------

def _register_install_cursor(subparsers) -> None:
    icu = subparsers.add_parser(
        "install-cursor",
        help="Install AGENTS.md section + .cursor/mcp.json entry for repoctx (v2)",
    )
    icu.add_argument("--repo", default=".", help="Repository root")


def _run_install_cursor(args: argparse.Namespace) -> None:
    from repoctx.harness import install_cursor

    result = install_cursor(repo_root=args.repo)
    print(json.dumps(result.to_dict(), indent=2))


install_cursor_cmd = SimpleNamespace(
    NAME="install-cursor", register=_register_install_cursor, run=_run_install_cursor,
)


# -- install-codex ------------------------------------------------------------

def _register_install_codex(subparsers) -> None:
    ico = subparsers.add_parser(
        "install-codex",
        help="Install AGENTS.md section + .codex/mcp.json entry for repoctx (v2)",
    )
    ico.add_argument("--repo", default=".", help="Repository root")


def _run_install_codex(args: argparse.Namespace) -> None:
    from repoctx.harness import install_codex

    result = install_codex(repo_root=args.repo)
    print(json.dumps(result.to_dict(), indent=2))


install_codex_cmd = SimpleNamespace(
    NAME="install-codex", register=_register_install_codex, run=_run_install_codex,
)


# -- init-authority -----------------------------------------------------------

def _register_init_authority(subparsers) -> None:
    ia = subparsers.add_parser(
        "init-authority",
        help="Scaffold contracts/ + docs/architecture/ + examples/ starter layout (v2)",
    )
    ia.add_argument("--repo", default=".", help="Repository root")


def _run_init_authority(args: argparse.Namespace) -> None:
    from repoctx.authority.scaffold import init_authority

    result = init_authority(repo_root=args.repo)
    print(json.dumps(result.to_dict(), indent=2))


init_authority_cmd = SimpleNamespace(
    NAME="init-authority", register=_register_init_authority, run=_run_init_authority,
)


# -- propose-authority --------------------------------------------------------

def _register_propose_authority(subparsers) -> None:
    pa = subparsers.add_parser(
        "propose-authority",
        help="Generate a brief that lets an LLM author the authority files itself",
    )
    pa.add_argument("--repo", default=".", help="Repository root")
    pa.add_argument(
        "--brief-only",
        action="store_true",
        help="Print only the markdown brief (skip the JSON envelope)",
    )


def _run_propose_authority(args: argparse.Namespace) -> None:
    from repoctx.authority.propose import propose_authority

    result = propose_authority(repo_root=args.repo)
    if args.brief_only:
        print(result["agent_brief"])
        return
    print(json.dumps(result, indent=2))


propose_authority_cmd = SimpleNamespace(
    NAME="propose-authority", register=_register_propose_authority, run=_run_propose_authority,
)
