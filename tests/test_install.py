"""Tests for the settings.json installer.

This is the only part of whyskill that writes to a file someone else owns, and
that file holds real configuration. Every test here is about not damaging it.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from whyskill.install import MARKER, install, settings_path, status, uninstall

EXISTING = {
    "env": {"MY_VAR": "keep-me"},
    "permissions": {"allow": ["Bash(git status)"]},
    "hooks": {
        "PostToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo theirs"}]}
        ],
        "PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "echo pre"}]}],
    },
}


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.path = self.root / ".claude" / "settings.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def seed(self, data: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data, indent=2)
        self.path.write_text(text, encoding="utf-8")

    def read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def ours(self, settings: dict) -> list:
        return [
            hook
            for groups in settings.get("hooks", {}).values()
            for group in groups
            for hook in group.get("hooks", [])
            if MARKER in hook.get("command", "")
        ]


class FreshInstall(InstallCase):
    def test_creates_the_file_and_directory(self):
        code, _ = install(self.path)
        self.assertEqual(code, 0)
        self.assertTrue(self.path.exists())
        self.assertEqual(len(self.ours(self.read())), 2)

    def test_registers_both_events(self):
        install(self.path)
        hooks = self.read()["hooks"]
        self.assertIn("PostToolUse", hooks)
        self.assertIn("SessionStart", hooks)

    def test_post_tool_use_only_matches_edits(self):
        install(self.path)
        group = self.read()["hooks"]["PostToolUse"][0]
        self.assertEqual(group["matcher"], "Edit|Write")

    def test_dry_run_writes_nothing(self):
        code, output = install(self.path, dry_run=True)
        self.assertEqual(code, 0)
        self.assertFalse(self.path.exists())
        self.assertIn(MARKER, output)
        json.loads(output)  # must be valid settings JSON


class PreservesExistingConfig(InstallCase):
    def test_unrelated_keys_survive(self):
        self.seed(EXISTING)
        install(self.path)
        settings = self.read()
        self.assertEqual(settings["env"], EXISTING["env"])
        self.assertEqual(settings["permissions"], EXISTING["permissions"])

    def test_other_hooks_survive(self):
        self.seed(EXISTING)
        install(self.path)
        settings = self.read()
        commands = [
            hook["command"]
            for groups in settings["hooks"].values()
            for group in groups
            for hook in group["hooks"]
        ]
        self.assertIn("echo theirs", commands)
        self.assertIn("echo pre", commands)

    def test_installing_is_idempotent(self):
        self.seed(EXISTING)
        for _ in range(3):
            install(self.path)
        self.assertEqual(len(self.ours(self.read())), 2)

    def test_a_backup_is_kept(self):
        self.seed(EXISTING)
        install(self.path)
        backup = self.path.with_suffix(self.path.suffix + ".whyskill-backup")
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), EXISTING)


class RefusesToDamage(InstallCase):
    def test_malformed_json_is_left_alone(self):
        self.seed("{ not json at all")
        code, message = install(self.path)
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", message)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ not json at all")

    def test_non_object_json_is_left_alone(self):
        self.seed("[1, 2, 3]")
        code, message = install(self.path)
        self.assertEqual(code, 2)
        self.assertIn("JSON object", message)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "[1, 2, 3]")

    def test_empty_file_is_treated_as_empty_settings(self):
        self.seed("")
        code, _ = install(self.path)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.ours(self.read())), 2)


class Uninstall(InstallCase):
    def test_restores_the_original_exactly(self):
        self.seed(EXISTING)
        install(self.path)
        code, _ = uninstall(self.path)
        self.assertEqual(code, 0)
        self.assertEqual(self.read(), EXISTING)

    def test_leaves_no_empty_event_lists(self):
        install(self.path)  # no pre-existing hooks at all
        uninstall(self.path)
        self.assertNotIn("hooks", self.read())

    def test_is_safe_to_run_twice(self):
        self.seed(EXISTING)
        install(self.path)
        uninstall(self.path)
        code, message = uninstall(self.path)
        self.assertEqual(code, 0)
        self.assertIn("no whyskill hooks", message)

    def test_on_a_missing_file(self):
        code, message = uninstall(self.path)
        self.assertEqual(code, 0)
        self.assertIn("nothing to remove", message)


class Status(InstallCase):
    def test_reports_absence_with_a_nonzero_code(self):
        code, message = status(self.path)
        self.assertEqual(code, 1)
        self.assertIn("not installed", message)

    def test_reports_presence(self):
        install(self.path)
        code, message = status(self.path)
        self.assertEqual(code, 0)
        self.assertIn("PostToolUse", message)
        self.assertIn("SessionStart", message)


class SettingsLocation(unittest.TestCase):
    def test_project_default(self):
        path = settings_path(user=False, project=Path("/tmp/proj"))
        self.assertEqual(path, Path("/tmp/proj/.claude/settings.json"))

    def test_project_local(self):
        path = settings_path(user=False, project=Path("/tmp/proj"), local=True)
        self.assertEqual(path, Path("/tmp/proj/.claude/settings.local.json"))

    def test_user_scope(self):
        path = settings_path(user=True)
        self.assertEqual(path, Path.home() / ".claude" / "settings.json")


if __name__ == "__main__":
    unittest.main()
