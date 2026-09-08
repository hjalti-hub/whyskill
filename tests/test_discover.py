"""Tests for what whyskill decides is a skill in the first place.

Discovery had no tests, and that is how the bug below survived 164 of them:
every rule was tested against skills handed to it directly, so nothing ever
checked which files became skills. A false positive here is worse than a
missed rule - it invents findings about files the reader does not own and
cannot fix.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from whyskill.discover import discover

SKILL = """\
---
name: impeccable
description: Design fluency for frontend work. Use when building or polishing UI.
---

Body.
"""


class PluginDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp()) / ".claude"
        self.home.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.home.parent, ignore_errors=True)

    def write(self, relative: str, content: str = SKILL) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def found(self) -> list:
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home)}):
            return discover(project=None, include_personal=True, include_plugins=True)

    def test_other_tools_copies_are_not_claude_skills(self):
        """Regression: one repository was reported as a dozen duplicate skills.

        A marketplace entry is a whole git repository. A project supporting
        several agents ships the same skill into each one's directory, so a
        recursive search for SKILL.md finds `.cursor/`, `.gemini/`, `.grok/`
        and the rest. Claude Code loads none of them.

        The real report, against a marketplace clone of pbakaus/impeccable:
        nineteen skills with byte-identical descriptions, every one of them a
        different harness's copy of a single skill. The advice that came with
        it was to go and edit another tool's configuration files.
        """
        market = "plugins/marketplaces/impeccable"
        for tool in (".agent", ".cursor", ".gemini", ".github", ".grok", ".codex"):
            self.write(f"{market}/{tool}/skills/impeccable/SKILL.md")
        self.write(f"{market}/.claude/skills/impeccable/SKILL.md")

        skills = self.found()
        self.assertEqual(len(skills), 1, [str(s.path) for s in skills])
        self.assertIn(".claude", skills[0].path.parts)

    def test_a_root_skill_in_another_tools_directory_is_also_ignored(self):
        """The same exclusion has to cover the plugin-root branch.

        Skills are collected twice - once under `skills/`, once from a plugin
        root - and filtering only the first leaves the bug reachable by any
        layout that puts SKILL.md at the top of a tool's directory.
        """
        self.write("plugins/marketplaces/thing/.cursor/SKILL.md")
        self.write("plugins/marketplaces/thing/.claude/SKILL.md")

        skills = self.found()
        self.assertEqual(len(skills), 1, [str(s.path) for s in skills])
        self.assertIn(".claude", skills[0].path.parts)

    def test_ordinary_plugin_skills_are_still_found(self):
        """The exclusion must not swallow the normal case."""
        self.write("plugins/repos/someone/toolkit/skills/deploy/SKILL.md")
        self.write("plugins/repos/someone/toolkit/skills/review/SKILL.md")

        names = sorted(s.path.parent.name for s in self.found())
        self.assertEqual(names, ["deploy", "review"])

    def test_claude_plugin_manifest_directory_is_not_treated_as_foreign(self):
        """`.claude-plugin` is Claude's own, despite the leading dot."""
        self.write("plugins/repos/someone/kit/.claude-plugin/skills/deploy/SKILL.md")
        self.assertEqual(len(self.found()), 1)


if __name__ == "__main__":
    unittest.main()
