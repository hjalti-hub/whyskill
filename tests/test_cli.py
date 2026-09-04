"""End-to-end tests over the CLI and the shipped examples.

The examples in ``examples/broken`` are documentation: each directory claims to
demonstrate one silent failure. These tests hold them to that claim, so an
example cannot quietly stop demonstrating its own bug.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from whyskill.cli import _with_default_command, main

REPO = Path(__file__).resolve().parent.parent
BROKEN = REPO / "examples" / "broken"
GOOD = REPO / "examples" / "good"

BASE = ["--no-personal", "--no-plugins"]


def run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class ArgumentDefaulting(unittest.TestCase):
    """A bare path must not be mistaken for a subcommand, and vice versa."""

    def test_bare_path_defaults_to_check(self):
        self.assertEqual(_with_default_command(["./skills"]), ["check", "./skills"])

    def test_explicit_subcommand_is_left_alone(self):
        self.assertEqual(_with_default_command(["why", "deploy"]), ["why", "deploy"])

    def test_option_value_is_not_read_as_a_subcommand(self):
        self.assertEqual(
            _with_default_command(["--disable", "PORT001"]),
            ["check", "--disable", "PORT001"],
        )

    def test_help_is_not_rewritten(self):
        self.assertEqual(_with_default_command(["--help"]), ["--help"])

    def test_new_subcommands_are_recognised(self):
        for command in ("install", "hook"):
            with self.subTest(command=command):
                self.assertEqual(_with_default_command([command]), [command])

    def test_no_arguments_still_gets_the_default_command(self):
        """Regression: `whyskill` with no arguments crashed.

        An empty argv was returned unchanged, so argparse parsed no subcommand
        and produced a Namespace without any of the check options. The first
        attribute access then raised AttributeError. Bare `whyskill` is the
        most common invocation there is.
        """
        self.assertEqual(_with_default_command([]), ["check"])


class RunsWithNoArguments(unittest.TestCase):
    """Every entry point must survive being invoked with nothing at all."""

    def test_bare_invocation_does_not_crash(self):
        with tempfile.TemporaryDirectory() as empty:
            # An empty config dir keeps the result independent of this machine.
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": empty}):
                code, out = run([])
        self.assertIn(code, (0, 1))
        self.assertTrue(out.strip())

    def test_every_subcommand_survives_bare_invocation(self):
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": empty}):
                for command in ("check", "list", "rules"):
                    with self.subTest(command=command):
                        code, out = run([command])
                        self.assertIn(code, (0, 1))
                        self.assertTrue(out.strip())


class BadInvocation(unittest.TestCase):
    """A mistyped subcommand must not be silently scanned as a directory."""

    def test_missing_path_is_an_error(self):
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(["check", "/nonexistent/path/xyz", *BASE])
        self.assertEqual(code, 2)
        self.assertIn("no such file or directory", buffer.getvalue())

    def test_mistyped_subcommand_does_not_report_success(self):
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main(["uninstall"])
        self.assertEqual(code, 2)


class InstallCommand(unittest.TestCase):
    def test_print_only_emits_settings_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run(["install", "--project", tmp, "--print"])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertIn("SessionStart", payload["hooks"])
            self.assertFalse((Path(tmp) / ".claude" / "settings.json").exists())

    def test_install_then_status_then_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run(["install", "--project", tmp])[0], 0)
            self.assertEqual(run(["install", "--project", tmp, "--status"])[0], 0)
            self.assertEqual(run(["install", "--project", tmp, "--uninstall"])[0], 0)
            self.assertEqual(run(["install", "--project", tmp, "--status"])[0], 1)


class ExitCodes(unittest.TestCase):
    def test_clean_examples_exit_zero(self):
        code, out = run([str(GOOD), *BASE])
        self.assertEqual(code, 0)
        self.assertIn("nothing silently broken", out)

    def test_broken_examples_exit_one(self):
        code, _ = run([str(BROKEN), *BASE])
        self.assertEqual(code, 1)

    def test_fail_on_never_always_exits_zero(self):
        code, _ = run([str(BROKEN), *BASE, "--fail-on", "never"])
        self.assertEqual(code, 0)

    def test_fail_on_warning_catches_warnings(self):
        code, _ = run([str(BROKEN), *BASE, "--fail-on", "warning"])
        self.assertEqual(code, 1)

    def test_disable_suppresses_a_rule(self):
        _, with_rule = run([str(BROKEN), *BASE, "--json"])
        _, without = run([str(BROKEN), *BASE, "--json", "--disable", "PORT001"])
        self.assertIn("PORT001", with_rule)
        self.assertNotIn("PORT001", without)


class ExampleCoverage(unittest.TestCase):
    """Every broken example must demonstrate the rule it is named for."""

    EXPECTED = {
        "blank-line-first": "LOAD001",
        "bom-first": "LOAD002",
        "never-closed": "LOAD003",
        "duplicate-key": "LOAD005",
        "truncated-triggers": "LIST004",
        "no-when-clause": "LIST005",
        "dead-skill": "INVOKE001",
        "path-typo": "INVOKE003",
        "invented-fields": "PORT001",
    }

    @classmethod
    def setUpClass(cls) -> None:
        _, out = run([str(BROKEN), *BASE, "--json"])
        cls.payload = json.loads(out)

    def test_each_example_triggers_its_rule(self):
        for directory, rule in self.EXPECTED.items():
            with self.subTest(example=directory):
                fired = {
                    f["rule"] for f in self.payload["findings"] if f"/{directory}/" in f["path"]
                }
                self.assertIn(rule, fired)

    def test_collision_examples_pair_up(self):
        rules = {f["rule"] for f in self.payload["findings"]}
        self.assertIn("COLLIDE001", rules)  # deploy-app vs Deploy—App
        self.assertIn("COLLIDE002", rules)  # review-pr vs Cyrillic twin
        self.assertIn("COLLIDE005", rules)  # git-helper vs git-assistant

    def test_every_finding_carries_a_mechanic_and_fix(self):
        for finding in self.payload["findings"]:
            with self.subTest(rule=finding["rule"], path=finding["path"]):
                self.assertTrue(finding.get("mechanic"), "missing mechanic")
                self.assertTrue(finding.get("fix"), "missing fix")

    def test_findings_are_sorted_errors_first(self):
        ranks = {"error": 3, "warning": 2, "note": 1}
        seen = [ranks[f["severity"]] for f in self.payload["findings"]]
        self.assertEqual(seen, sorted(seen, reverse=True))


class OutputFormats(unittest.TestCase):
    def test_json_shape(self):
        _, out = run([str(BROKEN), *BASE, "--json"])
        payload = json.loads(out)
        self.assertIn("summary", payload)
        self.assertIn("findings", payload)
        self.assertEqual(payload["summary"]["skills"], len(payload["skills"]))
        self.assertGreater(payload["summary"]["errors"], 0)

    def test_sarif_shape(self):
        _, out = run([str(BROKEN), *BASE, "--sarif"])
        doc = json.loads(out)
        self.assertEqual(doc["version"], "2.1.0")
        run_block = doc["runs"][0]
        self.assertEqual(run_block["tool"]["driver"]["name"], "whyskill")
        self.assertTrue(run_block["results"])
        for result in run_block["results"]:
            self.assertIn(result["level"], {"error", "warning", "note"})
            region = result["locations"][0]["physicalLocation"]["region"]
            self.assertGreaterEqual(region["startLine"], 1)

        # Every rule referenced by a result must be declared in the driver.
        declared = {rule["id"] for rule in run_block["tool"]["driver"]["rules"]}
        used = {result["ruleId"] for result in run_block["results"]}
        self.assertEqual(used - declared, set())


class WhyCommand(unittest.TestCase):
    def test_reports_a_blocked_skill(self):
        code, out = run(["why", "dead-skill", str(BROKEN), *BASE])
        self.assertEqual(code, 0)
        self.assertIn("cannot auto-invoke", out)
        self.assertIn("INVOKE001", out)

    def test_unknown_skill_exits_two(self):
        code, out = run(["why", "no-such-skill", str(BROKEN), *BASE])
        self.assertEqual(code, 2)
        self.assertIn("No skill named", out)

    def test_suggests_near_matches(self):
        code, out = run(["why", "deploy", str(BROKEN), *BASE])
        self.assertEqual(code, 2)
        self.assertIn("Did you mean", out)

    def test_leading_slash_is_accepted(self):
        code, _ = run(["why", "/dead-skill", str(BROKEN), *BASE])
        self.assertEqual(code, 0)


class RulesCommand(unittest.TestCase):
    def test_lists_every_rule_in_the_catalog(self):
        from whyskill.rules.catalog import CATALOG

        code, out = run(["rules"])
        self.assertEqual(code, 0)
        for rule in CATALOG:
            self.assertIn(rule, out)


class ListCommand(unittest.TestCase):
    def test_lists_discovered_skills(self):
        code, out = run(["list", str(BROKEN), *BASE])
        self.assertEqual(code, 0)
        self.assertIn("dead-skill", out)
        self.assertIn("user-only", out)


if __name__ == "__main__":
    unittest.main()
