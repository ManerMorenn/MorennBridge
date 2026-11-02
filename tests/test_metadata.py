# SPDX-License-Identifier: GPL-3.0-or-later
"""Facts that live in several files and must not drift apart."""

import pathlib
import re
import tomllib
import unittest

from tests import fixtures

ROOT = pathlib.Path(__file__).resolve().parent.parent
ISSUE_CODE = re.compile(r"\b[EWI]0\d\d\b")


class MetadataTests(unittest.TestCase):
    def setUp(self):
        with open(ROOT / "blender_manifest.toml", "rb") as fh:
            self.manifest = tomllib.load(fh)

    def test_version_matches_the_manifest(self):
        self.assertEqual(fixtures.addon().__version__, self.manifest["version"])

    def test_changelog_has_an_entry_for_the_version(self):
        self.assertIn(f"## {self.manifest['version']}", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_every_issue_code_is_documented(self):
        used = set()
        for folder in ("core", "ops", "ui"):
            for path in (ROOT / folder).glob("*.py"):
                used.update(ISSUE_CODE.findall(path.read_text(encoding="utf-8")))
        documented = set(ISSUE_CODE.findall((ROOT / "docs" / "ISSUES.md").read_text(encoding="utf-8")))
        self.assertEqual(used - documented, set())
        self.assertEqual(documented - used, set())

    def test_bundled_fbx_modules_are_used(self):
        self.assertIn("_vendor", fixtures.addon_module("fbxpatch.tree").parse_fbx.__file__)


if __name__ == "__main__":
    unittest.main()
