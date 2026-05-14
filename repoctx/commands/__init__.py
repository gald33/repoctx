"""Per-subcommand modules for the repoctx CLI.

Each module exposes:
  - NAME: str            -- the subcommand string
  - register(subparsers) -- add the subparser
  - run(args)            -- execute the subcommand
"""

from repoctx.commands import (
    eval as eval_module,
    experiment,
    hook,
    index,
    install,
    protocol_ops,
    query,
    reap,
    stats,
    tune as tune_module,
)

# Order controls --help display order.
COMMAND_MODULES = [
    query,
    index.index_cmd,
    index.update_cmd,
    index.rebuild_cmd,
    experiment,
    protocol_ops.bundle_cmd,
    protocol_ops.authority_cmd,
    protocol_ops.scope_cmd,
    protocol_ops.validate_plan_cmd,
    protocol_ops.risk_report_cmd,
    protocol_ops.refresh_cmd,
    protocol_ops.detect_changes_cmd,
    protocol_ops.semantic_search_cmd,
    install.install_all_cmd,
    install.install_claude_code_cmd,
    install.install_cursor_cmd,
    install.install_codex_cmd,
    install.init_authority_cmd,
    install.propose_authority_cmd,
    stats,
    hook.hook_cmd,
    reap.reap_cmd,
    eval_module.eval_cmd,
    tune_module.tune_cmd,
]

COMMAND_HANDLERS = {mod.NAME: mod.run for mod in COMMAND_MODULES}
SUBCOMMAND_NAMES = {mod.NAME for mod in COMMAND_MODULES}
