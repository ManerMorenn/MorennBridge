# SPDX-License-Identifier: GPL-3.0-or-later
import os

import bpy


class MORENN_OT_log_clear(bpy.types.Operator):
    bl_idname = "morenn.log_clear"
    bl_label = "Clear Log"

    def execute(self, context):
        context.scene.morenn.log.clear()
        context.scene.morenn.log_index = 0
        return {"FINISHED"}


class MORENN_OT_log_copy(bpy.types.Operator):
    """Copy the log to the clipboard"""

    bl_idname = "morenn.log_copy"
    bl_label = "Copy Log"

    def execute(self, context):
        lines = [
            f"{e.time} {e.level}: {e.message}" + (f" [{e.path}]" if e.path else "") for e in context.scene.morenn.log
        ]
        context.window_manager.clipboard = "\n".join(lines)
        self.report({"INFO"}, f"{len(lines)} log line(s) copied")
        return {"FINISHED"}


class MORENN_OT_log_open(bpy.types.Operator):
    """Open the folder of the selected log entry"""

    bl_idname = "morenn.log_open"
    bl_label = "Open Folder"

    @classmethod
    def poll(cls, context):
        s = context.scene.morenn
        return 0 <= s.log_index < len(s.log) and bool(s.log[s.log_index].path)

    def execute(self, context):
        s = context.scene.morenn
        path = bpy.path.abspath(s.log[s.log_index].path)
        if not os.path.isdir(path):
            path = os.path.dirname(path)
        bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


classes = (MORENN_OT_log_clear, MORENN_OT_log_copy, MORENN_OT_log_open)
