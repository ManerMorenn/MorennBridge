# SPDX-License-Identifier: GPL-3.0-or-later
"""Check an extension installed from the built zip: it loads, exports and imports from a read-only folder.

blender -b --python dev/ci/smoke_installed.py
"""

import os
import stat
import sys
import tempfile

import bpy

MODULE = "bl_ext.user_default.morennbridge"


def fail(message):
    print(f"FAILED: {message}")
    sys.exit(1)


def make_read_only(root):
    for folder, _subfolders, files in os.walk(root):
        os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)
        for name in files:
            os.chmod(os.path.join(folder, name), stat.S_IRUSR)


def make_writable(root):
    for folder, _subfolders, _files in os.walk(root):
        os.chmod(folder, stat.S_IRWXU)


def main():
    if MODULE not in bpy.context.preferences.addons:
        bpy.ops.preferences.addon_enable(module=MODULE)
    addon = sys.modules.get(MODULE)
    if addon is None:
        fail("the extension did not load")
    root = os.path.dirname(addon.__file__)
    if os.path.islink(root):
        fail("the extension is a link, not an installed copy")
    for path in ("ue/scripts/mb_ue_import.py", "docs/UE_SETUP.md", "fbxpatch/_vendor/parse_fbx.py"):
        if not os.path.exists(os.path.join(root, path)):
            fail(f"{path} is missing from the zip")
    for path in ("tests", "dev", "docs/images"):
        if os.path.exists(os.path.join(root, path)):
            fail(f"{path} should not be in the zip")
    make_read_only(root)
    try:
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        out = tempfile.mkdtemp()
        bpy.context.scene.morenn.export_dir = out
        bpy.ops.morenn.scene_setup()
        if "FINISHED" not in bpy.ops.morenn.export_ue():
            fail("export was cancelled")
        path = os.path.join(out, "SM_Cube.fbx")
        if not os.path.exists(path):
            fail("the FBX was not written")
        if "FINISHED" not in bpy.ops.morenn.import_ue(filepath=path):
            fail("import was cancelled")
    finally:
        make_writable(root)
    print("Installed extension works")


if __name__ == "__main__":
    main()
