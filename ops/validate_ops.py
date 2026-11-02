# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import IntProperty

from ..core import classify, validate
from ..prefs import get_prefs


def selected_groups(context, mode="AUTO"):
    from .export_ops import selected_objects

    prefs = get_prefs(context)
    split = classify.split_selection(selected_objects(context), context.view_layer, context.evaluated_depsgraph_get())
    return classify.build_groups(split.objects, prefs, context.scene, mode=mode)


def _issue_from_item(item):
    return validate.Issue(item.code, item.severity, item.message, item.object_name, item.bone_name, item.fix_id)


def _revalidate():
    try:
        bpy.ops.morenn.validate()
    except RuntimeError:  # raised when the remaining issues include errors; the list is updated regardless
        pass


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
        changed, message = validate.apply_fix(context, _issue_from_item(s.issues[index]))
        self.report({"INFO" if changed else "WARNING"}, message)
        _revalidate()
        return {"FINISHED"}


class MORENN_OT_fix_all(bpy.types.Operator):
    """Apply every available automatic fix"""

    bl_idname = "morenn.fix_all"
    bl_label = "Fix All"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        done = 0
        failures = []
        for item in list(context.scene.morenn.issues):
            if not item.fix_id:
                continue
            changed, message = validate.apply_fix(context, _issue_from_item(item))
            done += bool(changed)
            if "failed" in message:
                failures.append(message)
        _revalidate()
        if failures:
            self.report({"WARNING"}, f"{done} fix(es) applied, {len(failures)} failed: {failures[0]}")
        else:
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
