# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from ..core import scene_setup, units
from ..core.log import Report
from ..prefs import get_prefs


class MORENN_OT_scene_setup(bpy.types.Operator):
    """Configure units (1 unit = 1 cm), frame rate and viewport clip/grid for Unreal"""

    bl_idname = "morenn.scene_setup"
    bl_label = "Set Up Scene for Unreal"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        report = Report("Scene setup")
        scene_setup.apply(context, get_prefs(context), report)
        report.flush(context.scene, self)
        return {"FINISHED"}


class MORENN_OT_scene_convert_units(bpy.types.Operator):
    """Scale every length in the scene so that it keeps its physical size in centimetre units, then run the scene setup"""

    bl_idname = "morenn.scene_convert_units"
    bl_label = "Convert Scene to Unreal Units"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        f = units.unit_factor(context.scene)
        if abs(f - 1.0) < 1e-9:
            return self.execute(context)
        return context.window_manager.invoke_confirm(
            self, event, title="Convert scene", message=f"All lengths will be multiplied by {f:g}. Continue?"
        )

    def execute(self, context):
        report = Report("Convert units")
        units.convert_scene_to_cm(context.scene, report)
        scene_setup.apply(context, get_prefs(context), report)
        report.flush(context.scene, self)
        return {"FINISHED"}


classes = (MORENN_OT_scene_setup, MORENN_OT_scene_convert_units)
