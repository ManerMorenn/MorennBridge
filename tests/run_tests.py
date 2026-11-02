# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless test runner.

    blender -b --factory-startup --python tests/run_tests.py -- [-k pattern] [-v]

The add-on is loaded as an extension through a symlink in the ``user_default`` repository when possible,
otherwise it is imported directly from the source tree. Set ``MORENN_COVERAGE_LIB`` to a folder holding the
``coverage`` package to measure coverage; the threshold comes from ``pyproject.toml``.
"""

import importlib.util
import os
import pathlib
import sys
import unittest

import bpy

ADDON_DIR = pathlib.Path(__file__).resolve().parent.parent
ADDON_NAME = "morennbridge"
EXT_MODULE = f"bl_ext.user_default.{ADDON_NAME}"


def _link_into_user_repo():
    ext_root = pathlib.Path(bpy.utils.user_resource("EXTENSIONS", path="user_default", create=True))
    link = ext_root / ADDON_NAME
    if link.is_symlink() or link.exists():
        try:
            if link.resolve() == ADDON_DIR:
                return link
        except OSError:
            pass
        if link.is_symlink():
            link.unlink()
        else:
            return None  # a real directory we must not touch
    link.symlink_to(ADDON_DIR, target_is_directory=True)
    return link


def load_addon():
    """Return the loaded add-on module (extension if possible, source tree otherwise)."""
    try:
        if _link_into_user_repo() is not None:
            if hasattr(bpy.ops, "extensions") and hasattr(bpy.ops.extensions, "repo_refresh_all"):
                bpy.ops.extensions.repo_refresh_all()
            bpy.ops.preferences.addon_enable(module=EXT_MODULE)
            module = sys.modules.get(EXT_MODULE)
            if module is not None and EXT_MODULE in bpy.context.preferences.addons:
                print(f"[tests] loaded as extension {EXT_MODULE}")
                return module
    except Exception as exc:
        print(f"[tests] extension load failed ({exc!r}); importing from source tree")
    spec = importlib.util.spec_from_file_location(
        ADDON_NAME, ADDON_DIR / "__init__.py", submodule_search_locations=[str(ADDON_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    print(f"[tests] loaded from source as {ADDON_NAME}")
    return module


def start_coverage():
    library = os.environ.get("MORENN_COVERAGE_LIB")
    if not library:
        return None
    sys.path.insert(0, library)
    import coverage

    measure = coverage.Coverage(config_file=str(ADDON_DIR / "pyproject.toml"), source=[str(ADDON_DIR)])
    measure.start()
    return measure


def finish_coverage(measure):
    """Print the report. Returns False when coverage is below the configured threshold."""
    measure.stop()
    measure.save()
    total = measure.report()
    return total >= measure.get_option("report:fail_under")


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    pattern = None
    verbosity = 1
    i = 0
    while i < len(argv):
        if argv[i] == "-k" and i + 1 < len(argv):
            pattern = argv[i + 1]
            i += 1
        elif argv[i] == "-v":
            verbosity = 2
        i += 1

    measure = start_coverage()
    addon = load_addon()
    os.environ["MORENN_ADDON_MODULE"] = addon.__name__

    sys.path.insert(0, str(ADDON_DIR))
    loader = unittest.TestLoader()
    suite = loader.discover(str(ADDON_DIR / "tests"), pattern="test_*.py", top_level_dir=str(ADDON_DIR))
    if pattern:

        def _filter(s):
            out = unittest.TestSuite()
            for t in s:
                if isinstance(t, unittest.TestSuite):
                    out.addTest(_filter(t))
                elif pattern in t.id():
                    out.addTest(t)
            return out

        suite = _filter(suite)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    passed = result.wasSuccessful()
    if measure is not None:
        passed = finish_coverage(measure) and passed
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
