"""Rules about frontmatter fields: unknown ones, and ones that stop travelling.

Claude Code tolerates frontmatter fields it does not know, so a typo, or a field
invented by a third-party linter, simply does nothing. The claude.ai upload path
and the Skills API are stricter and reject the file outright.
"""

from __future__ import annotations

from ..model import Finding, Severity, Skill
from ..spec import CLAUDE_CODE_FIELDS, COMPATIBILITY_MAX, SPEC_FIELDS
from . import Context, per_skill

#: Fields that other tools in the ecosystem ask for but Claude Code never reads.
#: Called out by name so the report can say why removing them is safe.
KNOWN_NON_FIELDS = {
    "version": "not a Claude Code frontmatter field, though several third-party skill linters require it",
    "author": "not a frontmatter field; put it in `metadata` if you need it",
    "tags": "not a frontmatter field; put it in `metadata` if you need it",
    "category": "not a frontmatter field; put it in `metadata` if you need it",
    "tools": "not a field; the field is `allowed-tools`",
    "when-to-use": "misspelled; the field is `when_to_use` with underscores",
    "when_to_trigger": "not a field; the field is `when_to_use`",
    "trigger": "not a field; put trigger phrasing in `description` or `when_to_use`",
    "triggers": "not a field; put trigger phrasing in `description` or `when_to_use`",
    "enabled": "not a field; use `disable-model-invocation` or settings `skillOverrides`",
    "argument_hint": "misspelled; the field is `argument-hint` with a dash",
    "allowed_tools": "misspelled; the field is `allowed-tools` with a dash",
    "disable_model_invocation": "misspelled; the field is `disable-model-invocation` with dashes",
}


@per_skill
def unknown_fields(skill: Skill, ctx: Context) -> list[Finding]:
    """PORT001 - a frontmatter field Claude Code does not read."""
    if not skill.has_frontmatter:
        return []

    findings: list[Finding] = []
    for key in skill.frontmatter:
        if key in CLAUDE_CODE_FIELDS:
            continue
        reason = KNOWN_NON_FIELDS.get(key)
        detail = f" - {reason}" if reason else " - not a documented frontmatter field"
        findings.append(
            skill.finding(
                "PORT001",
                Severity.WARNING if reason else Severity.NOTE,
                f"`{key}` has no effect{detail}",
                mechanic=(
                    "Claude Code acts only on documented frontmatter fields. It "
                    "ignores the rest silently, so a misspelled field behaves "
                    "exactly like a missing one."
                ),
                fix=(f"Remove `{key}`, or move it under `metadata` if your own tooling reads it."),
                line=skill.line_of(key, 2),
            )
        )
    return findings


@per_skill
def not_portable_to_spec(skill: Skill, ctx: Context) -> list[Finding]:
    """PORT002 - fields that break a claude.ai or Skills API upload."""
    if ctx.target != "spec" or not skill.has_frontmatter:
        return []

    offenders = sorted(k for k in skill.frontmatter if k not in SPEC_FIELDS)
    if not offenders:
        return []

    return [
        skill.finding(
            "PORT002",
            Severity.ERROR,
            (
                "Frontmatter field(s) rejected on this distribution path: "
                + ", ".join(f"`{k}`" for k in offenders)
            ),
            mechanic=(
                "claude.ai skill uploads, the Skills API and `package_skill.py` "
                "accept only name, description, license, compatibility, metadata "
                "and allowed-tools. Anything else fails with 'Unexpected key(s) in "
                "SKILL.md frontmatter'."
            ),
            fix=(
                "Remove the extra fields for this distribution path. Frontmatter "
                "that follows the spec still loads in Claude Code without changes."
            ),
            line=skill.line_of(offenders[0], 2),
        )
    ]


@per_skill
def compatibility_too_long(skill: Skill, ctx: Context) -> list[Finding]:
    """PORT003 - `compatibility` past its documented 500-character limit."""
    value = skill.frontmatter.get("compatibility")
    if not isinstance(value, str) or len(value) <= COMPATIBILITY_MAX:
        return []
    return [
        skill.finding(
            "PORT003",
            Severity.WARNING,
            f"`compatibility` is {len(value)} characters; the limit is {COMPATIBILITY_MAX}",
            mechanic="`compatibility` accepts a string of up to 500 characters.",
            fix="Shorten it.",
            line=skill.line_of("compatibility", 2),
        )
    ]


@per_skill
def metadata_shape(skill: Skill, ctx: Context) -> list[Finding]:
    """PORT004 - `metadata` that is dropped, or shadows a real field name."""
    if "metadata" not in skill.frontmatter:
        return []
    value = skill.frontmatter["metadata"]
    line = skill.line_of("metadata", 2)

    if value is not None and not isinstance(value, dict):
        return [
            skill.finding(
                "PORT004",
                Severity.WARNING,
                "`metadata` is not a map, so Claude Code drops it",
                mechanic="Claude Code drops a `metadata` value that isn't a map.",
                fix="Write `metadata` as nested `key: value` pairs.",
                line=line,
            )
        ]

    if isinstance(value, dict):
        clashes = sorted(k for k in value if k in CLAUDE_CODE_FIELDS)
        if clashes:
            return [
                skill.finding(
                    "PORT004",
                    Severity.WARNING,
                    (
                        "`metadata` reuses frontmatter field name(s): "
                        + ", ".join(f"`{k}`" for k in clashes)
                    ),
                    mechanic=(
                        "The documentation warns against reusing frontmatter field "
                        "names such as `paths` as metadata keys."
                    ),
                    fix="Rename the metadata key to something unambiguous.",
                    line=line,
                )
            ]
    return []
