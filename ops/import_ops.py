# SPDX-License-Identifier: GPL-3.0-or-later
import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..core import import_fbx
from ..core.log import Report
from ..prefs import get_prefs


class MORENN_OT_import_ue(bpy.types.Operator, ImportHelper):
    """Import FBX files exported from Unreal (bones re-oriented, root bone restored, hierarchy tidied)"""

    bl_idname = "morenn.import_ue"
    bl_label = "Import from Unreal (FBX)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    convert_axes: BoolProperty(
        name="Convert bone axes",
        default=True,
        description="Re-orient bones so Blender's bone Y axis follows the Unreal bone X axis",
    )
    tidy: BoolProperty(
        name="Tidy hierarchy",
        default=True,
        description="Name the armature/mesh after the asset, parent collisions, resolve LOD groups",
    )
    reset_pose: BoolProperty(name="Reset pose", default=True)

    def execute(self, context):
        prefs = get_prefs(context)
        report = Report("Import")
        paths = []
        if self.files and self.directory:
            paths = [os.path.join(self.directory, f.name) for f in self.files if f.name]
        if not paths and self.filepath:
            paths = [self.filepath]
        imported = 0
        for path in paths:
            try:
                res = import_fbx.import_file(
                    context,
                    path,
                    prefs,
                    report,
                    convert_axes=self.convert_axes,
                    tidy=self.tidy,
                    reset_pose=self.reset_pose,
                )
                imported += 1
                report.info(
                    f"Imported {os.path.basename(path)}: {len(res.objects)} object(s), {len(res.actions)} action(s)",
                    path,
                )
            except Exception as exc:
                report.error(f"Import of '{os.path.basename(path)}' failed: {exc!r}")
        report.title = f"Import: {imported}/{len(paths)} file(s)"
        report.flush(context.scene, self)
        return {"FINISHED"} if imported else {"CANCELLED"}


classes = (MORENN_OT_import_ue,)
