# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import IntProperty

from ..core import classify, validate
from ..prefs import get_prefs


def selected_groups(context, mode="AUTO"):
    from .export_ops import selected_objects

    prefs = get_prefs(context)
    return classify.build_groups(selected_objects(context), prefs, context.scene, mode=mode)


class MORENN_OT_validate(bpy.types.Operator):
    """Check the selection for problems that would break the Unreal import"""

    bl_idname = "morenn.validate"
    bl_label = "Validate Selection"

    def execute(self, context):
        prefs = get_prefs(context)
        groups = selected_groups(context)
        issues = validate.run(context, groups, prefs)
        validate.store(context.scene, issues)
        n_err = sum(1 for i in issues if i.severity == "ERROR")
        n_warn = sum(1 for i in issues if i.severity == "WARNING")
        level = "ERROR" if n_err else ("WARNING" if n_warn else "INFO")
        self.report({level}, f"Validation: {len(groups)} asset(s), {n_err} error(s), {n_warn} warning(s)")
        return {"FINISHED"}


class MORENN_OT_fix_issue(bpy.types.Operator):
    """Apply the automatic fix of the selected issue"""

    bl_idname = "morenn.fix_issue"
    bl_label = "Fix"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.morenn
        index = self.index if self.index >= 0 else s.issues_index
        if not (0 <= index < len(s.issues)):
            return {"CANCELLED"}
        item = s.issues[index]
        entry = validate.FIXERS.get(item.fix_id)
        if entry is None:
            self.report({"WARNING"}, "No automatic fix for this issue")
            return {"CANCELLED"}
        issue = validate.Issue(item.code, item.severity, item.message, item.object_name, item.bone_name, item.fix_id)
        ok = entry[1](context, issue)
        self.report({"INFO" if ok else "WARNING"}, f"{entry[0]}: {'done' if ok else 'nothing changed'}")
        bpy.ops.morenn.validate()
        return {"FINISHED"}


class MORENN_OT_fix_all(bpy.types.Operator):
    """Apply every available automatic fix"""

    bl_idname = "morenn.fix_all"
    bl_label = "Fix All"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.morenn
        done = 0
        for item in list(s.issues):
            entry = validate.FIXERS.get(item.fix_id)
            if entry is None:
                continue
            issue = validate.Issue(
                item.code, item.severity, item.message, item.object_name, item.bone_name, item.fix_id
            )
            if entry[1](context, issue):
                done += 1
        bpy.ops.morenn.validate()
        self.report({"INFO"}, f"{done} fix(es) applied")
        return {"FINISHED"}


class MORENN_OT_select_offenders(bpy.types.Operator):
    """Select the objects referenced by the current issues"""

    bl_idname = "morenn.select_offenders"
    bl_label = "Select Offenders"

    def execute(self, context):
        names = {i.object_name for i in context.scene.morenn.issues if i.object_name}
        for obj in context.view_layer.objects:
            obj.select_set(obj.name in names)
        return {"FINISHED"}


classes = (MORENN_OT_validate, MORENN_OT_fix_issue, MORENN_OT_fix_all, MORENN_OT_select_offenders)
