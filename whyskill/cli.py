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
  whyskill install                let Claude check skills without being asked
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

    installer = sub.add_parser(
        "install",
        help="Install the hooks so Claude checks skills on its own.",
        description=(
            "Register whyskill as a Claude Code hook. Once installed, skills are "
            "checked when a session starts and whenever a SKILL.md is written - "
            "the harness runs it, so nobody has to remember to."
        ),
    )
    installer.add_argument(
        "--user",
        action="store_true",
        help="Install into ~/.claude/settings.json instead of this project.",
    )
    installer.add_argument(
        "--local",
        action="store_true",
        help="Use .claude/settings.local.json (not shared with the repository).",
    )
    installer.add_argument(
        "--project", type=Path, default=None, help="Project root (default: cwd)."
    )
    installer.add_argument("--uninstall", action="store_true", help="Remove the hooks.")
    installer.add_argument(
        "--status", action="store_true", help="Report whether the hooks are installed."
    )
    installer.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print the resulting settings.json without writing it.",
    )

    # Invoked by Claude Code, not by people: reads the hook payload on stdin.
    hook = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument("--event", default=None, help="Override the hook event name.")
    hook.add_argument(
        "--fail-on",
        dest="minimum",
        choices=("error", "warning", "note"),
        default=None,
        help="Lowest severity worth reporting for this event.",
    )

    return parser


#: Subcommand names, so a bare `whyskill ./skills` can default to `check`.
COMMANDS = ("check", "why", "rules", "list", "install", "hook")


def _with_default_command(argv: list[str]) -> list[str]:
    """Insert the implicit `check` subcommand.

    Positionals cannot live on both the top-level parser and its subparsers
    without the top-level one swallowing subcommand names, so the default is
    applied here rather than in argparse.
    """
    # `not argv` must NOT return early: with no arguments at all there is no
    # subcommand to parse, so argparse produces a Namespace without any of the
    # check options and the first attribute access raises. Bare `whyskill` is
    # the most common invocation there is, so it gets the default like the rest.
    if argv and argv[0] in ("-h", "--help", "--version"):
        return argv
    first_positional = next((a for a in argv if not a.startswith("-")), None)
    if first_positional in COMMANDS:
        return argv
    return ["check", *argv]


class UsageError(Exception):
    """A bad invocation, reported without a stack trace."""


def _collect(args: argparse.Namespace) -> tuple[list[Skill], Context, Path | None]:
    explicit = list(args.paths or [])

    # A path that does not exist is a mistake, not an empty result. Without this
    # check a mistyped subcommand (`whyskill uninstall`) is silently treated as a
    # directory to scan, and reports "no skills found" with a success exit code.
    missing = [p for p in explicit if not p.expanduser().exists()]
    if missing:
        listed = ", ".join(str(p) for p in missing)
        raise UsageError(
            f"no such file or directory: {listed}\n"
            f"Run `whyskill --help` for usage, or `whyskill list` to see what is discoverable."
        )

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


def cmd_install(args: argparse.Namespace) -> int:
    from .install import install, settings_path, status, uninstall

    path = settings_path(user=args.user, project=args.project, local=args.local)

    if args.status:
        code, message = status(path)
        print(message)
        return code
    if args.uninstall:
        code, message = uninstall(path)
        print(message)
        return code

    code, message = install(path, dry_run=args.print_only)
    print(message)
    return code


def cmd_hook(args: argparse.Namespace) -> int:
    from .hooks import run

    return run(event_name=args.event, minimum=args.minimum)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_with_default_command(raw))

    command = args.command or "check"
    try:
        if command == "hook":
            return cmd_hook(args)
        if command == "install":
            return cmd_install(args)
        if command == "rules":
            return cmd_rules(args)
        if command == "list":
            return cmd_list(args)
        if command == "why":
            return cmd_why(args)
        return cmd_check(args)
    except UsageError as exc:
        print(f"whyskill: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
