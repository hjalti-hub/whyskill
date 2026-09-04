"""Tests for the rules themselves.

Each test builds the smallest skill tree that triggers one rule, so a failure
names exactly one broken behaviour. A shared helper asserts both directions:
the rule fires when it should, and is silent when it should not.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from whyskill.discover import discover
from whyskill.model import Severity, Source
from whyskill.rules import Context, run_all
from whyskill.rules.invocation import glob_to_regex

CLEAN = (
    "---\n"
    "name: {name}\n"
    "description: Summarizes uncommitted changes. Use when the user asks what "
    "changed or wants a commit message.\n"
    "---\n\nBody.\n"
)


class RuleCase(unittest.TestCase):
    """Base class providing a scratch project tree."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.skills_dir = self.root / ".claude" / "skills"
        self.skills_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, name: str, content: str, *, personal: bool = False) -> Path:
        base = (self.root / "personal" / "skills") if personal else self.skills_dir
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "SKILL.md"
        path.write_text(content, encoding="utf-8")
        return path

    def run_rules(self, *, target: str = "claude-code", overlap: float = 0.5):
        skills = discover(project=self.root, include_personal=False)
        ctx = Context(project=self.root, target=target, overlap_threshold=overlap)
        return skills, run_all(skills, ctx)

    def rules_fired(self, **kwargs) -> set[str]:
        _, findings = self.run_rules(**kwargs)
        return {f.rule for f in findings}


class ListingRules(RuleCase):
    def test_clean_skill_is_silent(self):
        self.write("summarize", CLEAN.format(name="summarize"))
        self.assertEqual(self.rules_fired(), set())

    def test_missing_description_with_no_body(self):
        self.write("empty", "---\nname: empty\n---\n")
        self.assertIn("LIST001", self.rules_fired())

    def test_missing_description_falls_back_to_body(self):
        self.write("fallback", "---\nname: fallback\n---\n\nSome prose paragraph.\n")
        fired = self.rules_fired()
        self.assertIn("LIST002", fired)
        self.assertNotIn("LIST001", fired)

    def test_heading_is_not_a_usable_fallback(self):
        self.write("heading", "---\nname: heading\n---\n\n# Just a heading\n")
        self.assertIn("LIST001", self.rules_fired())

    def test_listing_over_cap_is_reported(self):
        # Cues present in the kept part, so this is LIST003 not LIST004.
        text = "Use when the user asks. " + ("padding word " * 200)
        self.write("long", f"---\nname: long\ndescription: {text}\n---\n")
        fired = self.rules_fired()
        self.assertIn("LIST003", fired)
        self.assertNotIn("LIST004", fired)

    def test_triggers_past_the_cap_are_an_error(self):
        body = "reference material " * 100  # ~1900 chars, no trigger cue
        self.write(
            "cut",
            f"---\nname: cut\ndescription: {body}\nwhen_to_use: Use when the user asks to deploy.\n---\n",
        )
        _, findings = self.run_rules()
        matching = [f for f in findings if f.rule == "LIST004"]
        self.assertEqual(len(matching), 1)
        self.assertIs(matching[0].severity, Severity.ERROR)

    def test_no_trigger_cue(self):
        self.write(
            "vague",
            "---\nname: vague\ndescription: A helpful utility for Kubernetes manifests and cluster resources.\n---\n",
        )
        self.assertIn("LIST005", self.rules_fired())

    def test_thin_description(self):
        self.write("thin", "---\nname: thin\ndescription: Deploy helper.\n---\n")
        self.assertIn("LIST006", self.rules_fired())

    def test_trigger_wording_variants_are_recognised(self):
        """Regression: phrase lists flagged real descriptions that state a trigger.

        Every string below says when to use the skill, in wording drawn from
        skills that ship with Claude Code. None may raise LIST005.
        """
        variants = [
            "Use this skill whenever the user wants to do anything with PDF files.",
            "Use when the user asks what changed or wants a commit message.",
            "Trigger whenever the user asks for a PowerPoint or .pptx file.",
            "Reach for this when the task matches an available agent type.",
            "Any time a spreadsheet file is the primary input or output.",
            "If the user mentions a .pdf file, produce one with this skill.",
            "Creating startup hooks, e.g. setting up a repository for the web.",
            "Handles report generation, such as weekly summaries for a team.",
        ]
        for index, description in enumerate(variants):
            with self.subTest(description=description):
                name = f"variant-{index}"
                self.write(name, f"---\nname: {name}\ndescription: {description}\n---\n")
                fired = {f.rule for f in self.run_rules()[1] if f.skill == name}
                self.assertNotIn("LIST005", fired)

    def test_description_with_no_conditions_still_flagged(self):
        """The counterpart: a description naming only a capability must flag."""
        self.write(
            "capability-only",
            "---\nname: capability-only\ndescription: Import a memory export from "
            "another AI assistant into Claude's memory, additively and safely.\n---\n",
        )
        self.assertIn("LIST005", self.rules_fired())


class InvocationRules(RuleCase):
    def test_unreachable_skill(self):
        self.write(
            "dead",
            CLEAN.format(name="dead").replace(
                "---\n\nBody.",
                "disable-model-invocation: true\nuser-invocable: false\n---\n\nBody.",
            ),
        )
        fired = self.rules_fired()
        self.assertIn("INVOKE001", fired)
        # INVOKE002 must not also fire; the stronger finding supersedes it.
        self.assertNotIn("INVOKE002", fired)

    def test_model_invocation_disabled_is_a_note(self):
        content = CLEAN.format(name="manual").replace(
            "---\n\nBody.", "disable-model-invocation: true\n---\n\nBody."
        )
        self.write("manual", content)
        _, findings = self.run_rules()
        notes = [f for f in findings if f.rule == "INVOKE002"]
        self.assertEqual(len(notes), 1)
        self.assertIs(notes[0].severity, Severity.NOTE)

    def test_paths_matching_nothing(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "app.ts").write_text("x", encoding="utf-8")
        content = CLEAN.format(name="scoped").replace(
            "---\n\nBody.", "paths: src/nowhere/**/*.tsx\n---\n\nBody."
        )
        self.write("scoped", content)
        self.assertIn("INVOKE003", self.rules_fired())

    def test_paths_that_match_are_silent(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "app.ts").write_text("x", encoding="utf-8")
        content = CLEAN.format(name="scoped").replace(
            "---\n\nBody.", "paths: src/**/*.ts\n---\n\nBody."
        )
        self.write("scoped", content)
        self.assertNotIn("INVOKE003", self.rules_fired())

    def test_fork_only_fields_without_fork(self):
        content = CLEAN.format(name="forky").replace("---\n\nBody.", "agent: Explore\n---\n\nBody.")
        self.write("forky", content)
        self.assertIn("INVOKE004", self.rules_fired())

    def test_fork_fields_with_fork_are_silent(self):
        content = CLEAN.format(name="forky").replace(
            "---\n\nBody.", "context: fork\nagent: Explore\n---\n\nBody."
        )
        self.write("forky", content)
        self.assertNotIn("INVOKE004", self.rules_fired())

    def test_invalid_effort(self):
        content = CLEAN.format(name="effortful").replace(
            "---\n\nBody.", "effort: extreme\n---\n\nBody."
        )
        self.write("effortful", content)
        self.assertIn("INVOKE005", self.rules_fired())


class CollisionRules(RuleCase):
    def test_names_that_fold_together_collide(self):
        self.write("deploy-app", CLEAN.format(name="deploy-app"))
        self.write("Deploy—App", CLEAN.format(name="Deploy—App"))
        _, findings = self.run_rules()
        collisions = [f for f in findings if f.rule == "COLLIDE001"]
        self.assertEqual(len(collisions), 1)
        self.assertIs(collisions[0].severity, Severity.ERROR)

    def test_distinct_names_do_not_collide(self):
        self.write("deploy", CLEAN.format(name="deploy"))
        self.write("release", CLEAN.format(name="release"))
        self.assertNotIn("COLLIDE001", self.rules_fired())

    def test_lookalike_name_is_reported(self):
        self.write("review", CLEAN.format(name="review"))
        self.write("rеview", CLEAN.format(name="rеview"))  # Cyrillic IE
        _, findings = self.run_rules()
        hits = [f for f in findings if f.rule == "COLLIDE002"]
        self.assertEqual(len(hits), 1)
        self.assertIs(hits[0].severity, Severity.ERROR)

    def test_description_overlap(self):
        shared = (
            "description: Use when the user asks to stage files, commit changes, "
            "write a commit message, or review the diff in the git repository.\n"
        )
        self.write("git-helper", f"---\nname: git-helper\n{shared}---\n\nBody.\n")
        self.write("git-assistant", f"---\nname: git-assistant\n{shared}---\n\nBody.\n")
        self.assertIn("COLLIDE005", self.rules_fired())

    def test_unrelated_descriptions_do_not_overlap(self):
        self.write(
            "sql",
            "---\nname: sql\ndescription: Use when the user asks to format a database migration file.\n---\n\nBody.\n",
        )
        self.write(
            "css",
            "---\nname: css\ndescription: Use when the user asks about stylesheet layout or typography tokens.\n---\n\nBody.\n",
        )
        self.assertNotIn("COLLIDE005", self.rules_fired())

    def test_manual_only_skills_are_excluded_from_overlap(self):
        shared = (
            "description: Use when the user asks to stage files, commit changes, "
            "write a commit message, or review the diff in the git repository.\n"
        )
        self.write("git-helper", f"---\nname: git-helper\n{shared}---\n\nBody.\n")
        self.write(
            "git-assistant",
            f"---\nname: git-assistant\n{shared}disable-model-invocation: true\n---\n\nBody.\n",
        )
        # Routing cannot pick a skill Claude may not invoke, so there is no clash.
        self.assertNotIn("COLLIDE005", self.rules_fired())


class PortabilityRules(RuleCase):
    def test_version_field_has_no_effect(self):
        content = CLEAN.format(name="versioned").replace(
            "---\n\nBody.", "version: 1.0.0\n---\n\nBody."
        )
        self.write("versioned", content)
        _, findings = self.run_rules()
        hits = [f for f in findings if f.rule == "PORT001"]
        self.assertEqual(len(hits), 1)
        self.assertIn("version", hits[0].message)

    def test_spec_target_rejects_claude_code_fields(self):
        content = CLEAN.format(name="scoped").replace(
            "---\n\nBody.", "paths: src/**\ndisable-model-invocation: true\n---\n\nBody."
        )
        self.write("scoped", content)
        self.assertNotIn("PORT002", self.rules_fired(target="claude-code"))
        self.assertIn("PORT002", self.rules_fired(target="spec"))

    def test_metadata_must_be_a_map(self):
        content = CLEAN.format(name="meta").replace(
            "---\n\nBody.", "metadata: just-a-string\n---\n\nBody."
        )
        self.write("meta", content)
        self.assertIn("PORT004", self.rules_fired())

    def test_metadata_reusing_a_field_name(self):
        content = CLEAN.format(name="meta").replace(
            "---\n\nBody.", "metadata:\n  paths: src\n---\n\nBody."
        )
        self.write("meta", content)
        self.assertIn("PORT004", self.rules_fired())

    def test_compatibility_length_limit(self):
        content = CLEAN.format(name="compat").replace(
            "---\n\nBody.", f"compatibility: {'x' * 600}\n---\n\nBody."
        )
        self.write("compat", content)
        self.assertIn("PORT003", self.rules_fired())


class Shadowing(unittest.TestCase):
    """Personal skills beat project skills - the counterintuitive direction."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_personal_shadows_project(self):
        from whyskill.discover import _load

        project_dir = self.root / ".claude" / "skills" / "deploy"
        project_dir.mkdir(parents=True)
        project_path = project_dir / "SKILL.md"
        project_path.write_text(CLEAN.format(name="deploy"), encoding="utf-8")

        personal_dir = self.root / "home" / "skills" / "deploy"
        personal_dir.mkdir(parents=True)
        personal_path = personal_dir / "SKILL.md"
        personal_path.write_text(CLEAN.format(name="deploy"), encoding="utf-8")

        skills = [
            _load(project_path, Source.PROJECT, "deploy"),
            _load(personal_path, Source.PERSONAL, "deploy"),
        ]
        findings = run_all(skills, Context(project=self.root))
        collisions = [f for f in findings if f.rule == "COLLIDE001"]
        self.assertEqual(len(collisions), 1)
        # The project skill is the one that loses.
        self.assertEqual(collisions[0].path, project_path)
        self.assertIn("personal", collisions[0].message)
        self.assertIn("opposite", collisions[0].mechanic)


class GlobMatching(unittest.TestCase):
    def test_double_star_spans_directories(self):
        pattern = glob_to_regex("src/**/*.ts")
        self.assertTrue(pattern.match("src/a/b/c.ts"))
        self.assertTrue(pattern.match("src/a.ts"))
        self.assertFalse(pattern.match("lib/a.ts"))

    def test_single_star_stops_at_a_slash(self):
        pattern = glob_to_regex("src/*.ts")
        self.assertTrue(pattern.match("src/a.ts"))
        self.assertFalse(pattern.match("src/a/b.ts"))

    def test_bare_directory_matches_contents(self):
        pattern = glob_to_regex("docs")
        self.assertTrue(pattern.match("docs"))
        self.assertTrue(pattern.match("docs/guide.md"))
        self.assertFalse(pattern.match("website/docs"))

    def test_leading_dot_slash_is_ignored(self):
        self.assertTrue(glob_to_regex("./src/*.ts").match("src/a.ts"))


if __name__ == "__main__":
    unittest.main()
