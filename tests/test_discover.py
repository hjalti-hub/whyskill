"""Tests for what whyskill decides is a skill in the first place.

Discovery had no tests, and that is how the bug below survived 164 of them:
every rule was tested against skills handed to it directly, so nothing ever
checked which files became skills. A false positive here is worse than a missed
rule - it invents findings about files the reader does not own and cannot fix.

The layouts here are copied from a real ~/.claude/plugins, not imagined. An
earlier version of this file guessed at the structure, and the guess was wrong
in a way that let the bug through a second time.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from whyskill.discover import discover

SKILL = """\
---
name: impeccable
description: Design fluency for frontend work. Use when building or polishing UI.
---

Body.
"""

#: Every harness directory found in one real marketplace clone.
HARNESS_DIRS = (
    ".cursor",
    ".trae-cn",
    ".gemini",
    ".trae",
    ".grok",
    ".qoder",
    ".rovodev",
    ".opencode",
    ".agents",
    ".claude",
    ".github",
    ".agent",
    ".vibe",
    ".kiro",
    ".hermes",
    ".pi",
    "plugin",
)


class DiscoveryCase(unittest.TestCase):
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

    def record(self, plugins: dict) -> None:
        self.write(
            "plugins/installed_plugins.json",
            json.dumps({"version": 2, "plugins": plugins}),
        )

    def marketplace_clone(self) -> None:
        """A catalogue clone of a project that supports many AI tools."""
        for harness in HARNESS_DIRS:
            self.write(f"plugins/marketplaces/impeccable/{harness}/skills/impeccable/SKILL.md")

    def found(self) -> list:
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home)}):
            return discover(project=None, include_personal=True, include_plugins=True)


class MarketplaceCatalogues(DiscoveryCase):
    def test_a_catalogue_clone_is_not_installed_software(self):
        """Regression: one skill was reported as sixteen colliding duplicates.

        `marketplaces/` holds git clones of plugin catalogues - everything a
        marketplace offers, installed or not. A project supporting many
        harnesses ships the same skill into each one's directory, so scanning
        the clone finds all seventeen copies. Claude Code loads none of them.
        """
        self.marketplace_clone()
        self.record({})
        self.assertEqual(self.found(), [])

    def test_catalogue_is_skipped_even_with_plugins_installed(self):
        """The exclusion is structural, not a side effect of an empty record.

        A first fix filtered dot-directories instead, which left `plugin/` and
        `.claude/` inside the same clone still being reported.
        """
        self.marketplace_clone()
        self.write("plugins/cache/impeccable/impeccable/4.1.1/skills/impeccable/SKILL.md")
        self.record({"impeccable": {}})

        skills = self.found()
        self.assertEqual(len(skills), 1, [str(s.path) for s in skills])
        self.assertIn("cache", skills[0].path.parts)

    def test_plugin_data_directories_hold_no_skills(self):
        self.write("plugins/data/impeccable-inline/skills/whatever/SKILL.md")
        self.record({"impeccable": {}})
        self.assertEqual(self.found(), [])


class InstalledRecord(DiscoveryCase):
    def test_an_empty_record_means_nothing_is_installed(self):
        """`{"version": 2, "plugins": {}}` is an answer, not a missing file.

        It is what Claude Code writes when a marketplace has been added but
        nothing from it installed - the exact state that produced five findings
        about plugins the reader had never heard of.
        """
        self.write("plugins/cache/impeccable/impeccable/4.1.1/skills/impeccable/SKILL.md")
        self.record({})
        self.assertEqual(self.found(), [])

    def test_an_unreadable_record_falls_back_to_scanning(self):
        """Going silent because a file moved would be its own kind of wrong."""
        self.write("plugins/cache/mkt/toolkit/1.0.0/skills/deploy/SKILL.md")
        self.assertEqual(len(self.found()), 1)

    def test_a_malformed_record_falls_back_to_scanning(self):
        self.write("plugins/cache/mkt/toolkit/1.0.0/skills/deploy/SKILL.md")
        self.write("plugins/installed_plugins.json", "{ not json")
        self.assertEqual(len(self.found()), 1)

    def test_plugins_absent_from_the_record_are_skipped(self):
        self.write("plugins/cache/mkt/wanted/1.0.0/skills/deploy/SKILL.md")
        self.write("plugins/cache/mkt/unwanted/1.0.0/skills/review/SKILL.md")
        self.record({"wanted": {}})

        names = [s.path.parent.name for s in self.found()]
        self.assertEqual(names, ["deploy"])

    def test_record_keys_may_be_marketplace_qualified(self):
        self.write("plugins/cache/mkt/toolkit/1.0.0/skills/deploy/SKILL.md")
        self.record({"mkt:toolkit": {}})
        self.assertEqual(len(self.found()), 1)


class PluginNaming(DiscoveryCase):
    def test_the_version_directory_is_not_the_plugin_name(self):
        """Regression: a skill was reported as belonging to plugin `4.1.1`.

        The cache is `<marketplace>/<plugin>/<version>/skills/`, so the
        directory above `skills/` is the version. Even when discovery found the
        right file, it labelled it with a version number.
        """
        self.write("plugins/cache/impeccable/impeccable/4.1.1/skills/impeccable/SKILL.md")
        self.record({"impeccable": {}})

        skills = self.found()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].plugin, "impeccable")

    def test_an_unversioned_layout_still_names_the_plugin(self):
        self.write("plugins/cache/mkt/toolkit/skills/deploy/SKILL.md")
        self.record({"toolkit": {}})
        self.assertEqual(self.found()[0].plugin, "toolkit")


class OrdinarySkills(DiscoveryCase):
    def test_personal_skills_are_unaffected(self):
        self.marketplace_clone()
        self.write("skills/mine/SKILL.md")
        self.record({})

        skills = self.found()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].path.parent.name, "mine")

    def test_installed_plugin_skills_are_still_found(self):
        self.write("plugins/cache/mkt/toolkit/1.0.0/skills/deploy/SKILL.md")
        self.write("plugins/cache/mkt/toolkit/1.0.0/skills/review/SKILL.md")
        self.record({"toolkit": {}})

        names = sorted(s.path.parent.name for s in self.found())
        self.assertEqual(names, ["deploy", "review"])

    def test_a_plugin_may_ship_a_skill_at_its_root(self):
        self.write("plugins/cache/mkt/toolkit/1.0.0/SKILL.md")
        self.record({"toolkit": {}})
        self.assertEqual(len(self.found()), 1)


if __name__ == "__main__":
    unittest.main()
