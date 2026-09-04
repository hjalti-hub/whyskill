"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .discover import discover
from .model import Finding, Severity, Skill
from .normalize import fold
from .report import VERSION, Style, render_human, render_json, render_sarif, render_skill_list
from .rules import Context, run_all
from .rules.catalog import CATALOG, GROUPS

EPILOG = """\
examples:
  whyskill                        check the current project plus your personal skills
  whyskill ./skills               check a directory of skills you are publishing
  whyskill why deploy             explain why the `deploy` skill may not be firing
  whyskill --target spec          also check claude.ai / Skills API portability
  whyskill --sarif > out.sarif    emit SARIF for CI annotations
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whyskill",
        description=(
            "Find Claude Code skills that will not load, will not be chosen, or "
            "collide with your other skills - the failures that never print an error."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"whyskill {VERSION}")

    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "paths",
            nargs="*",
            type=Path,
            help="Skill directories or SKILL.md files. Defaults to the current project.",
        )
        p.add_argument(
            "--project",
            type=Path,
            default=None,
            help="Project root to resolve .claude/skills and `paths` globs against.",
        )
        p.add_argument(
            "--no-personal",
            action="store_true",
            help="Skip ~/.claude/skills. Cross-skill shadowing will not be detected.",
        )
        p.add_argument("--no-plugins", action="store_true", help="Skip plugin skills.")
        p.add_argument(
            "--target",
            choices=("claude-code", "spec"),
            default="claude-code",
            help="Distribution target. `spec` also checks claude.ai/Skills API field limits.",
        )
        p.add_argument(
            "--overlap",
            type=float,
            default=0.5,
            metavar="N",
            help="Similarity threshold for COLLIDE005, 0..1 (default: 0.5).",
        )
        p.add_argument(
            "--disable",
            default="",
            metavar="IDS",
            help="Comma-separated rule ids to suppress, e.g. INVOKE002,PORT001.",
        )
        p.add_argument(
            "--fail-on",
            choices=("error", "warning", "note", "never"),
            default="error",
            help="Lowest severity that exits non-zero (default: error).",
        )
        p.add_argument(
            "--explain", action="store_true", help="Show the mechanic behind each finding."
        )
        group = p.add_mutually_exclusive_group()
        group.add_argument("--json", action="store_true", help="Emit JSON.")
        group.add_argument("--sarif", action="store_true", help="Emit SARIF 2.1.0.")

    check = sub.add_parser("check", help="Check skills (default).")
    add_common(check)

    why = sub.add_parser("why", help="Explain why one skill may not be firing.")
    why.add_argument("skill", help="Skill name, as you would type it.")
    add_common(why)

    sub.add_parser("rules", help="List every rule.")

    listing = sub.add_parser("list", help="List discovered skills.")
    add_common(listing)

    return parser


#: Subcommand names, so a bare `whyskill ./skills` can default to `check`.
COMMANDS = ("check", "why", "rules", "list")


def _with_default_command(argv: list[str]) -> list[str]:
    """Insert the implicit `check` subcommand.

    Positionals cannot live on both the top-level parser and its subparsers
    without the top-level one swallowing subcommand names, so the default is
    applied here rather than in argparse.
    """
    if not argv or argv[0] in ("-h", "--help", "--version"):
        return argv
    first_positional = next((a for a in argv if not a.startswith("-")), None)
    if first_positional in COMMANDS:
        return argv
    return ["check", *argv]


def _collect(args: argparse.Namespace) -> tuple[list[Skill], Context, Path | None]:
    explicit = list(args.paths or [])
    project = args.project
    if project is None:
        project = Path.cwd()

    skills = discover(
        project=None if explicit else project,
        include_personal=not args.no_personal,
        include_plugins=not args.no_plugins,
        explicit=explicit or None,
    )

    disabled = frozenset(part.strip().upper() for part in args.disable.split(",") if part.strip())
    ctx = Context(
        project=project,
        target=args.target,
        overlap_threshold=args.overlap,
        disabled=disabled,
    )
    return skills, ctx, project


def _exit_code(findings: list[Finding], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = {"error": 3, "warning": 2, "note": 1}[fail_on]
    return 1 if any(f.severity.rank >= threshold for f in findings) else 0


def _emit(
    args: argparse.Namespace, findings: list[Finding], skills: list[Skill], root: Path | None
) -> None:
    if args.json:
        render_json(findings, skills)
    elif args.sarif:
        render_sarif(findings, skills, root=root)
    else:
        render_human(findings, skills, root=root, verbose=args.explain)


def cmd_check(args: argparse.Namespace) -> int:
    skills, ctx, root = _collect(args)
    findings = run_all(skills, ctx)
    _emit(args, findings, skills, root)
    return _exit_code(findings, args.fail_on)


def cmd_list(args: argparse.Namespace) -> int:
    skills, _, _ = _collect(args)
    render_skill_list(skills)
    return 0


def cmd_rules(_: argparse.Namespace) -> int:
    st = Style(sys.stdout)
    for prefix, heading in GROUPS.items():
        print(f"\n{st.bold(heading)}")
        for rule, summary in CATALOG.items():
            if rule.startswith(prefix):
                print(f"  {st.cyan(rule.ljust(11))}{summary}")
    print()
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    skills, ctx, root = _collect(args)
    st = Style(sys.stdout)

    target = fold(args.skill.lstrip("/"))
    matches = [s for s in skills if fold(s.invocation_name) == target]
    if not matches:
        near = [s.invocation_name for s in skills if target in fold(s.invocation_name)]
        print(f"No skill named {args.skill!r} was found.")
        if near:
            print("Did you mean: " + ", ".join(sorted(near)[:6]) + "?")
        else:
            print("Run `whyskill list` to see what was discovered.")
        return 2

    findings = run_all(skills, ctx)

    for skill in matches:
        relevant = [f for f in findings if f.path == skill.path or str(skill.path) in f.related]

        print(f"\n{st.bold(skill.invocation_name)}  {st.dim(str(skill.path))}")
        print(f"  source: {skill.source.value}")

        blockers = [f for f in relevant if f.severity is Severity.ERROR]
        auto_blocked = [
            f
            for f in relevant
            if f.rule
            in {
                "LOAD001",
                "LOAD002",
                "LOAD003",
                "LIST001",
                "INVOKE001",
                "INVOKE002",
                "INVOKE003",
                "COLLIDE001",
            }
        ]

        if auto_blocked:
            print(f"  {st.red('Claude cannot auto-invoke this skill.')}")
        elif blockers:
            print(f"  {st.yellow('Reachable, but something above is broken.')}")
        else:
            print(f"  {st.blue('Nothing is stopping this skill from being chosen.')}")

        if not relevant:
            print(st.dim("  No findings. If it still is not firing, the description "))
            print(st.dim("  probably does not match the words you actually type."))
            continue

        print()
        for finding in relevant:
            print(f"  {st.severity(finding.severity)} {st.dim(finding.rule)}  {finding.message}")
            if finding.mechanic:
                print(st.dim(f"      why: {finding.mechanic}"))
            if finding.fix:
                print(st.cyan(f"      fix: {finding.fix}"))
            print()

    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_with_default_command(raw))

    command = args.command or "check"
    if command == "rules":
        return cmd_rules(args)
    if command == "list":
        return cmd_list(args)
    if command == "why":
        return cmd_why(args)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
