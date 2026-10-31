# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless test runner.

    /Applications/Blender.app/Contents/MacOS/Blender -b --python morennbridge/tests/run_tests.py -- [-k pattern] [-v]

The add-on is loaded as an extension through a symlink in the ``user_default`` repository when possible,
otherwise it is imported directly from the source tree.
"""

import importlib
import os
import pathlib
import sys
import unittest

import bpy

ADDON_DIR = pathlib.Path(__file__).resolve().parent.parent
ADDON_NAME = ADDON_DIR.name
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
    sys.path.insert(0, str(ADDON_DIR.parent))
    module = importlib.import_module(ADDON_NAME)
    module.register()
    print(f"[tests] loaded from source as {ADDON_NAME}")
    return module


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
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
