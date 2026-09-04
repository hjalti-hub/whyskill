"""Tests for the hook handlers.

The hook runs inside someone else's session, on every edit they make. Two
properties matter more than any finding it could report:

* it never breaks the session, whatever it is handed;
* it says nothing when there is nothing wrong, because silence is what makes
  the noisy case worth reading.

Both are asserted here alongside the actual reporting behaviour.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from whyskill.hooks import looks_like_skill_file, run

CLEAN = (
    "---\n"
    "name: {name}\n"
    "description: Summarizes uncommitted changes. Use when the user asks what "
    "changed or wants a commit message.\n"
    "---\n\nBody.\n"
)
# A blank first line stops the frontmatter being read at all.
BROKEN = "\n" + CLEAN
# Parses fine, but never says when to use it.
VAGUE = "---\nname: {name}\ndescription: A utility for Kubernetes manifests and clusters.\n---\n\nBody.\n"


class HookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".claude" / "skills").mkdir(parents=True)

        # Point personal-skill discovery at an empty directory so results do not
        # depend on whatever the machine running the tests happens to have.
        self.home = self.root / "fake-home"
        (self.home / "skills").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write_skill(self, name: str, template: str = CLEAN) -> Path:
        directory = self.root / ".claude" / "skills" / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "SKILL.md"
        path.write_text(template.format(name=name), encoding="utf-8")
        return path

    def fire(self, payload: object, **kwargs) -> tuple[int, str, str]:
        """Run the hook with ``payload`` on stdin; return (code, stdout, stderr)."""
        text = payload if isinstance(payload, str) else json.dumps(payload)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)):
            with redirect_stdout(out), redirect_stderr(err):
                code = run(**kwargs)
        return code, out.getvalue(), err.getvalue()

    def post_tool_use(self, path: Path, tool: str = "Write") -> tuple[int, str, str]:
        return self.fire(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": tool,
                "cwd": str(self.root),
                "tool_input": {"file_path": str(path)},
            }
        )

    def session_start(self) -> tuple[int, str, str]:
        return self.fire(
            {"hook_event_name": "SessionStart", "kind": "startup", "cwd": str(self.root)}
        )


class FileDetection(unittest.TestCase):
    def test_skill_files_are_recognised(self):
        self.assertTrue(looks_like_skill_file("/x/.claude/skills/deploy/SKILL.md"))
        self.assertTrue(looks_like_skill_file("skills/deploy/SKILL.md"))
        self.assertTrue(looks_like_skill_file("/x/.claude/commands/deploy.md"))

    def test_other_files_are_not(self):
        for path in ("README.md", "/x/src/main.py", "/x/skill.md", "", "/x/SKILL.txt"):
            with self.subTest(path=path):
                self.assertFalse(looks_like_skill_file(path))

    def test_a_markdown_file_outside_commands_is_not_a_skill(self):
        self.assertFalse(looks_like_skill_file("/x/.claude/docs/notes.md"))


class PostToolUse(HookCase):
    def test_error_exits_two_so_claude_sees_stderr(self):
        path = self.write_skill("deploy", BROKEN)
        code, _, err = self.post_tool_use(path)
        # Exit 2 is what routes the message to Claude for PostToolUse.
        self.assertEqual(code, 2)
        self.assertIn("LOAD001", err)
        self.assertIn("fix:", err)

    def test_warning_only_reports_without_interrupting(self):
        path = self.write_skill("vague", VAGUE)
        code, out, err = self.post_tool_use(path)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        block = payload["hookSpecificOutput"]
        self.assertEqual(block["hookEventName"], "PostToolUse")
        self.assertIn("LIST005", block["additionalContext"])

    def test_clean_skill_says_nothing(self):
        path = self.write_skill("summarize")
        code, out, err = self.post_tool_use(path)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_non_skill_file_is_ignored(self):
        other = self.root / "README.md"
        other.write_text("# hello", encoding="utf-8")
        code, out, err = self.post_tool_use(other)
        self.assertEqual((code, out, err), (0, "", ""))

    def test_skill_outside_the_standard_location_is_still_checked(self):
        """A repository of skills for publishing does not use .claude/skills."""
        directory = self.root / "published" / "deploy"
        directory.mkdir(parents=True)
        path = directory / "SKILL.md"
        path.write_text(BROKEN.format(name="deploy"), encoding="utf-8")

        code, _, err = self.post_tool_use(path)
        self.assertEqual(code, 2)
        self.assertIn("LOAD001", err)

    def test_cross_skill_findings_reach_the_edited_file(self):
        """A collision is caused by the *other* skill, and must still surface."""
        self.write_skill("deploy")
        path = self.write_skill("Deploy—App")  # folds to `deploy-app`, not `deploy`
        self.write_skill("deploy-app")

        code, _, err = self.post_tool_use(path)
        self.assertEqual(code, 2)
        self.assertIn("COLLIDE001", err)

    def test_missing_file_is_ignored(self):
        code, out, err = self.post_tool_use(self.root / "gone" / "SKILL.md")
        self.assertEqual((code, out, err), (0, "", ""))

    def test_edit_tool_is_handled_too(self):
        path = self.write_skill("deploy", BROKEN)
        code, _, err = self.post_tool_use(path, tool="Edit")
        self.assertEqual(code, 2)
        self.assertIn("LOAD001", err)


class SessionStart(HookCase):
    def test_broken_skill_becomes_context(self):
        self.write_skill("deploy", BROKEN)
        code, out, _ = self.session_start()
        # Plain stdout is what SessionStart injects as context; exit stays 0 so
        # the session is never blocked by a linter.
        self.assertEqual(code, 0)
        self.assertIn("LOAD001", out)
        self.assertIn("whyskill", out)

    def test_clean_setup_is_silent(self):
        self.write_skill("summarize")
        code, out, err = self.session_start()
        self.assertEqual((code, out, err), (0, "", ""))

    def test_warnings_alone_do_not_interrupt_every_session(self):
        """SessionStart reports only definite breakage, or it becomes noise."""
        self.write_skill("vague", VAGUE)
        code, out, _ = self.session_start()
        self.assertEqual((code, out), (0, ""))

    def test_warnings_can_be_opted_into(self):
        self.write_skill("vague", VAGUE)
        code, out, _ = self.fire(
            {"hook_event_name": "SessionStart", "cwd": str(self.root)},
            minimum="warning",
        )
        self.assertEqual(code, 0)
        self.assertIn("LIST005", out)


class NeverBreaksTheSession(HookCase):
    """Whatever goes wrong, the hook exits 0 and stays quiet."""

    def test_malformed_stdin(self):
        for payload in ("", "   ", "not json", "[]", "null", '{"broken": '):
            with self.subTest(payload=payload):
                code, out, err = self.fire(payload)
                self.assertEqual((code, out, err), (0, "", ""))

    def test_unknown_event(self):
        code, out, err = self.fire({"hook_event_name": "PreCompact"})
        self.assertEqual((code, out, err), (0, "", ""))

    def test_missing_tool_input(self):
        code, out, err = self.fire({"hook_event_name": "PostToolUse", "tool_name": "Write"})
        self.assertEqual((code, out, err), (0, "", ""))

    def test_tool_input_of_the_wrong_shape(self):
        code, out, err = self.fire({"hook_event_name": "PostToolUse", "tool_input": "not-a-dict"})
        self.assertEqual((code, out, err), (0, "", ""))

    def test_an_internal_crash_is_swallowed(self):
        self.write_skill("deploy", BROKEN)
        with mock.patch("whyskill.hooks._analyse", side_effect=RuntimeError("boom")):
            code, out, err = self.session_start()
        self.assertEqual((code, out, err), (0, "", ""))

    def test_explicit_event_override_wins(self):
        """`--event` lets the hook work even if the payload omits the name."""
        self.write_skill("deploy", BROKEN)
        code, out, _ = self.fire({"cwd": str(self.root)}, event_name="SessionStart")
        self.assertEqual(code, 0)
        self.assertIn("LOAD001", out)


if __name__ == "__main__":
    unittest.main()
