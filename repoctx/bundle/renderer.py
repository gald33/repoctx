"""Markdown rendering for a :class:`GroundTruthBundle` (secondary view).

JSON is the primary output for agents. This renderer is for human-facing UIs
and for use inside system prompts when an agent consumes markdown.
"""

from __future__ import annotations

from repoctx.bundle.schema import GroundTruthBundle


def render_bundle_markdown(bundle: GroundTruthBundle) -> str:
    lines: list[str] = []
    lines.append(f"# Ground-truth bundle")
    lines.append("")
    lines.append(f"**Task:** {bundle.task_summary}")
    lines.append("")

    lines.append("## Authority (hard → guided → implementation)")
    if not bundle.authoritative_records:
        lines.append("- None")
    for r in bundle.authoritative_records:
        lines.append(
            f"- **L{int(r.authority_level)} {r.type}** `{r.path}` — {r.title}"
        )
        if r.summary and r.summary != r.title:
            lines.append(f"  - {r.summary}")
    lines.append("")

    lines.append("## Constraints")
    if not bundle.constraints:
        lines.append("- None")
    for c in bundle.constraints:
        lines.append(f"- **[{c.severity}]** `{c.id}` — {c.statement}")
        if c.applies_to_paths:
            lines.append(f"  - applies to: {', '.join(c.applies_to_paths)}")
    lines.append("")

    lines.append("## Edit scope")
    scope = bundle.edit_scope
    lines.append(f"- allowed: {', '.join(f'`{p}`' for p in scope.allowed_paths) or '—'}")
    lines.append(f"- related: {', '.join(f'`{p}`' for p in scope.related_paths) or '—'}")
    lines.append(f"- protected: {', '.join(f'`{p}`' for p in scope.protected_paths) or '—'}")
    if scope.rationale:
        lines.append(f"- rationale: {scope.rationale}")
    lines.append("")

    lines.append("## Relevant code")
    if not bundle.relevant_code:
        lines.append("- None")
    for ref in bundle.relevant_code:
        lines.append(f"- `{ref.path}` — {ref.reason}")
    lines.append("")

    lines.append("## Validation plan")
    plan = bundle.validation_plan
    if plan.commands:
        lines.append("Commands:")
        for cmd in plan.commands:
            lines.append(f"- `{cmd}`")
    if plan.tests:
        lines.append("Tests:")
        for t in plan.tests:
            lines.append(f"- `{t}`")
    if not (plan.commands or plan.tests):
        lines.append("- None")
    lines.append("")

    lines.append("## Risk notes")
    if not bundle.risk_notes:
        lines.append("- None")
    for risk in bundle.risk_notes:
        lines.append(f"- **[{risk.severity}]** {risk.risk} — {risk.why}")
    lines.append("")

    lines.append("## When to recall repoctx")
    for rule in bundle.when_to_recall_repoctx:
        lines.append(f"- {rule}")
    lines.append("")

    lines.append("## Before you finalize")
    for item in bundle.before_finalize_checklist:
        lines.append(f"- [ ] {item}")
    lines.append("")

    lines.append(f"> {bundle.uncertainty_rule}")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_bundle_markdown"]
